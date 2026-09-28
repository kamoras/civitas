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

STATEWIDE EXECUTIVE contests (only with `statewide_offices`) come off the
same document by a different reader -- see "Statewide executive contests"
below for why word positions rather than the table. 2026 carries two:
Governor (Tina Kotek D, Christine Drazan R) and the NON-partisan
Commissioner of the Bureau of Labor and Industries, which Christina E
Stephenson won outright in May with 63.2% (ORS 249.088(1)(b); the
Abstract marks her "**" Elected), so it is not a November contest and
nobody is published for it. How a non-partisan contest resolves is the
state entry's `nonpartisan_resolution`, never assumed here.
"""

import logging
import re
from io import BytesIO

import httpx
import pdfplumber

from app.pipeline.fetch.http_utils import fetch_bytes_with_retry, fetch_json_with_retry
from app.pipeline.fetch.state_candidates_common import (
    JUDICIAL_RESOLUTION_DECIDED_EARLY,
    JUDICIAL_RESOLUTION_ELECTS,
    JUDICIAL_RESOLUTION_SOLE_CANDIDATE,
    NONPARTISAN,
    DiscoveryFailed,
    clean_display_name,
    normalize_party,
    parse_office,
    parse_statewide_office,
    pick_nominee,
    pick_nominees,
    resolve_confirmed_nominees,
    surname,
)
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
    the SharePoint list's current row is for a different year (healthy: not
    published yet). Raises DiscoveryFailed if the list itself couldn't be
    read. The list's own real election DATE field (never a record id — see
    module docstring) decides which row is current."""
    item = await fetch_json_with_retry(client, _rate_limiter, _LIST_ITEMS_URL, f"{state} results list")
    if not item:
        raise DiscoveryFailed(f"{state} results list fetch failed")
    row = (item.get("value") or [None])[0]
    if not row:
        raise DiscoveryFailed(f"{state} results list returned no rows")
    held = str(row.get("Election_x0020_Date") or "")[:10]
    if not held.startswith(str(year)):
        return None
    m = _URI_RE.search(row.get("Results") or "")
    if not m:
        raise DiscoveryFailed(f"{state} results list row has no Results link")
    return _PDF_URL_PATTERN.format(uri=m.group(1)), held


