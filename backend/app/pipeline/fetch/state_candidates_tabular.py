"""Bulk delimited-results strategy — one adapter for every state that
publishes its official results as a downloadable CSV/TSV (optionally
zipped), which is the most common shape after the ENR vendors.

Nothing here is state-specific: the file's location, its delimiter,
encoding and column names all come from the state's entry in
state_candidate_sources.json, so adding a state that publishes this way is
a config entry, not code. Only a state on a genuinely different vendor
needs a new module (Clarity -> state_candidates_clarity.py, Civix ->
state_candidates_tx.py).

Verified live against North Carolina's real 2026 primary on 2026-08-12:
`results_pct_20260303.zip` (1.4 MB) holds all 103,517 precinct rows for the
whole state, of which 49,884 are federal, resolving to 21 federal contests.
Ground-truthed on recognisable outcomes — Virginia Foxx taking the NC-05
Republican primary with 74.5%, Valerie Foushee NC-04 Democratic with 49.2%.

FOUR DISCOVERY MODES, because the file's URL must never be hardcoded to
one cycle's date:

  s3_listing      — list an S3 bucket prefix and pick the matching key. North
                    Carolina publishes to dl.ncsbe.gov this way, one folder
                    per election date (ENRS/2026_03_03/).
  direct_url      — a stable URL template with {year} substituted, for states
                    that keep one predictable path per cycle.
  sos_api_report  — the Enhanced Voting results portal's own three-hop API
                    (GA, WA, VA, UT); see _sos_api_report_urls.
  landing_page    — read this cycle's file off the page the state keeps
                    current (FL), for a state that publishes one dated file
                    per election and no way to list them.

Results are aggregated across precinct rows: these exports are one row per
precinct per choice, so a candidate's real total is the SUM over every row
naming them, never a single row's value.

STATE OFFICES ride the same download. Only about half of North Carolina's
103,517 precinct rows are federal; the rest are the state's own executive
and legislative contests, which were being read and discarded. A state
whose entry sets statewide_offices gets those too, through the same two
conservative gates state_candidates_enhanced_voting uses
(parse_statewide_office, parse_state_leg_office), and their names are
kept whole rather than reduced to a surname because there is no FEC row
to match them against.

Verified live against Minnesota's real 2026 primary export: 18 federal,
8 statewide (Keith Ellison AG, Steve Simon Secretary of State, a joint
"Governor & Lt Governor" ticket resolving to the top of the ticket, and
State Auditor) and 61 legislative records across all 32 legislative
contests the file actually contains. Its house districts are "10A"/"10B",
which is why a district identifier is a string. The county offices in
the same file -- "County Attorney", "County Auditor/Treasurer" -- are
refused despite containing the words the statewide gate keys on.

Note that opting a tabular state in may require widening its discovery
URL first: several entries deliberately fetch a federal-only export (New
Mexico's carries type=FED), which contains no state contests to read.

WHAT THE OTHER TABULAR STATES ACTUALLY HOLD, surveyed live 2026-09-21 by
running these gates over each state's own 2026 export. A count alone
never justifies opting a state in -- the flag asserts that its real
labels were checked -- so the findings are recorded here rather than
acted on blindly:

  GA   LIVE. 30 federal, 236 legislative seats -- exactly its 180 House
       + 56 Senate, which is the structural check -- and every one of
       its statewide contests: the eight constitutional offices plus
       both Public Service Commission seats, which are elected
       statewide but held by district (see StatewideNominee.district).
       Its county subdivisions are Census County Divisions and had to
       be swapped for incorporated places; three of its House districts
       lie entirely in unincorporated land and fall back to counties.
  ID   LIVE. "State Representative District N Seat A"/"Seat B" —
       two seats of ONE district, now kept apart by StateLegNominee.seat
       rather than colliding: 35 districts, 70 lower seats, which is
       Idaho's real House. All seven of its constitutional officers
       resolve; State Controller had to be added for the last of them.
  WA   LIVE, same shape via "Pos. N - Legislative District N": 49
       districts, 98 lower seats, its real House. No statewide offices,
       and that is a checked claim rather than an empty result — every
       unmatched non-judicial contest in its file is a local levy,
       proposition or PUD commissioner, because Washington elects its
       statewide officers in presidential years.
  NC   LIVE. Its House was being missed entirely: the label is "NC
       HOUSE OF REPRESENTATIVES DISTRICT 1", with no "State" in it, so
       only the Senate matched and the state looked like a 15-seat
       chamber. parse_state_leg_office now reads a BARE "House of
       Representatives" -- safe because it refuses anything parse_office
       recognises first, and a bare one carries no federal marker. 14
       Senate and 41 House seats, which is every contested primary its
       file holds. No statewide offices, and that is checked: every
       unmatched non-judicial contest is municipal, because North
       Carolina elects its Council of State in presidential years.
  UT   LIVE, once State Board of Education was added -- elected
       statewide, held by district, the same shape as Georgia's PSC. Its
       file carries only a handful of contests because only contested
       primaries appear.
  CA   LIVE. Its Assembly was being missed entirely ("State Assembly
       Member District N"), so the state looked like a 20-seat
       legislature; it is 100 this cycle (80 Assembly plus the 20 Senate
       seats up). It also prints its statewide officers BARE -- just
       "Treasurer", just "Controller" -- which the qualified patterns
       could not see. Note it is a top-two state, so two same-party
       nominees from one contest are correct, which is why display_name
       is part of both tables' keys.
  FL   LIVE, once Chief Financial Officer was added -- its fourth
       cabinet officer, and the only thing its file was missing.
  VA   No state contests at all, which is expected -- Virginia elects
       its legislature and statewide officers in ODD years -- but its
       export may simply be scoped, so "none" is not yet a verified
       claim.
  HI   Nothing discoverable for 2026 (its landing page yielded no file).
  IL   36 federal, no state contests in the file it publishes.
  MD   LIVE, and it brought the third multi-member shape with it. Not
       Idaho's "Seat A"/"Seat B" nor Washington's "Pos. 1"/"Pos. 2", but
       "House of Delegates District 10 ... Vote for up to 3" -- three
       members from ONE contest with no seat designator, so the number
       advancing is a property of the contest and is read from its own
       label (vote_for_count). Applied only where the state runs
       one-nominee party primaries: under top-two exactly two advance no
       matter how many seats are filled, so a seat count there would be
       the wrong question. Its 11A/11B subdistricts are separate
       geographies like Minnesota's, and Census names them the same way,
       so the crosswalk resolves them directly.
  NM   Its URL is federal-only (type=FED), so there is nothing to read.
  AK   No 2026 file discoverable from its landing page.
"""

