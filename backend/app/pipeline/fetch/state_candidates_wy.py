"""Wyoming's own official post-canvass Excel export (sos.wyo.gov) — a
single-state deployment, so this is a vendor module in the same sense
state_candidates_pa.py/state_candidates_in.py are, not a per-state fetcher
(see state_candidates.py for why that distinction is the whole design).

ONE download, State-Canvassing-Board-certified: sos.wyo.gov's own 2026
primary results page links a zip
("Docs/{year}/Results/Primary/{year}_Wyoming_Primary_Results.zip") holding
three Excel workbooks — this module reads the one whose member name
contains "Results Summaries" (matched by a pattern, not the exact
"2026 Primary Results Summaries - OFFICIAL.xlsx" filename, since that
name is year-prefixed and could shift slightly cycle to cycle). Unlike
this system's live ENR vendors (TotalVote, Clarity, ...), this file is
never published until the State Canvassing Board has certified — its own
page names that board and its "Certification" minutes right next to this
download — so there is no separate "unofficial/preliminary" state to
guard against the way settle_days does elsewhere; the file's mere
existence at this URL, for the requested year, IS the certification
signal. settle_days is still read from config as a defensive floor
(matching every other module in this system), not the primary gate.

The workbook's "Statewide Candidates" sheet (found by NAME via
workbook.xml -> its _rels, never a hardcoded "sheetN.xml" — sheet ORDER
in this workbook is not sheet 1, it is fourth) is a WIDE cross-tab, the
opposite shape of every other tabular state registered so far: a race
name and party appear ONCE per candidate-group, in the group's FIRST
column only (blank in every other column of that group) across two
header rows, a third header row names every candidate/Write-Ins/
Overvotes/Undervotes column, and each COUNTY gets its own row below with
vote counts — plus a final "Total" row this module reads directly rather
than summing 23 counties itself (verified live: the Total row's own
values already match hand-summed county totals). Getting one candidate's
statewide vote count means forward-filling the sparse race/party header
cells leftward across their group before reading the Total row's value
in that same column. Like the sheet-by-name lookup above, the header
rows themselves are found by CONTENT (the "Write-Ins" column every real
group carries) rather than a fixed row index, and the Total row lookup
requires there be exactly one match — a row inserted above the header
block, or a second row ever also labelled "Total", fails closed (None,
a real fetch_failed) instead of silently reading the wrong row.

Wyoming's real 2026 field also carries a "* Withdrawn\\nCandidate" column
in the House Republican group (2,162 votes still counted under that
placeholder after the real candidate withdrew) — excluded the same way
Write-Ins/Overvotes/Undervotes already must be, via a name-text match
rather than position, since nothing else marks this column as special.

Wyoming nominates by plurality — no evidence of a federal-primary runoff
in state law was found in this research pass (contrast South Dakota's
entry, which found a real "(Run-Off required...)" marker but only on its
Governor race) — so runoff_threshold_pct is null.

A known, accepted gap: state_candidates_common.surname() takes the
trailing whitespace-separated token of a display name, which is wrong
for a compound surname with a lowercase connector ("Elena\\nDel Real" ->
"Real", not "Del Real"). Not fixed here — a pre-existing shared-helper
limitation, not something introduced by this module — and it happens not
to change any real 2026 outcome (that candidate lost her primary by a
wide margin regardless of which surname she's stored under).

Verified live 2026-09-08 against the real, certified 2026 primary
(workbook internally dated 2026-08-26, 8 days after the August 18
primary; "Total" row cross-checked against a hand-sum of all 23
counties' own rows for the Senate race): Harriet Hageman (Senate R,
real plurality winner of a 5-way field, 83,807 of ~129,000 R votes —
WY's sitting at-large US Representative moving up to run for Senate),
James Byrd (Senate D, real plurality winner of a 2-way field, 9,591 over
2,499), Chuck Gray (House R, real plurality winner of a crowded 9-way
field opened by Hageman's Senate run, 31,224 over runner-up Steve
Friess's 25,059), Lisa Kinney (House D, real plurality winner of a
2-way field, 9,344 over 2,660).
"""

import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime

import httpx

from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import normalize_party, parse_office, pick_nominee, surname
from app.pipeline.fetch.state_candidates_tabular import DEFAULT_SETTLE_DAYS, _settled
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

_XL_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_OFFICE_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"

