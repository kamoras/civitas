"""Alabama's own election-night-reporting site (www2.alabamavotes.gov), a
single-state deployment covering ONLY the 2026-08-11 special primary — not
Alabama's whole federal slate, and not by choice.

(A state that opts in with `statewide_offices` ALSO gets its statewide
executive nominees, read from a different Secretary of State publication:
the official primary and runoff precinct results. See "State offices"
below; nothing about the federal reading changes.)

WHY JUST THE SPECIAL PRIMARY: following Louisiana v. Callais (2026-04-29)
and a Alabama Legislature special session, Governor Ivey ordered four of
Alabama's seven US House districts (1, 2, 6, 7) redrawn and re-run under a
new map, decided in a single-round SPECIAL primary on 2026-08-11 with NO
runoff (confirmed reporting: "There will be no runoff election"). The other
three districts (3, 4, 5) and the US Senate seat were decided earlier under
the ORDINARY May 19 primary / June 16 runoff cycle, whose results this
module does NOT read — that data exists (the state parties published real
per-county Excel workbooks — GOP: sos.alabama.gov/sites/default/files/
05-29-2026/GOP%20Results.xlsx; Democratic primary and runoff equivalents
under /election-2026/), but every STATE-CERTIFIED nominee document for that
cycle (the two-column Office/Name PDFs read by the Alabama Republican and
Democratic Parties' own letters to the Secretary of State) is a flat
SCANNED image with no text layer at all (confirmed: pdftotext, pdffonts and
pdfimages against six of these certification PDFs all show zero embedded
fonts, only JPEG/JBIG2 raster pages) — unreadable without OCR, which this
codebase does not build. And the one piece that WOULD close the gap without
OCR — the June 16 Republican Senate runoff's own vote count — has no
published machine-readable file anywhere findable (the Democratic runoff's
Excel exists publicly; the Republican Party's equivalent apparently was
only ever shared with the Secretary of State's office directly, per its own
certification letter: "the documents found in the shared Dropbox folder").
So CD3, CD4, CD5 and the Senate seat stay on the ordinary FEC-filer
fallback rather than being wrongly guessed from certification news
coverage — this module reads only what has a genuine machine-readable
source: the four redrawn districts.

ecode=1001300 is the special primary's own results-page id on Alabama's
ASP.NET election-night system, verified live 2026-09-03 (weeks after the
election) to still be a stable, permanent reference — Alabama does not
appear to recycle an id the way New Mexico's single "always current" URL
does. ponytail: this id is NOT rediscovered each run (no listing/API
endpoint or dated results-announcement page was found that names it
programmatically, and state_source_crawler.py's own landing-page probes
only ever match a downloadable file extension, never an ASP.NET query-
string results page like this one); the upgrade path for 2028+ is finding
one, or hand-verifying and updating this id when the next redistricting-
driven special primary (or any future AL special congressional primary)
occurs. Kept in state_candidate_sources.json's "AL" entry rather than
hardcoded here, so that update is a config edit, not a code change.

Because that id has no cycle of its own baked into the URL, this module
refuses to serve it for any cycle but the one it was verified against —
`YEAR` below — rather than silently re-confirming 2026's winners against a
LATER cycle's real FEC candidates on nothing but a surname match (Jerry
Carl and Gary Palmer, among real 2026 winners here, are exactly the kind
of repeat incumbent who could otherwise coincidentally "confirm" a false
positive in 2028).

Verified live 2026-09-03 against the real, certified-by-count special
primary: Jerry Carl (CD1 R, 74.70%), Rhett Marques (CD2 R, 50.03%), Maurice
Mercer (CD6 D, 64.17%), Gary Palmer (CD6 R, 86.98%), Ammie Akin (CD7 R,
73.37%) — no CD1/CD2/CD7 Democratic primary is shown on this page at all,
meaning Democrats fielded no candidate in those three redrawn districts.
"""

import asyncio
import html
import io
import logging
import re
import zipfile
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx
import xlrd