import csv
import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from urllib.parse import quote, urljoin

import httpx

from app.election_calendar import next_election_day
from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import (
    clean_display_name,
    normalize_party,
    office_from_columns,
    parse_office,
    JUDICIAL_COURT_LABELS,
    parse_judicial_office,
    parse_state_leg_office,
    parse_statewide_office,
    pick_nominees,
    surname,
    vote_for_count,
)
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_HEADERS = BROWSER_HEADERS
_rate_limiter = RateLimiter(rps=1.0)

# A state's whole-election export is a few MB; anything far past that is a
# sign the configured URL now points at something else entirely (a full
# voter file, an error page served as an attachment), which should fail
# loudly rather than be parsed into nonsense or held in memory.
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024

# How long after an election to keep withholding when a state asks for the
# certification gate but names no deadline of its own. Utah proved a
# portal's official flag can simply never be flipped, so a gate with no
# failsafe is a gate that never opens — and a discovered source has no
# statutory deadline to quote. Every state certifies a primary well inside
# a month, and a nominee named a month late is still named months before
# the general.
DEFAULT_SETTLE_DAYS = 30

_KEY_RE = re.compile(r"<Key>([^<]+)</Key>")


async def _get(client: httpx.AsyncClient, url: str, label: str) -> httpx.Response | None:
    return await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=120.0,
        log_label=label, headers=_HEADERS,
    )


def _settled(held: str | None, settle_days: int | None) -> bool:
    """True once an election is far enough past that counting is over,
    whatever any official flag says.

    The failsafe under require_official, and it exists because that flag is
    not reliably flipped: Utah's 2026 primary was canvassed and certified in
    July — its own signed state canvass report is published on the same
    portal — and isOfficialResults was still false a month later. Without
    this, a state whose office never ticks that box would confirm nobody
    forever, silently, which looks exactly like working code.

    For a state that publishes no such flag at ALL (Florida's election-night
    file is just a file, and it starts filling with partial counts the
    moment polls close), this is the only gate there is — which is why it
    applies to every discovery mode, not just the portal one.

    The window is the state's certification deadline, from config, not a
    guess about how fast a count goes.
    """
    if not settle_days:
        return False
    try:
        held_on = date.fromisoformat(str(held or "")[:10])
    except ValueError:
        return False
    return (datetime.now(UTC).date() - held_on).days >= int(settle_days)


def _held_from_rows(rows: list[dict], fmt: dict) -> str | None:
    """The election's own date, read out of the results rows, for a file
    whose URL doesn't carry one. Florida stamps every row with
    "08/18/2026"; a state that stamps none simply stays undatable, and an
    undatable file can't clear the certification gate — refusing is the
    safe end of that trade."""
    column = fmt.get("held_column")
    if not column:
        return None
    for row in rows[:200]:
        raw = str(row.get(column) or "").strip()
        for pattern, order in ((r"(\d{4})-(\d{2})-(\d{2})", "ymd"),
                               (r"(\d{1,2})/(\d{1,2})/(\d{4})", "mdy")):
            m = re.match(pattern, raw)
            if not m:
                continue
            y, mo, d = (m.group(1), m.group(2), m.group(3)) if order == "ymd" \
                else (m.group(3), m.group(1), m.group(2))
            try:
                return date(int(y), int(mo), int(d)).isoformat()
            except ValueError:
                continue
    return None