_ZIP_URL_PATTERN = "https://sos.wyo.gov/Elections/Docs/{year}/Results/Primary/{year}_Wyoming_Primary_Results.zip"
# The zip's own member name is year-prefixed ("2026 Primary Results
# Summaries - OFFICIAL.xlsx") -- matched by the stable middle phrase, not
# the exact filename, since the year prefix and "OFFICIAL" suffix are
# exactly the parts most likely to drift cycle to cycle.
_SUMMARY_MEMBER_RE = re.compile(r"Results Summaries.*\.xlsx$", re.IGNORECASE)
_SHEET_NAME = "Statewide Candidates"
_TOTAL_ROW_LABEL = "total"
# "Wyoming Primary Election - August 18, 2026" -- printed in the sheet's
# own title row, read fresh every call rather than trusted from the
# requested `year` alone.
_TITLE_RE = re.compile(r"Primary\s+Election\s*-\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})")
_NON_CANDIDATE_RE = re.compile(r"write.?in|over.?vote|under.?vote|withdrawn", re.IGNORECASE)
_CELL_COL_RE = re.compile(r"^([A-Z]+)")


def _col_index(cell_ref: str) -> int:
    """0-based column index from a cell reference ("C5" -> 2)."""
    m = _CELL_COL_RE.match(cell_ref)
    if not m:
        return 0
    idx = 0
    for ch in m.group(1):
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


async def _fetch_zip(client: httpx.AsyncClient, url: str, label: str) -> bytes | None:
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=60.0, log_label=label, headers=BROWSER_HEADERS,
    )
    return resp.content if resp is not None else None


def _find_summary_sheet_rows(zip_bytes: bytes) -> list[list[str]] | None:
    """Every row of the "Statewide Candidates" sheet, found by NAME (never
    a hardcoded sheet number — this workbook's own sheet order isn't even
    stable: "Statewide Candidates" is the SECOND sheet by id but the
    fourth in display order, with two hidden sheets between)."""
    try:
        outer = zipfile.ZipFile(io.BytesIO(zip_bytes))
        member = next((n for n in outer.namelist() if _SUMMARY_MEMBER_RE.search(n)), None)
        if member is None:
            logger.warning("WY results zip had no member matching 'Results Summaries...xlsx'")
            return None
        workbook_bytes = outer.read(member)
        wb = zipfile.ZipFile(io.BytesIO(workbook_bytes))

        workbook_xml = ET.fromstring(wb.read("xl/workbook.xml"))
        sheet_el = next(
            (s for s in workbook_xml.iter(f"{_XL_NS}sheet") if s.get("name") == _SHEET_NAME), None,
        )
        if sheet_el is None:
            logger.warning("WY results workbook has no sheet named %r", _SHEET_NAME)
            return None
        rid = sheet_el.get(f"{_R_NS}id")

        rels_xml = ET.fromstring(wb.read("xl/_rels/workbook.xml.rels"))
        rel_el = next(
            (r for r in rels_xml.iter(f"{_REL_NS}Relationship")
             if r.get("Id") == rid and r.get("Type") == _OFFICE_DOC_REL),
            None,
        )
        if rel_el is None or not rel_el.get("Target"):
            logger.warning("WY results workbook's %r sheet has no resolvable worksheet target", _SHEET_NAME)
            return None
        sheet_path = "xl/" + rel_el.get("Target")

        shared = [
            "".join(t.text or "" for t in si.iter(f"{_XL_NS}t"))
            for si in ET.fromstring(wb.read("xl/sharedStrings.xml"))
        ]
        sheet = ET.fromstring(wb.read(sheet_path))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        logger.warning("Wyoming results zip was not the expected shape")
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

    def row_to_list(row) -> list[str]:
        # A row's <c> elements are sparse -- OOXML omits a cell entirely
        # when it has no value AND no style, which this sheet's own many
        # genuinely-blank header cells (the whole forward-fill scheme in
        # _federal_totals depends on them) do routinely. Building the
        # list by ITERATION POSITION rather than each cell's own `r`
        # reference ("C5") would silently shift every later column left
        # by however many blanks preceded it. Not a hypothetical: it
        # happens on the very first attempt.
        cells = list(row.iter(f"{_XL_NS}c"))
        result: list[str] = []
        for c in cells:
            col = _col_index(c.get("r") or "")
            while len(result) <= col:
                result.append("")
            result[col] = cell(c)
        return result

    return [row_to_list(row) for row in sheet.iter(f"{_XL_NS}row")]