from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_text_with_retry, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import (
    clean_display_name,
    federal_record,
    normalize_party,
    parse_office,
    parse_statewide_office,
    pick_nominee,
    surname,
)
from app.pipeline.fetch.state_candidates_tabular import _votes
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# The only cycle ecode=1001300 (see below) is verified to mean — see the
# module docstring for why an off-cycle call refuses rather than reuses it.
YEAR = 2026

_HEADERS = BROWSER_HEADERS
_rate_limiter = RateLimiter(rps=1.0)


class _ContestResultsParser(HTMLParser):
    """Alabama's own results page nests one table per contest, headed by a
    td.enrContestHeader ("UNITED STATES REPRESENTATIVE, 1ST CONGRESSIONAL
    DISTRICT (REP)"), then one row per candidate whose name/party sit in a
    td.enrCandNameCol ("Jerry Carl                             (REP)") and
    whose vote count sits in a td.enrCandVoteNumCol. Alternating rows carry
    an extra "enrAlt" class prefix (plain zebra striping), so matching is on
    the class SUFFIX, not the exact class string — but a plain "CandNameCol"
    substring also matches the (different) column-labels row above the real
    candidates ("enrCandidatesHeader enrCandNameCol", holding only &nbsp;),
    so `CandidateListItemCol` — present only on a real candidate row's own
    class, singular "Candidate" — is required too, or that label row would
    be captured as a same-named "candidate" with an empty name.

    Capture is entered only when nothing is already being captured, and
    exited only by that SAME td's own close: a matching-class td can only
    ever directly hold text (never a candidate/header td nested inside
    another one on this page), so once inside a capture, a nested td
    starttag only tracks a depth counter rather than hijacking the buffer
    or ending the capture on ITS close instead of the outer td's.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.contests: dict[str, list[tuple[str, int]]] = {}
        self._contest: str | None = None
        self._capture: str | None = None
        self._capture_depth = 0
        self._buf: list[str] = []
        self._pending_name: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "td":
            return
        if self._capture:
            self._capture_depth += 1
            return
        cls = dict(attrs).get("class") or ""
        if "enrContestHeader" in cls:
            self._capture = "header"
        elif "CandidateListItemCol" in cls and "CandNameCol" in cls:
            self._capture = "name"
        elif "CandidateListItemCol" in cls and "CandVoteNumCol" in cls:
            self._capture = "votes"
        else:
            return
        self._capture_depth = 1
        self._buf = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "td" or not self._capture:
            return
        self._capture_depth -= 1
        if self._capture_depth > 0:
            return
        text = "".join(self._buf).strip()
        if self._capture == "header":
            self._contest = text
            self.contests.setdefault(text, [])
            # A stray name with no votes row after it (page truncated
            # mid-fetch, a still-tabulating precinct) must not survive
            # into the NEXT contest and get attributed to its first
            # unrelated vote count.
            self._pending_name = None
        elif self._capture == "name":
            self._pending_name = text
        elif self._capture == "votes" and self._contest and self._pending_name:
            self.contests[self._contest].append((self._pending_name, _votes(text)))
            self._pending_name = None
        self._capture = None


# ── State offices: the official precinct results ─────────────────────
#
# The special primary above only ever carried four congressional
# districts. Alabama's statewide executive offices were decided in the
# ORDINARY May primary and June runoff, and the Secretary of State
# publishes both as official precinct results on its election-data page:
# one zip per election ("2026_Primary_Election.zip",
# "2026_PRIMARY_RUNOFF_ELECTION.zip"), holding one legacy .xls workbook
# per county. Each sheet has three label columns -- Contest Title, Party,
# Candidate -- and then one column per precinct (plus ABSENTEE and
# PROVISIONAL), so a candidate's statewide total is the sum of every
# numeric cell in their row across all 67 counties. Summed that way the
# 2026 Republican primary gives Thomas (Tommy) Tuberville 422,255 votes for
# Governor and Jim Zeigler 194,062 for PSC Place 2, both exactly the
# Alabama Republican Party's own certified workbook's figures.
#
# Alabama nominates by MAJORITY (runoff_threshold_pct 50 in the config):
# a primary leader below it is withheld and the runoff decides. A runoff
# that has not been published yet decides nothing, so that office is
# withheld rather than handed to the primary leader.
#
# Only STATEWIDE offices are read from these files. Their federal
# contests are the pre-redistricting ones for four districts (voided by
# the special primary above), and their legislative contests include
# State Senate districts 25 and 26, also redrawn and re-run; reading
# either would publish a nominee for a contest that no longer exists.

_LABEL_COLUMNS = ("Contest Title", "Party", "Candidate")


def _workbook_rows(payload: bytes) -> list[tuple[str, str, str, int]]:
    """(contest, party, candidate, votes) for every row of one county's
    precinct workbook, votes summed across its precinct columns. Empty
    for a workbook without the three label columns."""
    book = xlrd.open_workbook(file_contents=payload)
    sheet = book.sheet_by_index(0)
    if sheet.nrows == 0:
        return []
    header = [str(v).strip() for v in sheet.row_values(0)]
    try:
        cols = [header.index(name) for name in _LABEL_COLUMNS]
    except ValueError:
        return []
    rows = []
    for r in range(1, sheet.nrows):
        values = sheet.row_values(r)
        contest, party, candidate = (" ".join(str(values[c]).split()) for c in cols)
        votes = sum(
            v for i, v in enumerate(values)
            if i not in cols and isinstance(v, float)
        )
        rows.append((contest, party, candidate, int(votes)))
    return rows


def contest_totals(archive: bytes, exclude: set[str]) -> dict[tuple[str, str], dict[str, int]]:
    """{(contest, party): {candidate: statewide votes}} over every county
    workbook in one election's zip."""
    totals: dict[tuple[str, str], dict[str, int]] = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        for member in zf.namelist():
            if not member.lower().endswith(".xls"):
                continue
            for contest, party, candidate, votes in _workbook_rows(zf.read(member)):
                if not contest or not candidate or candidate in exclude:
                    continue
                seat = totals.setdefault((contest, party), {})
                seat[candidate] = seat.get(candidate, 0) + votes
    return totals


