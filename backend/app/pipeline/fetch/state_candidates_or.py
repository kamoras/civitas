"""Oregon's own official "Abstract of Votes" post-canvass PDF (sos.oregon.gov
/ records.sos.state.or.us) — a single-state deployment, so this is a vendor
module in the same sense state_candidates_pa.py/state_candidates_wy.py are,
not a per-state fetcher (see state_candidates.py for why that distinction is
the whole design).

TWO real hops, both plain unauthenticated GETs — no CSOM, no login:

1. The SoS's own "Election Results & History" SharePoint list answers a
   PLAIN REST query (no CSOM ProcessQuery needed, despite the results page's
   own front end using that heavier protocol to render the same data) —
   `_api/web/lists(guid'...')/items?$filter=Election_x0020_Type eq
   'Primary'&$orderby=Election_x0020_Date desc&$top=1` returns exactly the
   current cycle's primary row, found by the real election DATE field, never
   a hardcoded record id (verified live 2026-09-08: the archive's own
   `uri=` record ids are NOT chronological — a 1904 general's id is
   numerically HIGHER than a 1990 primary's — so "take the newest id" would
   have been a real, silent bug; the SharePoint query's own `$orderby` on
   the real date field is what actually picks the right one). The returned
   row's own "Results" field carries an HTML-entity-escaped link
   ("https&#58;//records...") to the real PDF's RecordViewer page — this
   module reads the `uri=` value straight out of that text rather than
   trying to construct a real URL from the escaped href.
2. The PDF itself downloads from a different, un-escaped, directly-fetchable
   endpoint: `records.sos.state.or.us/.../DocumentStream.ashx?uri={uri}`.

The "Abstract of Votes" PDF has no ruling lines pdfplumber's default table
detection needs — it is whitespace-column-aligned only — so every page is
read with pdfplumber's TEXT-based table strategy instead
(vertical/horizontal_strategy="text"), which reconstructs columns from
consistent x-coordinates. Verified live against the real 2026 document: every
federal contest fits on exactly one page (no candidate list spans two pages
without also starting a fresh, self-contained party block with its own
accurate Total row — confirmed on CD2's real 2-page Democratic field, which
turned out to just be one page each for Ds and Rs, not one field split
across pages).

Each page's own OFFICE ("US Senator" / "US Representative") and, for House,
DISTRICT ("1st District") come from the page's plain text, not the table —
they render as centered text pdfplumber's text-strategy table detection
doesn't capture as a cell. Combined into one string and run through the same
shared parse_office() every other module uses (zero new parsing code:
"US Senator" and "US Representative 1st District" both already match; every
non-federal office on the surrounding pages — Governor, State Senator, State
Representative, judges, DA — correctly does NOT, precisely because
parse_office refuses a bare "Senator"/"Representative" with no "US"/"United
States" prefix).

Within a page, the real table shape (verified against dozens of real
contest blocks) is a repeating unit: a party-name row (e.g. "Democrat",
nothing else in the row), a row whose FIRST cell is blank and whose other
cells are each candidate's SURNAME (the real nominee marked with a leading
"*" — Oregon prints this directly, matching what the state itself calls a
winner), a "County"-labeled row of first names (skipped — surnames are all
this module or pick_nominee ever needs), one row per Oregon county with
that county's own vote counts (skipped — this module trusts the state's own
"Total" row rather than re-summing 36 counties itself, the same choice
state_candidates_wy.py made for its own per-county Total row), and a "Total"
row whose columns line up 1:1 with the surname row's. A stray trailing
"Nominee" row (the page's own "* Nominee / ** Elected / WI = Write In"
footer legend, misread as a table row because its lone "*" character
strips to nothing) is real and harmless: nothing in this module reacts to
row content it doesn't explicitly recognise, so it is silently skipped
rather than needing its own exclusion rule.

The "*Nominee" marker itself is NOT trusted directly to decide the winner —
per this system's standing policy (already applied to Idaho and Indiana's
own vendor-provided winner flags), this module recomputes through the
shared, tie-safe pick_nominee from the REAL vote totals instead, and only
cross-checks the marker in tests. Oregon nominates by plurality — no
runoff exists in state law — so runoff_threshold_pct is null.

No live "unofficial/preliminary" state exists to guard against here: unlike
this system's ENR vendors, the SharePoint list's own historical entries are
ALL titled "Official Results" going back decades, and this module's
own PDF is that same, single, final publication — not a rolling count.
settle_days is still read from config as a defensive floor, matching every
other module in this system, not the primary gate.

Verified live 2026-09-08 against the real, official 2026 primary (page
title "May 19, 2026, Primary Election Abstract of Votes", 63 pages, federal
contests on pages 1-9 -- every name/vote-total below read directly off the
document's own real rows, not recalled): Jeff Merkley (Senate D, real
plurality winner of a 2-way field, 93.7% over Paul Damian Wells), David
Brock Smith (Senate R, real plurality winner of a 7-way field, 107,953
over runner-up Jo Rae Perkins's 99,278 -- this is also the module's own
regression case for the middle-name-wrap quirk documented above: "Smith"
is correct only if that continuation row didn't overwrite it), Suzanne
Bonamici (CD1 D, real plurality winner of a 2-way field, 77,306 over
Jamil O Ahmad's 11,458), Barbara J Kahl (CD1 R, real plurality winner of
a 2-way field), Chris Beck (CD2 D, real plurality winner of a 6-way
field, 15,951 over runner-up Mary Doyle's 9,101), Cliff Bentz (CD2 R,
real plurality winner of a 3-way field), Maxine E Dexter (CD3 D, real
plurality winner of a 3-way field, 89.5%), Loran Ayles (CD3 R,
unopposed), Val Hoyle (CD4 D, real plurality winner of a 3-way field),
Monique DeSpain (CD4 R, real plurality winner of a 2-way field), Janelle
S Bynum (CD5 D, real plurality winner of a 2-way field), Patti Adair
(CD5 R, real plurality winner of a 2-way field), Andrea Salinas (CD6 D,
unopposed), David Russ (CD6 R, unopposed).
"""