def _page_election(rows: list[list[str]]) -> tuple[int, str] | None:
    title = rows[0][1] if rows and len(rows[0]) > 1 else ""
    m = _TITLE_RE.search(title)
    if not m:
        return None
    try:
        held = datetime.strptime(m.group(1), "%B %d, %Y").date()
    except ValueError:
        return None
    return held.year, held.isoformat()


def _federal_totals(rows: list[list[str]]) -> list[tuple[str, int | None, str, str, int]] | None:
    """(office, district, party, surname, votes) for every real federal
    candidate column -- forward-filling the race/party header rows
    leftward across each group's blank cells before reading the Total
    row in that same column. None (not []) if the sheet's own header/
    total-row shape can't be found at all -- a real "this broke", not
    "this cycle legitimately has zero federal contests".

    The header rows are found by CONTENT (the "Write-Ins" column every
    real candidate group carries), never a fixed row index -- the same
    principle _find_summary_sheet_rows already applies to finding the
    sheet by name and row_to_list applies to finding a cell by its own
    column reference. A row inserted or removed above the header block
    in some future cycle would otherwise silently shift fixed indices
    onto the wrong rows with no error, misattributing real vote totals
    to the wrong office/party.
    """
    name_row_idx = next((i for i, r in enumerate(rows) if "Write-Ins" in r), None)
    if name_row_idx is None or name_row_idx < 2:
        logger.warning("WY results workbook has no recognisable candidate-name header row")
        return None
    race_row, party_row, name_row = rows[name_row_idx - 2], rows[name_row_idx - 1], rows[name_row_idx]

    total_rows = [r for r in rows if r and r[0].strip().lower() == _TOTAL_ROW_LABEL]
    if len(total_rows) != 1:
        logger.warning("WY results workbook has %d rows labelled 'Total', expected exactly 1", len(total_rows))
        return None
    total_row = total_rows[0]

    results = []
    cur_race, cur_party = "", ""
    width = max(len(race_row), len(party_row), len(name_row), len(total_row))
    for i in range(1, width):
        cur_race = race_row[i] if i < len(race_row) and race_row[i] else cur_race
        cur_party = party_row[i] if i < len(party_row) and party_row[i] else cur_party
        office_district = parse_office(cur_race)
        if office_district is None:
            continue
        party = normalize_party(cur_party)
        if party is None:
            continue
        raw_name = name_row[i] if i < len(name_row) else ""
        if not raw_name or _NON_CANDIDATE_RE.search(raw_name):
            continue
        name = surname(raw_name)
        votes_text = (total_row[i] if i < len(total_row) else "").strip()
        if not name or not votes_text.isdigit():
            continue
        office, district = office_district
        results.append((office, district, party, name, int(votes_text)))
    return results


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state unused, this strategy is WY-only by construction
) -> list[dict] | None:
    url = _ZIP_URL_PATTERN.format(year=year)
    zip_bytes = await _fetch_zip(client, url, f"WY primary results {year}")
    if zip_bytes is None:
        # No zip published yet at this year's predictable URL (a 404
        # before certification is the normal, expected state for weeks)
        # is indistinguishable from a real outage at this layer -- same
        # as every other module in this system, this reports fetch_failed
        # rather than inventing a not-yet-published status fetch_with_
        # retry has no way to actually signal. Benign in practice: the
        # nightly sync's own fetch_failed handling just falls back to
        # whatever the crawler last proved, never drops the state.
        return None

    rows = _find_summary_sheet_rows(zip_bytes)
    if rows is None:
        return None

    election = _page_election(rows)
    if election is None:
        logger.warning("WY results workbook title didn't match the expected shape")
        return None
    page_year, held_on = election
    if page_year != year:
        logger.info("WY results workbook is for %d, not the requested %d", page_year, year)
        return []

    settle_days = source.get("settle_days", DEFAULT_SETTLE_DAYS)
    if not _settled(held_on, settle_days):
        return []

    totals = _federal_totals(rows)
    if totals is None:
        return None

    by_group: dict[tuple[str, int | None, str], list[tuple[str, int]]] = {}
    for office, district, party, name, votes in totals:
        by_group.setdefault((office, district, party), []).append((name, votes))

    runoff_threshold_pct = source.get("runoff_threshold_pct")
    results = []
    for (office, district, party), choices in by_group.items():
        won = pick_nominee(choices, runoff_threshold_pct=runoff_threshold_pct)
        if won:
            results.append({"office": office, "district": district, "party": party, "last_name": won[0]})
    return results