def resolve_statewide(
    primary: dict[tuple[str, str], dict[str, int]],
    runoff: dict[tuple[str, str], dict[str, int]] | None,
    runoff_threshold_pct: float | None,
) -> list[dict]:
    """One record per party per statewide contest: the primary's majority
    winner, or else the runoff's winner -- who must have been on that
    primary's ballot. Nothing for a contest whose runoff is not
    published, or that no one can be named for safely (a tie)."""
    records = []
    for (contest, party_text), choices in primary.items():
        statewide = parse_statewide_office(contest)
        party = normalize_party(party_text)
        if statewide is None or party is None:
            continue
        won = pick_nominee(list(choices.items()), runoff_threshold_pct=runoff_threshold_pct)
        if won is None and runoff is not None:
            second = runoff.get((contest, party_text)) or {}
            won = pick_nominee(list(second.items()), runoff_threshold_pct=None)
            if won is not None and won[0] not in choices:
                won = None
        name = clean_display_name(won[0]) if won else ""
        if name:
            records.append({"office": statewide[0], "district": statewide[1], "party": party, "last_name": name})
    return records


def _runoff_owed(primary: dict[tuple[str, str], dict[str, int]], runoff_threshold_pct: float | None) -> bool:
    """Whether any statewide primary contest's leader fell short of the
    majority, so that its nominee is decided by a runoff."""
    return any(
        parse_statewide_office(contest) is not None and normalize_party(party) is not None
        and pick_nominee(list(choices.items()), runoff_threshold_pct=runoff_threshold_pct) is None
        for (contest, party), choices in primary.items()
    )


def _one_link(page: str, pattern: str | None, year: int) -> str | None:
    if not pattern:
        return None
    found = {html.unescape(m.group(1)) for m in re.finditer(pattern.replace("{year}", str(year)), page)}
    return found.pop() if len(found) == 1 else None