def _page_office(text: str) -> tuple[str, int | None] | None:
    """Office/district for this page, found by scanning the first few lines
    for one that parse_office recognizes (paired with the line after it, for
    "US Representative" + "1st District") rather than trusting a fixed line
    index — the title line is one line on every page verified live, but
    searching a small window survives it wrapping to two."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for i in range(1, min(len(lines), 5)):
        second_line = lines[i + 1] if i + 1 < len(lines) else ""
        found = parse_office(f"{lines[i]} {second_line}") or parse_office(lines[i])
        if found:
            return found
    return None


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
            else:
                logger.warning("OR results: unrecognized party label %r, skipping its block", head)
            continue
        if awaiting_surnames and head == "" and any(rest):
            pending_surnames = rest
            awaiting_surnames = False
            continue
        if head == "Total" and pending_surnames is not None and current_party is not None:
            if len(rest) != len(pending_surnames):
                # Column counts must line up 1:1 (see module docstring) --
                # a mismatch means the "text" table strategy clustered this
                # Total row's numeric columns differently than the surname
                # row's, so zip()ping them would silently misattribute a
                # vote total to the wrong candidate. Fail closed, matching
                # state_candidates_wy.py's own Total-row guard.
                logger.warning(
                    "OR results: Total row has %d columns, surname row had %d, skipping block",
                    len(rest), len(pending_surnames),
                )
                pending_surnames = None
                continue
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


# ── Statewide executive contests ─────────────────────────────────────
#
# Read from each word's own position on the page, NOT from the text-
# strategy table the federal pages above use. That table places its
# column boundaries between whitespace runs, and on the statewide pages
# it cuts names mid-word -- the real 2026 Governor page comes back as
# "Alexander At" | "kinson IV" and "County Fo" | "rest (Fora)". A
# surname-only federal read was verified page by page and survives it;
# the WHOLE printed name a statewide nominee is shown under does not.
#
# Every name and every vote figure on the Abstract is RIGHT-aligned to
# its column (verified on every Governor and BOLI page: "*Kotek", "Tina",
# each county's figure and "385,999" all end at x=347), so the Total
# row's own numbers fix each column's right edge, and a word belongs to
# the first column whose edge is at or past the word's own right end.
# A candidate's block is: the name line (surnames, the nominee marked
# "*" and a majority winner of a NON-partisan contest "**" -- the page's
# own legend reads "* Nominee / ** Elected"), a "County" line of given
# names, any wrapped continuation of those given names ("Brock" below
# "David" for David Brock Smith), the 36 county rows, and "Total".
#
# A contest can run over several pages: the 2026 Governor's Democratic
# field is printed as "Democrat" (6 candidates) and then "Democrat
# (cont.)" (4 more and Misc.), each page with its own Total row, so the
# field is only complete once every page of it is merged.
#
# Legislative contests are deliberately NOT read here. Their pages print
# several districts each, split some district headings over two lines
# ("18th" / "District"), and give a one-county district no Total row at
# all (Senate District 3's Democratic block ends at its lone "Jackson"
# row), so a block's figures are not reliably where this reader expects
# them. The page keeps state legislative districts among its omissions.

# The label column (party names, "County", county names, "Total") starts
# at x=41 (Total at 59); the leftmost candidate column's words start past
# 100 on every page read.
_LABEL_X_MAX = 65.0
_ALIGN_TOL = 2.0
_LINE_TOL = 3.0
_LEGEND_RE = re.compile(r"^(?:\*+|WI)$")  # the "* Nominee / ** Elected / WI = Write In" footer


def _page_lines(page) -> list[list[dict]]:
    """The page's words grouped into visual lines, top to bottom, each
    line left to right."""
    words = sorted(page.extract_words() or [], key=lambda w: (w["top"], w["x0"]))
    lines: list[list[dict]] = []
    for word in words:
        if lines and abs(lines[-1][0]["top"] - word["top"]) <= _LINE_TOL:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def _line_text(line: list[dict]) -> str:
    return " ".join(w["text"] for w in line)


def _is_label_line(line: list[dict]) -> bool:
    return bool(line) and line[0]["x0"] <= _LABEL_X_MAX


def _figure(text: str) -> int | None:
    digits = text.replace(",", "")
    return int(digits) if digits.isdigit() else None


def _page_statewide_office(lines: list[list[dict]]) -> tuple[str, str | None] | None:
    """The statewide office this page prints, from its centred title lines
    (after the "... Abstract of Votes" header, before the first line in
    the label column), or None."""
    for line in lines[1:4]:
        if _is_label_line(line):
            break
        found = parse_statewide_office(_line_text(line))
        if found:
            return found
    return None


def _read_block(
    name_line: list[dict], given_lines: list[list[dict]], total_line: list[dict],
) -> list[tuple[str, str, int]] | None:
    """[(printed surname cell, given-name text, votes), ...] for one party
    block, one entry per column of its Total row, or None when any word
    does not fall in a column or any column lacks a name or a figure."""
    figures = [_figure(w["text"]) for w in total_line[1:]]
    if not figures or any(f is None for f in figures):
        return None
    edges = [w["x1"] for w in total_line[1:]]

    def column(word: dict) -> int | None:
        return next((i for i, edge in enumerate(edges) if word["x1"] <= edge + _ALIGN_TOL), None)

    surnames: list[list[str]] = [[] for _ in edges]
    given: list[list[str]] = [[] for _ in edges]
    for word in name_line:
        i = column(word)
        if i is None:
            return None
        surnames[i].append(word["text"])
    for line in given_lines:
        for word in line:
            i = column(word)
            if i is None:
                return None
            given[i].append(word["text"])
    if not all(surnames):
        return None
    return [
        (" ".join(s), " ".join(g), votes)
        for s, g, votes in zip(surnames, given, figures, strict=True)
    ]


def _page_blocks(lines: list[list[dict]]) -> list[tuple[str | None, list[tuple[str, str, int]]]] | None:
    """[(party or None, block), ...] for every candidate block on one
    statewide page, or None when any block on it cannot be read -- a
    field missing one block would name the wrong winner, so a page is
    read whole or not at all."""
    blocks: list[tuple[str | None, list[tuple[str, str, int]]]] = []
    party: str | None = None
    unknown_party = False
    for idx, line in enumerate(lines):
        if not _is_label_line(line):
            continue
        head = line[0]["text"]
        if head == "County":
            if idx == 0 or _is_label_line(lines[idx - 1]):
                return None
            given_lines = [line[1:]]
            j = idx + 1
            while j < len(lines) and not _is_label_line(lines[j]):
                given_lines.append(lines[j])
                j += 1
            total = None
            for later in lines[j:]:
                if _is_label_line(later) and later[0]["text"] in ("County", "Total"):
                    total = later if later[0]["text"] == "Total" else None
                    break
            if total is None or unknown_party:
                return None
            block = _read_block(lines[idx - 1], given_lines, total)
            if block is None:
                return None
            blocks.append((party, block))
        elif head != "Total" and not _LEGEND_RE.match(head) and not any(
            _figure(w["text"]) is not None for w in line
        ):
            # A party heading ("Democrat", "Republican (cont.)"). One this
            # doesn't recognise must not leave the previous party's label
            # on the block below it.
            party = normalize_party(_line_text(line))
            unknown_party = party is None
    return blocks


def _statewide_contests(pdf_bytes: bytes) -> dict[tuple[str, str | None], list | None]:
    """{(office, seat): [(party or None, block), ...]} for every statewide
    executive contest in the document, merged across the pages it runs
    over; None for a contest any page of which could not be read."""
    contests: dict[tuple[str, str | None], list | None] = {}
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            if _page_office(page.extract_text() or "") is not None:
                continue
            lines = _page_lines(page)
            office = _page_statewide_office(lines)
            if office is None:
                continue
            blocks = _page_blocks(lines)
            if blocks is None or not blocks:
                contests[office] = None
            elif office not in contests:
                contests[office] = blocks
            elif contests[office] is not None:
                contests[office].extend(blocks)
    return contests


def _nonpartisan_winners(
    named: list[tuple[str, int, int]], write_ins: int, source: dict,
) -> list[tuple[str, int]] | None:
    """[(name, marker)] who go on to November from a NON-partisan statewide
    contest, [] when nobody does, None when the state's rule isn't
    configured. Oregon's is ORS 249.088(1): "(a) Unless a candidate for
    nonpartisan office receives a majority of the votes cast for the
    office, the two candidates who receive the highest number of votes
    are nominated. (b) If a candidate for nonpartisan office receives a
    majority of votes cast for the office, that candidate is elected."
    Votes cast for the office include its write-ins, so the majority is
    measured against them too."""
    resolution = source.get("nonpartisan_resolution")
    if resolution is None:
        return None
    marker = {name: mark for name, _votes, mark in named}
    ranked = sorted(named, key=lambda c: c[1], reverse=True)
    cast = sum(v for _n, v, _m in named) + write_ins
    if resolution == JUDICIAL_RESOLUTION_DECIDED_EARLY:
        return []
    if ranked and cast and ranked[0][1] * 2 > cast:
        if resolution == JUDICIAL_RESOLUTION_ELECTS:
            return []
        if resolution == JUDICIAL_RESOLUTION_SOLE_CANDIDATE:
            return [(ranked[0][0], ranked[0][2])]
    won = pick_nominees(
        [(n, v) for n, v, _m in named], None, int(source.get("nonpartisan_advance_count") or 2),
    )
    return [(name, marker[name]) for name, _pct in won]


def _statewide_nominees(contests: dict, source: dict) -> list[dict]:
    """The November nominees these contests resolve to. Each winner is
    computed from the vote figures (pick_nominee / pick_nominees, as for
    the federal pages) and then held against the Abstract's own marks: a
    nominee must carry "*", and a non-partisan contest decided in May
    must show its winner "**" (Elected).

    Anything this cannot read or reconcile RAISES DiscoveryFailed rather
    than dropping the contest. With `statewide_offices` set, a contest
    missing from the result is recorded as a confirmed absence, so a
    skipped Governor page would tell a reader Oregon elects no governor.
    Failing the fetch leaves the last good sync standing instead."""
    threshold = source.get("runoff_threshold_pct")
    records: list[dict] = []
    for (office, seat), blocks in contests.items():
        if blocks is None:
            raise DiscoveryFailed(f"a {office} page could not be read")
        parties = {party for party, _block in blocks}
        if None in parties and len(parties) > 1:
            raise DiscoveryFailed(f"{office} mixes party and non-party blocks")
        by_party: dict[str | None, list[tuple[str, str, int]]] = {}
        for party, block in blocks:
            by_party.setdefault(party, []).extend(block)
        for party, cells in by_party.items():
            named = [
                (clean_display_name(f"{given} {cell}"), votes, len(cell) - len(cell.lstrip("*")))
                for cell, given, votes in cells if not _NON_CANDIDATE_RE.search(cell)
            ]
            write_ins = sum(votes for cell, _g, votes in cells if _NON_CANDIDATE_RE.search(cell))
            if party is None:
                won = _nonpartisan_winners(named, write_ins, source)
                if won is None:
                    raise DiscoveryFailed(f"{office} is non-partisan and no nonpartisan_resolution is set")
                if not won:
                    # Decided in the primary, which the Abstract states
                    # itself with "**" (Elected).
                    leader = max(named, key=lambda c: c[1], default=None)
                    if leader and leader[2] != 2:
                        raise DiscoveryFailed(f"{office} leader {leader[0]!r} is not marked elected")
                    continue
            else:
                pick = pick_nominee([(n, v) for n, v, _m in named], threshold)
                marker = {n: m for n, _v, m in named}
                won = [(pick[0], marker[pick[0]])] if pick else []
            if any(mark != 1 for _name, mark in won):
                raise DiscoveryFailed(f"{office} {party} winner(s) {won!r} not marked nominee")
            for name, _mark in won:
                if not name:
                    continue
                if party is None:
                    # A non-partisan office (the Labor Commissioner) is
                    # stored the way a certified list stores one: the
                    # NONPARTISAN code with its label, never "" -- which
                    # the page cannot tell from a missing party.
                    records.append({"office": office, "district": seat, "party": NONPARTISAN,
                                    "party_label": "Nonpartisan", "last_name": name})
                else:
                    records.append({"office": office, "district": seat, "party": party, "last_name": name})
    return records


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state unused, this strategy is OR-only by construction
) -> list[dict] | None:
    try:
        discovered = await _discover_pdf_url(client, state, year)
    except DiscoveryFailed as exc:
        logger.warning("OR results: discovery failed: %s", exc)
        return None
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
    results = resolve_confirmed_nominees(by_group, runoff_threshold_pct)
    if source.get("statewide_offices"):
        try:
            results.extend(_statewide_nominees(_statewide_contests(pdf_bytes), source))
        except DiscoveryFailed as exc:
            logger.warning("OR results: statewide contests unreadable, failing the fetch: %s", exc)
            return None
    return results