import logging
import re
from io import BytesIO

import httpx
import pdfplumber

from app.pipeline.fetch.http_utils import fetch_bytes_with_retry, fetch_json_with_retry
from app.pipeline.fetch.state_candidates_common import normalize_party, parse_office, pick_nominee, surname
from app.pipeline.fetch.state_candidates_tabular import DEFAULT_SETTLE_DAYS, _settled
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

_LIST_ITEMS_URL = (
    "https://sos.oregon.gov/elections/_api/web/lists(guid'8906ce2f-f53e-4a18-9474-642482d5a3e8')"
    "/items?$filter=Election_x0020_Type%20eq%20'Primary'&$orderby=Election_x0020_Date%20desc"
    "&$top=1&$select=Title,Election_x0020_Date,Results"
)
_URI_RE = re.compile(r"uri=(\d+)")
_PDF_URL_PATTERN = "https://records.sos.state.or.us/ORSOSCMSearch/Search/DocumentStream.ashx?uri={uri}"
_NON_CANDIDATE_RE = re.compile(r"misc\.?|write.?in|over.?vote|under.?vote", re.IGNORECASE)

_TABLE_SETTINGS = {"vertical_strategy": "text", "horizontal_strategy": "text"}


async def _discover_pdf_url(client: httpx.AsyncClient, state: str, year: int) -> tuple[str, str] | None:
    """(pdf_url, held ISO date) for the current cycle's primary, or None if
    the SharePoint list has nothing for `year`. The list's own real election
    DATE field (never a record id — see module docstring) decides which row
    is current."""
    item = await fetch_json_with_retry(client, _rate_limiter, _LIST_ITEMS_URL, f"{state} results list")
    if not item:
        return None
    row = (item.get("value") or [None])[0]
    if not row:
        return None
    held = str(row.get("Election_x0020_Date") or "")[:10]
    if not held.startswith(str(year)):
        return None
    m = _URI_RE.search(row.get("Results") or "")
    if not m:
        return None
    return _PDF_URL_PATTERN.format(uri=m.group(1)), held