def _withheld(stage: dict, discovery: dict) -> bool:
    """Whether this stage's results are still too unsettled to name a
    nominee from. One rule for every vendor: a state that asks for the gate
    (require_official) gets it, and passes only on its own certification
    flag or, failing that, its certification deadline.

    A stage carries whatever its source knows — `official` is None where the
    vendor publishes no such flag, `held` is None where nothing dates the
    file — and an unknown never counts as a yes.
    """
    if not discovery.get("require_official"):
        return False
    if stage.get("official"):
        return False
    if stage.get("held") is None and stage.get("official") is None:
        # Nothing yet says how fresh this is. Don't refuse it here — the
        # file itself may carry its election date, which is only readable
        # once downloaded (see _held_from_rows), and this same check runs
        # again with that answer before a single vote is counted.
        return False
    return not _settled(
        stage.get("held"), discovery.get("settle_days", DEFAULT_SETTLE_DAYS),
    )


def _stage(url: str | None, runoff: bool = False, held: str | None = None,
           official: bool | None = None) -> dict:
    """One results file to fold in, in the shape every discovery mode
    returns so the fetch below treats all states alike."""
    return {"url": url, "runoff": runoff, "held": held, "official": official}


def _date_in(text: str) -> str | None:
    """The YYYYMMDD an election file names itself with, as an ISO date —
    Florida's 20260818_ElecResultsFL.txt, North Carolina's ENRS/2026_03_03/.
    None when nothing in the string dates it."""
    m = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", text or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


async def _sos_api_report_urls(
    client: httpx.AsyncClient, state: str, year: int, discovery: dict,
) -> list[dict]:
    """Three-hop discovery for a Secretary-of-State results portal that
    publishes its data as report blobs behind a JSON API (Georgia's, live
    2026-08-15): the jurisdiction document lists every election and the
    portal's own id; the election document names its report blobs; the
    blob itself is the workbook.

    Nothing here is hardcoded to a cycle — the election is matched by its
    `electionDate` YEAR plus a name pattern, and the blob filename (which
    carries a fresh GUID every publish) is read from the API each run.

    Returns the primary first and, when the state ran one, its runoff
    second, so the caller can let the runoff override. Each stage carries
    the portal's own certification flag and election date; whether that is
    settled enough to name a nominee from is _withheld's single call for
    every vendor, not a decision taken here.
    """
    resp = await _get(
        client, discovery.get("jurisdiction_url") or "", f"{state} jurisdiction",
    )
    if resp is None:
        return []
    try:
        jurisdiction = resp.json() or {}
    except ValueError:
        return []

    jurisdiction_id = jurisdiction.get("id")
    elections = jurisdiction.get("elections") or []
    if not jurisdiction_id:
        return []

    def _name(entry: dict) -> str:
        return " ".join(n.get("text", "") for n in entry.get("name") or [])

    def _match(pattern: str | None) -> str | None:
        if not pattern:
            return None
        for entry in elections:
            if not str(entry.get("electionDate") or "").startswith(str(year)):
                continue
            if re.search(pattern, _name(entry), re.IGNORECASE):
                return entry.get("publicElectionId")
        return None

    # A list of patterns is a state that decides its nominees across more
    # than one election held the SAME day: Virginia runs its Democratic and
    # Republican primaries as two separate elections in this portal, and
    # both are needed. Kept as one-pattern-one-election rather than
    # letting a single pattern match many, so a loose regex can't silently
    # start pulling in a special election.
    patterns = discovery.get("election_name_regex") or []
    if isinstance(patterns, str):
        patterns = [patterns]
    stages = [(_match(p), False) for p in patterns]
    runoff = _match(discovery.get("runoff_name_regex"))
    if runoff:
        stages.append((runoff, True))

    found: list[dict] = []
    for election_id, is_runoff in [s for s in stages if s[0]]:
        resp = await _get(
            client,
            (discovery.get("election_url") or "").format(election_id=election_id),
            f"{state} election {election_id}",
        )
        if resp is None:
            continue
        try:
            payload = resp.json() or {}
        except ValueError:
            continue
        # This portal publishes running counts the night of the election
        # and flips isOfficialResults only at certification, so both facts
        # travel with the stage for _withheld to judge.
        wanted = discovery.get("report_name") or ""
        blob = next(
            (
                report.get("blobName")
                for category in payload.get("publicReportCategories") or []
                for report in category.get("reports") or []
                if report.get("reportName") == wanted and report.get("blobName")
            ),
            None,
        )
        if blob:
            found.append(_stage(
                (discovery.get("cdn_url") or "").format(
                    jurisdiction_id=jurisdiction_id, blob=quote(blob),
                ),
                runoff=is_runoff,
                held=str(payload.get("electionDate") or "")[:10] or None,
                official=bool(payload.get("isOfficialResults")),
            ))
    return found


