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

THREE DISCOVERY MODES, because the file's URL must never be hardcoded to
one cycle's date:

  s3_listing      — list an S3 bucket prefix and pick the matching key. North
                    Carolina publishes to dl.ncsbe.gov this way, one folder
                    per election date (ENRS/2026_03_03/).
  direct_url      — a stable URL template with {year} substituted, for states
                    that keep one predictable path per cycle.
  sos_api_report  — the Enhanced Voting results portal's own three-hop API
                    (GA, WA, VA); see _sos_api_report_urls.

Results are aggregated across precinct rows: these exports are one row per
precinct per choice, so a candidate's real total is the SUM over every row
naming them, never a single row's value.
"""

import csv
import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from urllib.parse import quote

import httpx

from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.fetch.state_candidates_common import (
    normalize_party, parse_office, pick_nominees, surname,
)
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Civitas civic-transparency-platform contact@civitas-research.org"}
_rate_limiter = RateLimiter(rps=1.0)

# A state's whole-election export is a few MB; anything far past that is a
# sign the configured URL now points at something else entirely (a full
# voter file, an error page served as an attachment), which should fail
# loudly rather than be parsed into nonsense or held in memory.
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024

_KEY_RE = re.compile(r"<Key>([^<]+)</Key>")


async def _get(client: httpx.AsyncClient, url: str, label: str) -> httpx.Response | None:
    return await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=120.0,
        log_label=label, headers=_HEADERS,
    )


async def _sos_api_report_urls(
    client: httpx.AsyncClient, state: str, year: int, discovery: dict,
) -> list[str]:
    """Three-hop discovery for a Secretary-of-State results portal that
    publishes its data as report blobs behind a JSON API (Georgia's, live
    2026-08-15): the jurisdiction document lists every election and the
    portal's own id; the election document names its report blobs; the
    blob itself is the workbook.

    Nothing here is hardcoded to a cycle — the election is matched by its
    `electionDate` YEAR plus a name pattern, and the blob filename (which
    carries a fresh GUID every publish) is read from the API each run.

    Returns the primary first and, when the state ran one, its runoff
    second, so the caller can let the runoff override.
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
    # ponytail: every non-runoff stage still gets the runoff threshold only
    # if it's first (see fetch_confirmed_candidates); a state that ever
    # combines same-day primaries WITH a runoff needs that to become a
    # per-stage flag.
    patterns = discovery.get("election_name_regex") or []
    if isinstance(patterns, str):
        patterns = [patterns]
    stages = [_match(p) for p in patterns]
    runoff = _match(discovery.get("runoff_name_regex"))
    if runoff:
        stages.append(runoff)

    urls = []
    for election_id in [s for s in stages if s]:
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
        # This portal publishes running counts the night of the election and
        # flips isOfficialResults only at certification. A slow-counting
        # state can still be tallying for two weeks afterwards, and a
        # confirmed nominee derived from a count that is still moving is
        # exactly the kind of wrong this whole module exists to avoid — so
        # an uncertified election yields nothing and simply lights up on
        # its own once the state certifies.
        if discovery.get("require_official") and not payload.get("isOfficialResults"):
            logger.info(
                "%s election %s is not certified yet — not confirming nominees from it",
                state, election_id,
            )
            continue
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
            urls.append(
                (discovery.get("cdn_url") or "").format(
                    jurisdiction_id=jurisdiction_id, blob=quote(blob),
                )
            )
    return urls


async def _discover_urls(
    client: httpx.AsyncClient, state: str, year: int, discovery: dict,
) -> list[str]:
    """This cycle's results file(s), never a hardcoded per-cycle path.

    More than one is returned only where a state's nominees genuinely need
    more than one election to determine: a runoff state's second contest
    decides every race its primary left short of the threshold, so it is
    ordered last and overrides.
    """
    mode = discovery.get("mode")

    if mode == "sos_api_report":
        return await _sos_api_report_urls(client, state, year, discovery)

    if mode == "direct_url":
        template = discovery.get("url") or ""
        return [template.format(year=year)] if template else []

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
        return [f"{bucket}/{sorted(keys)[0]}"]

    logger.error("Unknown results discovery mode %r for %s", mode, state)
    return []


_XL_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _xlsx_rows(payload: bytes) -> list[dict] | None:
    """Rows from a .xlsx workbook's first sheet, read with the standard
    library alone — an xlsx IS a zip of XML, so this needs no Excel
    dependency for the several states (CA among them) that publish results
    only in that format.

    Values come from the shared-string table when the cell says so
    (t="s"), otherwise inline. Anything else (formulas, rich text beyond
    its text runs) yields an empty cell rather than raising, on the same
    principle as _text elsewhere: a shape change should cost a field, not
    the whole download.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
        shared = [
            "".join(t.text or "" for t in si.iter(f"{_XL_NS}t"))
            for si in ET.fromstring(archive.read("xl/sharedStrings.xml"))
        ]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        logger.warning("Results download was not a readable xlsx workbook")
        return None

    def cell(c) -> str:
        v = c.find(f"{_XL_NS}v")
        if v is None or v.text is None:
            return ""
        if c.get("t") == "s":
            try:
                return shared[int(v.text)]
            except (ValueError, IndexError):
                return ""
        return v.text

    rows = [[cell(c) for c in row.iter(f"{_XL_NS}c")] for row in sheet.iter(f"{_XL_NS}row")]
    if not rows:
        return None
    header = rows[0]
    return [dict(zip(header, r)) for r in rows[1:]]


def _rows(payload: bytes, fmt: dict) -> list[dict] | None:
    """Delimited rows from the download, transparently unzipping a
    single-file archive or reading an xlsx workbook. None when the payload
    isn't what was configured."""
    encoding = fmt.get("encoding") or "utf-8"
    delimiter = fmt.get("delimiter") or ","

    if fmt.get("format") == "xlsx":
        return _xlsx_rows(payload)

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

    return list(csv.DictReader(io.StringIO(text), delimiter=delimiter))