def _page_office(text: str) -> tuple[str, int | None] | None:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    office_line, second_line = lines[1], lines[2] if len(lines) > 2 else ""
    return parse_office(f"{office_line} {second_line}") or parse_office(office_line)


def _page_candidates(page) -> list[tuple[str, str, int]]:
    """(surname, party, votes) for every real candidate on this page --
    walking the party-row / surname-row / Total-row sequence, county rows
    and the "County"-labeled first-name row skipped entirely (see module
    docstring).

    `awaiting_surnames` guards against a real PDF-rendering quirk: a
    candidate whose first+middle name is too wide for its column wraps
    onto its OWN row below the "County ..." first-name row (verified live:
    "David Brock Smith" -- surname row reads "*Smith", first-name row
    reads "David", then a THIRD row reads just "Brock"). That continuation
    row has the exact same blank-first-cell shape as the real surname row
    and would otherwise silently overwrite it right before the Total row
    is reached, attaching a candidate's real vote total to a fragment of
    someone's middle name instead of their surname. Only the FIRST
    blank-first-cell row seen since the last party row is trusted.
    """
    table = page.extract_table(table_settings=_TABLE_SETTINGS) or []
    results = []
    current_party: str | None = None
    pending_surnames: list[str] | None = None
    awaiting_surnames = False
    for row in table:
        if not row or not any((c or "").strip() for c in row):
            continue
        head = (row[0] or "").strip()
        rest = [(c or "").strip() for c in row[1:]]
        if head and not any(rest):
            party = normalize_party(head)
            if party is not None:
                current_party = party
                awaiting_surnames = True
            continue
        if awaiting_surnames and head == "" and any(rest):
            pending_surnames = rest
            awaiting_surnames = False
            continue
        if head == "Total" and pending_surnames is not None and current_party is not None:
            for raw_name, votes_text in zip(pending_surnames, rest):
                if not raw_name or _NON_CANDIDATE_RE.search(raw_name):
                    continue
                name = surname(raw_name.lstrip("*"))
                digits = votes_text.replace(",", "")
                if not name or not digits.isdigit():
                    continue
                results.append((name, current_party, int(digits)))
            pending_surnames = None
    return results


def _federal_contests(pdf_bytes: bytes) -> list[tuple[str, int | None, str, str, int]]:
    """(office, district, party, surname, votes) for every real federal
    candidate in the document -- non-federal pages (Governor, state
    legislature, judges, ...) are skipped by parse_office alone, never a
    hardcoded page range."""
    results = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            office_district = _page_office(page.extract_text() or "")
            if office_district is None:
                continue
            office, district = office_district
            for name, party, votes in _page_candidates(page):
                results.append((office, district, party, name, votes))
    return results


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state unused, this strategy is OR-only by construction
) -> list[dict] | None:
    discovered = await _discover_pdf_url(client, state, year)
    if discovered is None:
        return []
    pdf_url, held_on = discovered

    settle_days = source.get("settle_days", DEFAULT_SETTLE_DAYS)
    if not _settled(held_on, settle_days):
        return []

    pdf_bytes = await fetch_bytes_with_retry(client, _rate_limiter, pdf_url, f"{state} results {year}")
    if pdf_bytes is None:
        return None

    by_group: dict[tuple[str, int | None, str], list[tuple[str, int]]] = {}
    for office, district, party, name, votes in _federal_contests(pdf_bytes):
        by_group.setdefault((office, district, party), []).append((name, votes))

    runoff_threshold_pct = source.get("runoff_threshold_pct")
    results = []
    for (office, district, party), choices in by_group.items():
        won = pick_nominee(choices, runoff_threshold_pct=runoff_threshold_pct)
        if won:
            results.append({"office": office, "district": district, "party": party, "last_name": won[0]})
    return results