async def _discover_urls(
    client: httpx.AsyncClient, state: str, year: int, discovery: dict,
) -> list[dict]:
    """This cycle's results stage(s), never a hardcoded per-cycle path.
    Every mode returns the SAME _stage shape, carrying whatever that source
    knows about how settled its results are, so the fetch below treats a
    state the same way whatever its vendor is.

    More than one stage comes back only where a state's nominees genuinely
    need more than one election to determine: a runoff state's second
    contest decides every race its primary left short of the threshold (so
    it is ordered last and overrides, and the threshold does not apply to
    it), and a state like Virginia holds a separate election per party on
    the same day.
    """
    mode = discovery.get("mode")

    def _calendar_date() -> str | None:
        """This state's primary date from the national calendar, for a
        file that doesn't date itself — without it such a file can never
        clear the certification gate, since an undatable result is one
        whose freshness is unknown."""
        if not discovery.get("date_from_calendar"):
            return None
        from app.pipeline.fetch.state_election_dates import primary_date

        return primary_date(state, year)

    if mode == "sos_api_report":
        return await _sos_api_report_urls(client, state, year, discovery)

    if mode == "direct_url":
        template = discovery.get("url") or ""
        if not template:
            return []
        url = template.replace("{year}", str(year))
        held = None
        if "{primary_date" in url:
            # The state's own primary date, from the national calendar
            # (state_election_dates) — which is what lets a state whose
            # file is addressed by election date be reached WITHOUT
            # crawling that state's site for the date first. Minnesota is
            # the case: its results files are wide open, while the page
            # that lists them sits behind a bot manager.
            from app.pipeline.fetch.state_election_dates import primary_date

            held = primary_date(state, year)
            if not held:
                logger.info(
                    "%s's results file is addressed by primary date and none is "
                    "known yet — skipping", state,
                )
                return []
            url = url.replace("{primary_date_compact}", held.replace("-", ""))
            url = url.replace("{primary_date}", held)
        # A stable URL that always serves whichever election is current
        # (New Mexico's) carries no date of its own either. Unlike
        # landing_page, where _calendar_date() is a last-resort fallback
        # behind the file's own dated link, this IS the only source of
        # `held` — but _settled() below still protects a future cycle:
        # until that cycle's real primary approaches, its calendar date is
        # in the future, so "now - held >= settle_days" stays false and
        # nothing gets confirmed from whatever this URL currently serves.
        return [_stage(url, held=held or _calendar_date())]

    if mode == "s3_listing":
        bucket = (discovery.get("bucket_url") or "").rstrip("/")
        prefix = (discovery.get("prefix") or "").format(year=year)
        pattern = discovery.get("file_regex")
        if not bucket or not pattern:
            return []
        resp = await _get(
            client, f"{bucket}/?list-type=2&prefix={prefix}", f"{state} results listing",
        )
        if resp is None:
            return []
        keys = [k for k in _KEY_RE.findall(resp.text) if re.search(pattern, k)]
        if not keys:
            return []
        # Earliest key wins: within a cycle the first matching election is
        # the primary that decides nominees. A later folder is the general,
        # whose results can't confirm a nominee for the race it IS.
        key = sorted(keys)[0]
        return [_stage(f"{bucket}/{key}", held=_date_in(key))]

    if mode == "landing_page":
        # For a state that publishes one dated file per election and gives
        # no way to list them: read the link off the page the state itself
        # keeps current (Florida's floridaelectionwatch.gov/Downloads), so
        # the cycle's date is never written down here.
        pattern = discovery.get("link_regex")
        # An optional FIRST hop, for a state whose results page is keyed by
        # an internal election id rather than anything guessable: read the
        # id off an index page and substitute it. The HIGHEST id wins,
        # which is the newest election — the same "never write the cycle
        # down" rule every other mode follows.
        index_url = discovery.get("index_url")
        election_id = None
        if index_url:
            index = await _get(client, index_url, f"{state} election index")
            if index is None:
                return []
            ids = [int(m) for m in re.findall(discovery.get("index_regex") or "", index.text)]
            if not ids:
                logger.warning("No election id found on %s's index page", state)
                return []
            election_id = str(max(ids))
        # {year} is substituted in the PAGE url as well as the link
        # pattern: a state that files its results under a per-cycle path
        # (Maryland's /elections/{year}/primary_results/) needs the page
        # itself templated, or nothing is ever found there again.
        page_url = (discovery.get("page_url") or "").replace("{year}", str(year))
        if election_id:
            page_url = page_url.replace("{election_id}", election_id)
        resp = await _get(client, page_url, f"{state} downloads page")
        if resp is None or not pattern:
            return []
        if not re.search(pattern.replace("{year}", str(year)), resp.text):
            # A page that answers 200 with none of its own links on it is
            # usually a bot-manager challenge rather than an empty page —
            # Minnesota's does this intermittently, and the retry
            # succeeds because the challenge has by then set its cookie.
            # One retry only: a genuinely empty page stays empty.
            logger.info("%s's results page had no links — retrying once", state)
            resp = await _get(client, page_url, f"{state} downloads page (retry)") or resp
        # Literal token, not str.format: a link regex is full of {n}
        # quantifiers that format() would try to fill in.
        #
        # Matches are resolved against the page they were found on, and
        # Windows-style separators are normalised — real state pages link
        # results with relative hrefs and, in Illinois' case, backslashes.
        links = sorted({
            urljoin(page_url, m.group(0).replace("\\", "/"))
            for m in re.finditer(pattern.replace("{year}", str(year)), resp.text)
        })
        # Same rule s3_listing applies, for a page that shows one election
        # at a time: a file dated on this cycle's federal election day IS
        # the general, and the general's results can't confirm a nominee
        # for the race they decide. Left in, this state would quietly start
        # "confirming" November's winners every four years. A file that
        # dates itself to another cycle entirely is somebody's archive.
        general = next_election_day(date(year, 1, 1)).isoformat()
        wanted = [
            ln for ln in links
            if _date_in(ln) is None
            or (_date_in(ln) != general and _date_in(ln).startswith(str(year)))
        ]
        if not wanted:
            logger.info(
                "%s's downloads page lists no %d primary results file yet", state, year,
            )
            return []
        # Several links are one election published per office, not several
        # elections: Illinois posts a separate CSV for every congressional
        # district. Where they ARE dated, the earliest is the primary.
        dated = [ln for ln in wanted if _date_in(ln)]
        if dated:
            earliest = min(_date_in(ln) for ln in dated)
            wanted = [ln for ln in wanted if _date_in(ln) == earliest]
        fallback = _calendar_date()
        return [_stage(ln, held=_date_in(ln) or fallback) for ln in wanted]

    logger.error("Unknown results discovery mode %r for %s", mode, state)
    return []