def _votes(raw: str) -> int:
    """Vote counts arrive with thousands separators in some exports; a
    value that isn't a number contributes nothing rather than raising."""
    try:
        return int(re.sub(r"[,\s]", "", raw or "") or 0)
    except ValueError:
        return 0


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
    # Some states name the office in a way that is only unambiguous
    # alongside another column: Virginia's federal label is "Member, House
    # of Representatives (10th District)" — no "U.S.", which parse_office
    # deliberately refuses so a state lower chamber can't slip through, and
    # an ordinal parse_office doesn't read. Where the export carries its own
    # district-type column, that is the discriminator the label lacks, so
    # the contest is keyed by a canonical label instead. Any party in the
    # original label is lost by that rewrite, so a state configured this way
    # must also carry a party_column (Virginia does).
    type_col = fmt.get("district_type_column")
    district_col = fmt.get("district_column")

    tally: dict[str, dict] = defaultdict(
        lambda: {"votes": defaultdict(int), "party": {}},
    )
    for row in rows:
        contest = (row.get(contest_col) or "").strip()
        if type_col and (row.get(type_col) or "").strip().casefold() == "congressional":
            digits = re.search(r"\d+", row.get(district_col) or "")
            contest = f"U.S. House District {int(digits.group())}" if digits else "U.S. House"
        choice = (row.get(choice_col) or "").strip()
        if not contest or not choice or choice.casefold() in excluded:
            continue
        entry = tally[contest]
        entry["votes"][choice] += _votes(row.get(votes_col) or "")
        if party_col and choice not in entry["party"]:
            entry["party"][choice] = (row.get(party_col) or "").strip()
    return tally


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    """Every confirmed federal nominee `state` produced for `year`, or None
    on a fetch/parse failure or when the cycle's results aren't published
    yet — the tri-state None-vs-[] discipline used throughout this codebase.

    Each item: {"office", "district", "party", "last_name"}, matched against
    Civitas's FEC-derived Candidate rows by state_candidates.py, not here.
    """
    st = state.upper()
    threshold = source.get("runoff_threshold_pct")
    advance_count = int(source.get("advance_count") or 1)
    fmt = source.get("format") or {}

    urls = await _discover_urls(client, st, year, source.get("discovery") or {})
    if not urls:
        logger.warning("No %d results file discoverable for %s — skipping", year, st)
        return None

    # Keyed by seat-and-party so a runoff replaces whatever its primary
    # said about the same race. The primary is the only stage the runoff
    # threshold applies to: a runoff is decisive by construction, its
    # winner having beaten the only other candidate left.
    by_seat: dict[tuple, list[dict]] = {}
    parsed_any = False

    for index, url in enumerate(urls):
        resp = await _get(client, url, f"{st} results export")
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
        parsed_any = True
        _collect(rows, fmt, by_seat, threshold if index == 0 else None, advance_count)

    if not parsed_any:
        return None
    return [record for records in by_seat.values() for record in records]


def _collect(
    rows: list[dict],
    fmt: dict,
    by_seat: dict[tuple, list[dict]],
    threshold: float | None,
    advance_count: int,
) -> None:
    """Fold one results file into `by_seat`, replacing (not appending to)
    any seat it covers so a later stage's answer wins outright."""
    for contest, entry in _tally(rows, fmt).items():
        parsed = parse_office(contest)
        if parsed is None:
            continue
        office, district = parsed
        # A party-primary label carries its party ("US HOUSE OF
        # REPRESENTATIVES DISTRICT 01 (REP)"); a top-two label doesn't,
        # so each candidate's own party column is the fallback.
        contest_party = normalize_party(contest)
        won = pick_nominees(list(entry["votes"].items()), threshold, advance_count)
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
                if advance_count == 1:
                    continue
                party = ""
            last_name = surname(name)
            if not last_name:
                continue
            records.append({
                "office": office, "district": district,
                "party": party, "last_name": last_name,
            })
        # Keyed by each record's OWN party, not the contest's. A runoff must
        # replace its primary's answer for the same seat-and-party, but two
        # party primaries held the same day (Virginia runs separate
        # Democratic and Republican elections) are different races that must
        # both survive — keying on the contest would let the second silently
        # erase the first.
        for record in records:
            by_seat.setdefault((office, district, record["party"]), []).clear()
        for record in records:
            by_seat[(office, district, record["party"])].append(record)