async def _statewide_nominees(client: httpx.AsyncClient, year: int, spec: dict) -> list[dict] | None:
    """Every statewide nominee the official primary and runoff precinct
    results name, or None when they cannot be read in full.

    None, never [], for results not posted yet: the caller records the
    state as checked, so an empty list would publish "no statewide
    offices on this ballot" for a state that simply has not counted.
    The same holds while a runoff is owed but not posted -- the list
    would silently lack every office still being decided."""
    page_url = spec.get("page_url")
    if not page_url:
        return None
    page = await fetch_text_with_retry(client, _rate_limiter, page_url, f"AL election data {year}")
    if page is None:
        return None
    primary_link = _one_link(page, spec.get("primary_link_regex"), year)
    if primary_link is None:
        logger.info("AL: no single %d primary precinct-results file linked yet", year)
        return None
    runoff_link = _one_link(page, spec.get("runoff_link_regex"), year)
    exclude = set(spec.get("exclude_choices") or [])

    async def _totals(link: str, label: str) -> dict | None:
        resp = await fetch_with_retry(
            client, _rate_limiter, "GET", urljoin(page_url, link), timeout=120.0,
            log_label=f"AL {label} precinct results {year}", headers=_HEADERS,
        )
        if resp is None:
            return None
        try:
            return await asyncio.to_thread(contest_totals, resp.content, exclude)
        except Exception:  # noqa: BLE001 - a corrupt zip or workbook is a skip, not a crash
            logger.warning("AL %s precinct results for %d were not a readable zip of workbooks", label, year)
            return None

    primary = await _totals(primary_link, "primary")
    if primary is None:
        return None
    threshold = spec.get("runoff_threshold_pct")
    runoff = None
    if runoff_link is not None:
        runoff = await _totals(runoff_link, "runoff")
        if runoff is None:
            return None
    elif _runoff_owed(primary, threshold):
        logger.info("AL: %d statewide runoff results are owed but not posted yet", year)
        return None
    return resolve_statewide(primary, runoff, threshold)


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state unused, this strategy is AL-only by construction
) -> list[dict] | None:
    statewide: list[dict] = []
    if source.get("statewide_offices"):
        found = await _statewide_nominees(client, year, source.get("state_office_results") or {})
        if found is None:
            # The statewide section must not claim a partial reading.
            return None
        statewide = found

    if year != YEAR:
        # See module docstring — ecode=1001300 names one specific 2026
        # election with no date of its own; reusing it for any other
        # cycle would confirm that cycle's candidates off a stale surname
        # match rather than that cycle's real result.
        return statewide

    results_url = (
        "https://www2.alabamavotes.gov/electionNight/statewideResultsByContest.aspx"
        f"?ecode={source.get('ecode')}"
    )
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", results_url, timeout=30.0,
        log_label=f"AL special primary results {year}", headers=_HEADERS,
    )
    if resp is None:
        return None

    parser = _ContestResultsParser()
    try:
        parser.feed(resp.text)
    except Exception:  # noqa: BLE001 - a malformed page is a skip, not a crash
        logger.warning("AL special primary results page was not parsable HTML")
        return None
    if not parser.contests:
        logger.warning("No contests found on AL special primary results page")
        return None

    results: list[dict] = []
    for contest, choices in parser.contests.items():
        office_district = parse_office(contest)
        if office_district is None:
            continue
        office, district = office_district
        # Alabama runs separate per-party ballots, not a top-two contest,
        # so every candidate under one contest header shares one party —
        # taken from the header (same source parse_office already reads),
        # not re-derived per candidate: normalize_party on a full "Name
        # (PARTY)" cell is the only place in this codebase that runs it
        # against a name rather than a party-only column, and a name that
        # happened to contain a party word would misfile that candidate
        # into a fabricated second party for an otherwise single-party
        # contest.
        party = normalize_party(contest)
        if party is None:
            continue
        choices_by_name = [(name, votes) for name, votes in choices if surname(name)]
        won = pick_nominee(choices_by_name, runoff_threshold_pct=None)
        record = federal_record(office, district, party, won[0]) if won else None
        if record:
            results.append(record)

    if not results:
        logger.warning("AL special primary results yielded no confirmed nominees")
        return None
    return results + statewide