_XL_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _col_index(ref: str) -> int:
    """0-based column index from a cell reference like "D130" — "D" -> 3.
    Base-26, letters only (A=1 .. Z=26, AA=27 ...), 1-indexed then
    shifted down by one."""
    letters = ref.rstrip("0123456789")
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def _xlsx_rows(payload: bytes, skip: int = 0) -> list[dict] | None:
    """Rows from a .xlsx workbook's first sheet, read with the standard
    library alone — an xlsx IS a zip of XML, so this needs no Excel
    dependency for the several states (CA among them) that publish results
    only in that format.

    `skip` drops that many leading rows before the next one is treated as
    the header — the same role `_rows`' own `skip_lines` plays for a
    delimited-text download that opens with a banner before its real
    header (Hawaii's own "#FormatVersion 1"), extended here for a real
    xlsx shape that needed it: New Hampshire's own per-office exports
    open with two title/date rows before the real candidate-name header
    row.

    Values come from the shared-string table when the cell says so
    (t="s"), from the cell's own text runs for an inline string
    (t="inlineStr" — Delaware's candidate list uses nothing else, and has
    no shared-string table at all), otherwise the cell's value. Anything else (formulas, rich text beyond
    its text runs) yields an empty cell rather than raising, on the same
    principle as _text elsewhere: a shape change should cost a field, not
    the whole download.

    Placed by each cell's own reference (`r="D130"` -> column D), not by
    position among the `<c>` elements present in the row: a spreadsheet
    writer that omits a wholly-blank interior cell (real, live shape —
    Maine's town-by-town exports do this for any candidate/column with no
    value on a given row, e.g. a UOCAVA summary row's empty county field)
    would otherwise silently shift every following value one column to
    the left, misattributing a vote total to the wrong candidate with no
    error. A row is padded out to the header's own width so a value in a
    column the header never named is dropped, never misread as the next
    named column's value.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
        shared = [
            "".join(t.text or "" for t in si.iter(f"{_XL_NS}t"))
            for si in ET.fromstring(archive.read("xl/sharedStrings.xml"))
        ] if "xl/sharedStrings.xml" in archive.namelist() else []
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        logger.warning("Results download was not a readable xlsx workbook")
        return None

    def cell(c) -> str:
        if c.get("t") == "inlineStr":
            return "".join(t.text or "" for t in c.iter(f"{_XL_NS}t"))
        v = c.find(f"{_XL_NS}v")
        if v is None or v.text is None:
            return ""
        if c.get("t") == "s":
            try:
                return shared[int(v.text)]
            except (ValueError, IndexError):
                return ""
        return v.text

    def sparse_row(row) -> dict[int, str]:
        by_col = {}
        for c in row.iter(f"{_XL_NS}c"):
            ref = c.get("r")
            if ref is None:
                continue
            by_col[_col_index(ref)] = cell(c)
        return by_col

    sparse_rows = [sparse_row(row) for row in sheet.iter(f"{_XL_NS}row")]
    if skip:
        sparse_rows = sparse_rows[skip:]
    if not sparse_rows:
        return None
    width = max(sparse_rows[0], default=-1) + 1
    rows = [[r.get(i, "") for i in range(width)] for r in sparse_rows]
    header = rows[0]
    return [dict(zip(header, r)) for r in rows[1:]]


class _TableReader(HTMLParser):
    """Reads results published as HTML TABLES UNDER HEADINGS, which is how
    a good number of states publish and how Maryland publishes all eight
    of its congressional districts on one page.

    A table alone doesn't say which contest it is — the contest is the
    heading above it ("Representative in Congress", then "District 1") —
    so every row carries the heading stack in force where it appeared, as
    heading_1..heading_6. Config then names which of those make up the
    contest, exactly as it would name any other column.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict] = []
        self._headings: dict[int, str] = {}
        self._heading_level: int | None = None
        self._in_table = False
        self._cells: list[str] | None = None
        self._cell: list[str] | None = None
        self._header: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading_level = int(tag[1])
            self._headings[self._heading_level] = ""
        elif tag == "table":
            self._in_table, self._header = True, None
        elif tag == "tr" and self._in_table:
            self._cells = []
        elif tag in ("td", "th") and self._cells is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        elif self._heading_level is not None:
            self._headings[self._heading_level] += data

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            self._headings[level] = " ".join(self._headings.get(level, "").split())
            # A new heading replaces everything nested under it.
            for deeper in [k for k in self._headings if k > level]:
                del self._headings[deeper]
            self._heading_level = None
        elif tag in ("td", "th") and self._cell is not None:
            self._cells.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._cells is not None:
            if self._header is None:
                self._header = self._cells
            elif any(self._cells):
                row = dict(zip(self._header, self._cells))
                row.update({f"heading_{lvl}": text for lvl, text in self._headings.items()})
                self.rows.append(row)
            self._cells = None
        elif tag == "table":
            self._in_table, self._cells, self._header = False, None, None


def _html_rows(payload: bytes, fmt: dict) -> list[dict] | None:
    reader = _TableReader()
    try:
        reader.feed(payload.decode(fmt.get("encoding") or "utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - a malformed page is a skip, not a crash
        logger.warning("Results page was not parsable HTML")
        return None
    return reader.rows or None


def _rows(payload: bytes, fmt: dict) -> list[dict] | None:
    """Delimited rows from the download, transparently unzipping a
    single-file archive or reading an xlsx workbook. None when the payload
    isn't what was configured."""
    encoding = fmt.get("encoding") or "utf-8"
    delimiter = fmt.get("delimiter") or ","

    if fmt.get("format") == "xlsx":
        return _xlsx_rows(payload, skip=int(fmt.get("skip_lines") or 0))

    if fmt.get("format") == "html_table":
        return _html_rows(payload, fmt)

    if payload[:2] == b"PK":
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
        except zipfile.BadZipFile:
            logger.warning("Results download was not a readable zip")
            return None
        members = [n for n in archive.namelist() if not n.endswith("/")]
        member_re = fmt.get("member_regex")
        if member_re:
            members = [n for n in members if re.search(member_re, n)]
        if len(members) != 1:
            logger.warning("Expected one results member in archive, found %d", len(members))
            return None
        if archive.getinfo(members[0]).file_size > MAX_DOWNLOAD_BYTES:
            logger.warning("Results member %s exceeds the size ceiling", members[0])
            return None
        text = archive.read(members[0]).decode(encoding, errors="replace")
    else:
        text = payload.decode(encoding, errors="replace")

    # A state that publishes its results with NO header row (Minnesota's
    # semicolon file is one long list of positional fields) names its
    # columns in config instead. Everything downstream is unchanged: the
    # names it gives are the names the format keys refer to.
    # A file that opens with its own format banner before the header row
    # (Hawaii's "#FormatVersion 1") says how many lines to drop.
    skip = int(fmt.get("skip_lines") or 0)
    if skip:
        text = "\n".join(text.splitlines()[skip:])
    columns = fmt.get("columns")
    return list(csv.DictReader(
        io.StringIO(text), delimiter=delimiter, fieldnames=columns or None,
    ))


def _votes(raw: str) -> int:
    """Vote counts arrive with thousands separators in some exports; a
    value that isn't a number contributes nothing rather than raising."""
    try:
        return int(re.sub(r"[,\s]", "", raw or "") or 0)
    except ValueError:
        return 0


def _cell(row: dict, spec) -> str:
    """One configured field's value. A LIST of columns is joined, for an
    export that splits across columns what others keep in one: Florida
    names a contest by race AND party code, and a candidate by first name
    AND last name, so joining is what gives the shared parsing the single
    label and the single display name it works on everywhere else."""
    columns = spec if isinstance(spec, list) else [spec]
    return " ".join(
        (row.get(c) or "").strip() for c in columns if (row.get(c) or "").strip()
    )


def _tally(rows: list[dict], fmt: dict) -> dict[str, dict]:
    """Sum votes per (contest, choice) across every precinct/county row,
    keeping each CHOICE's own party. Party is per-candidate rather than
    per-contest because a top-two state's contest isn't party-scoped at all
    — "United States Representative District 10" holds every party's
    candidates in one race."""
    contest_col = fmt.get("contest_column") or "Contest Name"
    choice_col = fmt.get("choice_column") or "Choice"
    party_col = fmt.get("party_column")
    votes_col = fmt.get("votes_column") or "Total Votes"
    # Several exports append a per-contest summary row that sits in the
    # same shape as a candidate ("Total Votes" in Georgia's workbook, with
    # an empty choice id and party). Counting it would both double the
    # denominator and, in an uncontested race, win the contest outright.
    excluded = {c.casefold() for c in (fmt.get("exclude_choices") or [])}
    # Where the label alone can't identify the office, the row's own
    # columns do (see office_from_columns). Resolving it here, per row,
    # keeps ONE notion of "which office is this contest" for every state:
    # the label, unless the state's config names the columns that say so.
    office_spec = fmt.get("house_from_columns")

    tally: dict[str, dict] = defaultdict(
        lambda: {"votes": defaultdict(int), "party": {}, "office": None},
    )
    for row in rows:
        contest = _cell(row, contest_col)
        choice = _cell(row, choice_col)
        if not contest or not choice or choice.casefold() in excluded:
            continue
        entry = tally[contest]
        entry["votes"][choice] += _votes(row.get(votes_col) or "")
        if party_col and choice not in entry["party"]:
            entry["party"][choice] = (row.get(party_col) or "").strip()
        if entry["office"] is None:
            entry["office"] = office_from_columns(row, office_spec)
    return tally


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    """Every confirmed federal nominee `state` produced for `year`, or None
    on a fetch/parse failure — the tri-state None-vs-[] discipline used
    throughout this codebase.

    [] is the meaningful middle: the state's results were reached and are
    simply not confirmable yet (nothing certified). Returning None for that
    would report a perfectly healthy state as a failed fetch every run, for
    the weeks between its election and its certification.

    Each item: {"office", "district", "party", "last_name"}, matched against
    Civitas's FEC-derived Candidate rows by state_candidates.py, not here.
    """
    st = state.upper()
    threshold = source.get("runoff_threshold_pct")
    advance_count = int(source.get("advance_count") or 1)
    fmt = source.get("format") or {}

    discovery = source.get("discovery") or {}
    stages = await _discover_urls(client, st, year, discovery)
    if not stages:
        logger.warning("No %d results file discoverable for %s — skipping", year, st)
        return None
    usable = [s for s in stages if s.get("url") and not _withheld(s, discovery)]
    if not usable:
        logger.info(
            "%s has %d %d election(s) published but none settled enough to name "
            "nominees from — confirming nobody",
            st, len(stages), year,
        )
        return []

    # Keyed by seat-and-party so a runoff replaces whatever its primary
    # said about the same race. The runoff threshold applies to primary
    # stages only: a runoff is decisive by construction, its winner having
    # beaten the only other candidate left.
    by_seat: dict[tuple, list[dict]] = {}
    parsed_any = False
    withheld_any = False

    for stage in usable:
        resp = await _get(client, stage["url"], f"{st} results export")
        if resp is None:
            return None
        payload = resp.content
        if len(payload) > MAX_DOWNLOAD_BYTES:
            logger.warning("Results download for %s exceeds the size ceiling", st)
            return None

        rows = _rows(payload, fmt)
        if not rows:
            logger.warning("No parsable rows in the results export for %s", st)
            return None
        if stage.get("held") is None:
            dated = {**stage, "held": _held_from_rows(rows, fmt)}
            if _withheld(dated, discovery) or (
                discovery.get("require_official") and dated["held"] is None
            ):
                logger.info(
                    "%s: results at %s are not settled enough to name nominees from",
                    st, stage["url"],
                )
                withheld_any = True
                continue
        parsed_any = True
        _collect(
            rows, fmt, by_seat,
            None if stage["runoff"] else threshold,
            advance_count,
            state_offices=bool(source.get("statewide_offices")),
            judicial_resolution=source.get("judicial_resolution"),
            judicial_advance_count=source.get("judicial_advance_count"),
        )

    if not parsed_any:
        # Withheld is healthy and empty; nothing parsed at all is a
        # failure. Same distinction the discovery gate makes.
        return [] if withheld_any else None
    return [record for records in by_seat.values() for record in records]


def _collect(
    rows: list[dict],
    fmt: dict,
    by_seat: dict[tuple, list[dict]],
    threshold: float | None,
    advance_count: int,
    state_offices: bool = False,
    judicial_resolution: str | None = None,
    judicial_advance_count: int | None = None,
) -> None:
    """Fold one results file into `by_seat`, replacing (not appending to)
    any seat it covers so a later stage's answer wins outright."""
    for contest, entry in _tally(rows, fmt).items():
        seat = None
        parsed = entry["office"] or parse_office(contest)
        if parsed is not None:
            office, district = parsed
            federal = True
        elif not state_offices:
            continue
        else:
            # The same export carries this state's own executive offices
            # and legislative seats — North Carolina's is 103,517 precinct
            # rows of which only 49,884 are federal. They are read through
            # the same two conservative gates the Enhanced Voting adapter
            # uses, and only for a state whose entry opts in, because a
            # count of zero is only a true claim once somebody has checked
            # that state's real labels against them.
            federal = False
            statewide = parse_statewide_office(contest)
            if statewide is not None:
                office, district = statewide
            else:
                parsed_seat = parse_state_leg_office(contest)
                if parsed_seat is not None:
                    office, district, seat = parsed_seat
                else:
                    # The fourth gate, tried last so it can never take a
                    # label one of the other three answers. Gated on its
                    # own judicial_offices opt-in downstream, because a
                    # judgeship parsing cleanly says nothing about
                    # whether that state's judicial primaries NOMINATE or
                    # ELECT — see parse_judicial_office's docstring.
                    parsed_judicial = parse_judicial_office(contest)
                    if parsed_judicial is None:
                        continue
                    office, district, seat = parsed_judicial
        # A party-primary label carries its party ("US HOUSE OF
        # REPRESENTATIVES DISTRICT 01 (REP)"); a top-two label doesn't,
        # so each candidate's own party column is the fallback.
        contest_party = normalize_party(contest)
        # A district that fills several seats from one contest sends
        # several nominees. The count is read from the contest's own
        # label ("Vote for up to 3") and only where the state runs
        # ONE-NOMINEE party primaries: under top-two the number
        # advancing is two regardless of how many seats are being
        # filled, so a seat count there would be the wrong question.
        # JUDICIAL contests can advance a different number from the
        # party primaries in the SAME file. Florida sends one nominee per
        # party primary but runs its judgeships as non-partisan top-two,
        # so reading them at the state's advance_count silently dropped
        # every one: a "NOP" party normalises to None, and the
        # one-nominee branch below skips an unattributable contest by
        # design. Defaults to the state's own count, so no other state
        # changes.
        is_judicial = office in JUDICIAL_COURT_LABELS
        effective_advance = (
            judicial_advance_count if is_judicial and judicial_advance_count
            else advance_count
        )
        seats_filled = vote_for_count(contest) if effective_advance == 1 else None
        # Scoped to JUDICIAL contests, never the whole file: in
        # Washington the same export carries ordinary top-two races that
        # advance two whatever the leader's share, alongside judicial
        # ones where a majority leaves a single name on the general
        # ballot (RCW 29A.36.170). Applying it state-wide would drop real
        # candidates from every other contest.
        majority_rule = judicial_resolution if is_judicial else None
        won = pick_nominees(
            list(entry["votes"].items()), threshold,
            seats_filled or effective_advance,
            judicial_resolution=majority_rule,
        )
        if not won:
            continue

        records = []
        for name, _pct in won:
            party = contest_party or normalize_party(entry["party"].get(name, ""))
            if party is None:
                # In a one-nominee party primary an unattributable contest
                # is a label we don't understand, so it's skipped. Under
                # top-two the party is incidental — an independent or
                # no-party-preference candidate really can advance — so
                # they're kept, and the matcher falls back to surname.
                if effective_advance == 1:
                    continue
                party = ""
            # A federal nominee is matched against an FEC row, which
            # files surnames. A state-office nominee has no FEC row to
            # match or to render from, so the printed name is kept whole
            # with only the ballot annotations stripped — the same split
            # state_candidates_enhanced_voting makes.
            last_name = (
                surname(name, last_first=fmt.get("name_format") == "last_first")
                if federal else clean_display_name(name)
            )
            if not last_name:
                continue
            record = {
                "office": office, "district": district,
                "party": party, "last_name": last_name,
            }
            if federal:
                # Carried ONLY so the shared matcher can break a tie it
                # would otherwise refuse: two candidates sharing a
                # surname AND a party in one race. Alaska's 2026 Senate
                # top-four really does advance two of them -- "Sullivan,
                # Dan S." (the sitting senator, 68,726 votes) and
                # "Sullivan, Daniel J. Jr." (4,107) -- against FEC's own
                # "SULLIVAN, DAN" and "SULLIVAN, DANIEL J". Surname plus
                # party cannot separate those, so both went unmatched and
                # the state's marquee race published two Democrats.
                # Never used to REJECT a match the surname already
                # resolved uniquely; see _match_candidate.
                record["display_name"] = name

            if seat is not None:
                record["seat"] = seat
            records.append(record)
        # Keyed by each record's OWN party, not the contest's. A runoff must
        # replace its primary's answer for the same seat-and-party, but two
        # party primaries held the same day (Virginia runs separate
        # Democratic and Republican elections) are different races that must
        # both survive — keying on the contest would let the second silently
        # erase the first.
        for record in records:
            by_seat.setdefault((office, district, seat, record["party"]), []).clear()
        for record in records:
            by_seat[(office, district, seat, record["party"])].append(record)
