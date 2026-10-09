"""A state's certified general-election candidate list, published as a
spreadsheet (xlsx or csv) — one row per candidate on the November ballot.

Maine is the live case, and the reason this exists. Maine's adapter read
the June primary's official results and confirmed the Democratic Senate
nominee, correctly: the candidate won with 77.7%, then withdrew on
2026-07-10, and the party nominated a replacement at a convention on July
25. Primary results cannot see that, and they never will — the same way
South Carolina's results named a June Senate winner after a special
primary had replaced them. The Secretary of State's "2026 General Candidate List" is
the ballot as certified, replacement included, so it is read instead.

Every state-specific detail is configuration: where the list is linked
from, which columns hold what, and how the state codes its federal
offices (Maine writes "US" for Senate and "CG" for Congress, which no
label parser should be taught to guess).

    "discovery": {"page_url": ..., "link_regex": "...{year}..."},
    "format": {
        "office_column": "Office",
        "office_codes": {"US": "S", "CG": "H"},
        "district_column": "Dist",
        "party_column": "Party",
        "surname_column": "Last Name",
        "name_columns": ["First Name", "Middle Name", "Last Name", "Suffix"]
    }

Optional, each because a live state needed it:
  discovery.index_url + index_regex  one hop first, to the page that links the
                                     file (Virginia: index -> "{year} November
                                     Federal Offices" page -> xlsx)
  format.surname_column omitted      the name is one printed column; the
                                     surname is its last word (Colorado)
  format.status_column/status_values keep only these statuses ("Qualified")
  format.exclude                     {column: value} rows to drop — Colorado
                                     lists declared write-ins, who are not
                                     printed on the ballot
  discovery.link_regexes             several files, all required (Tennessee
                                     posts the Senate and House separately)
  format.office_parse                read the office column with parse_office
                                     ("United States House of Representatives
                                     District 1") instead of an exact code map;
                                     with district_column too, that column is
                                     read after it (Nebraska prints "District
                                     01" in a column of its own)
  format.office_fill_down            the office is printed once per group and
                                     left blank on the rows below it (Iowa)
  discovery.url + year_regex         the list lives at one fixed address that
                                     always shows the CURRENT election (New
                                     Mexico's candidate portal), so the page
                                     must name this year's election before a
                                     row is read — last cycle's list would
                                     confirm last cycle's people; year_regex
                                     is honoured on a discovered page too
  discovery.url with {year}          the fixed address names the election's
                                     year (Michigan's candidate report)
  discovery.form_button              the list is the page's own "Export to CSV"
                                     button (Hawaii's candidate report): the
                                     page's form is posted back with that
                                     button, exactly as a visitor's click does
  discovery.form_select              {select name: [option texts]} — with
                                     form_button, the form is posted once per
                                     option found, chosen by its visible text
                                     (North Dakota's contest ids change each
                                     election); a year missing an office (no
                                     Senate race) skips it, but one must exist
  format.name_last_first             names are printed "BERNING, Nathan M."
  format.html_headings               an HTML page holds one table per office
                                     under a heading (Alaska's <h4>UNITED
                                     STATES SENATOR</h4>); each row carries the
                                     headings above it as heading_1..heading_6,
                                     so office_column can name one
  format.party_regex                 the party is inside a longer cell; the
                                     regex's first group is the label (Alaska
                                     prints it after the name)
  format.exclude_regex               {column: regex} rows to drop — Alaska
                                     prefixes a write-in's name "Certified
                                     Write-In"
  discovery.every_link               read EVERY page the link regex matches,
                                     at least one (Kentucky links one page
                                     per office, and a year with no Senate
                                     race has no Senate page)
  format.party_column as a list      the first of these columns present in a
                                     row (Tennessee's legislative files say
                                     "Party" where its federal ones say
                                     "Party Name")
  format.state_office_codes          with statewide_offices on a list read by
                                     office_codes: the list's own codes for
                                     state offices, each spelled out as the
                                     label the shared gates read ({"GOV":
                                     "Governor", "SS": "State Senate"} --
                                     Maine; Colorado spells its offices out
                                     but keys Congress by a bare district
                                     number); a district cell holding a
                                     number is read after it as "District N"
  format.wrapped_columns             a cell too long for its PDF column
                                     wraps onto a line of its own; a row
                                     holding text ONLY in these columns is
                                     the tail of the row above (Nebraska's
                                     "Legal Marijuana" / "NOW")
  format.running_mate_columns        a state-office row's running mate,
                                     shown "Governor and Running Mate" as
                                     the state prints a joint ticket
                                     elsewhere (Maryland lists the
                                     Lieutenant Governor as a "Related
                                     Candidate" of the Governor's row)
  format.party_names                 {printed: name} for a party the list
                                     prints only as an abbreviation, spelled
                                     out as the party's own name so the page
                                     shows it rather than the abbreviation
                                     (Delaware's "Ind Pty of DE" is the
                                     Independent Party of Delaware, FEC's
                                     IDE); a display translation, never a
                                     party decision -- the name still goes
                                     through the shared party reading
  discovery.next_page_regex          the list is paged; each page's link to
                                     the next is followed until there is
                                     none (Alaska's 3 pages: its House
                                     districts run onto pages 2 and 3)
  format.outline_rows                the page is an indented outline, one
                                     line per one-row table, indented by
                                     empty leading cells (Oklahoma's List
                                     of Elections: county, section, office,
                                     then "NAME, PARTY"); each line becomes
                                     a row carrying the lines above it,
                                     keyed by indent: outline_2 is a line
                                     indented two cells (outline_rows)
  format.report_grid                 the page is one report laid out on an
                                     HTML grid by colspan (a JasperReports
                                     export: Michigan's Official Candidate
                                     Listing); each value belongs to the
                                     header column starting where it
                                     starts, and an office heading spanning
                                     the columns is carried down as
                                     `heading` (report_grid_rows)
  format.office_regex                the office is the first group of this
                                     regex over the office cell (Michigan
                                     follows it with the term and seat
                                     count: "1st District State Senator 4
                                     Year Term (1) Position Files In WAYNE
                                     County"); a cell it does not match is
                                     read whole
  format.seats_regex                 the office cell prints its seat count
                                     (first group; Michigan's "(2)
                                     Positions"): a list still holding more
                                     of one party's candidates than seats
                                     is not the ballot yet, answered []
  format.slate_complete              {office, requires}: every party with a
                                     candidate for `office` must have one
                                     for each of `requires` before state
                                     offices are read (Michigan's
                                     convention-nominated SoS and AG); until
                                     then only federal rows are returned,
                                     marked state_offices_incomplete
  format.name_regex                  the name is inside a longer cell; the
                                     regex's first group is the name
                                     (Oklahoma prints "JOHN DOE,
                                     REPUBLICAN" in one cell)
  discovery.form_select patterns     each choice is a pattern the option's
                                     whole text must match ({year} filled),
                                     so a label carrying a date is named by
                                     its year (Montana's "FEDERAL GENERAL
                                     2026 (11/03/2026) (General)")
  discovery.form_select_postback     the dropdown reloads the page when it
                                     changes (ASP.NET AutoPostBack), so the
                                     choice is posted first and the button
                                     pressed on the page that comes back,
                                     which must show the choice (Montana's
                                     election picker)
  discovery.headers                  request headers added to the browser's
                                     own (Arkansas's table API answers only
                                     an XMLHttpRequest)
  discovery.json_body                POST this JSON body to discovery.url
                                     instead of a GET; "{election}" in it is
                                     filled from discovery.election (Idaho)
  discovery.election                 {url, json_body?, rows, name, value,
                                     regex}: the portal's own election
                                     list, read for the id of the one
                                     election whose name matches regex
                                     ({year} filled); none yet is "not yet",
                                     two is a failure (Idaho)
  discovery.final_path               a JSON path that must be true before
                                     the list is the ballot (Idaho's
                                     isFinalList)
  discovery.year_url                 year_regex is checked on this page
                                     instead of the list, for a feed that
                                     names no year (Arkansas)
  format.json_rows / json_total      the payload is JSON: rows are the list
                                     at this path, each object read like a
                                     spreadsheet row; json_total names the
                                     count the API reports, which must equal
                                     the rows read (a partial page is half a
                                     ballot). An unparsable payload -- an
                                     empty body is how Arkansas's API refuses
                                     -- is a failed read, never no rows
  format.ruled_columns               a PDF table drawn with rules, its
                                     columns named left to right: the
                                     vertical rules are the column edges and
                                     a rule spanning the whole table closes
                                     an office's group, its office printed
                                     once beside its candidates (Clark
                                     County, Nevada -- ruled_table_rows)
  format.office_parts                [[column, prefix], ...]: the office is
                                     spread over several fields, joined with
                                     each non-blank one after its prefix
                                     (Idaho: officeName, "District " +
                                     district, "Seat " + seat)
  statewide_offices                  also read the state's own executive
                                     contests and legislative seats, through
                                     parse_statewide_office and
                                     parse_state_leg_office — the same claim
                                     the flag makes on a results adapter (see
                                     the sources file's _contract). A list is
                                     the certified November ballot, so every
                                     qualified name is published, independents
                                     included, rather than one winner per
                                     party (Wyoming, New Mexico, Tennessee)
  judicial_offices                   with statewide_offices and office_parse,
                                     also read the state's elected
                                     judgeships through parse_judicial_office
                                     -- the same separate claim the flag
                                     makes on a results adapter, that the
                                     state's judicial contests are partisan
                                     (North Carolina's candidate list: its
                                     results export names only the winners
                                     of contested primaries, so an unopposed
                                     nominee and a party's later replacement
                                     were missing or stale)

An HTML page is read from its table whose header row carries every
configured heading (New Mexico). A PDF is read as a table too (Iowa, Nebraska): the row whose cells include
every configured column heading is the header, and each later cell on the
page belongs to the column it starts in (_column_starts). Cells are split
where the gap between two words is wider than a space — see _CELL_GAP.

Only the columns named here are read. Virginia's list carries every
candidate's campaign email, phone and street address beside the ballot
fields; this platform has no reason to hold them.

Rows repeat (Virginia prints each candidate once per locality), so records
are deduplicated.
"""

import csv
import html
import io
import json
import logging
import re
from urllib.parse import urljoin

import httpx
import pdfplumber
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import rows as _clustered_rows
from app.pipeline.fetch.http_utils import (
    BROWSER_HEADERS,
    fetch_bytes_with_retry,
    fetch_text_with_retry,
    fetch_with_retry,
)
from app.pipeline.fetch.state_candidates_common import (
    ballot_list_party,
    clean_display_name,
    discover_certification_link,
    NONPARTISAN,
    federal_only,
    in_ballot_window,
    not_yet,
    normalize_party,
    parse_judicial_office,
    parse_office,
    parse_state_leg_office,
    parse_statewide_office,
    surname,
)
from app.pipeline.fetch.state_candidates_tabular import _html_rows, _xlsx_rows
from app.pipeline.rate_limiter import RateLimiter
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)


_SUFFIX_RE = re.compile(r"\s+((?:Jr|Sr)\.?|II|III|IV)$")

# Points. A space between two words of one cell measures 1.6-1.8 in both
# Iowa's and Nebraska's lists; the narrowest gap between two cells is 5.3
# (a phone number and an email) and between a party and a name 7.9.
_CELL_GAP = 4.0


def _cells(words: list[dict]) -> list[dict]:
    """One printed row's words joined into cells: {x0, x1, text}."""
    cells: list[dict] = []
    for w in sorted(words, key=lambda w: w["x0"]):
        if cells and w["x0"] - cells[-1]["x1"] < _CELL_GAP:
            cells[-1]["x1"] = w["x1"]
            cells[-1]["text"] += " " + w["text"]
        else:
            cells.append({"x0": w["x0"], "x1": w["x1"], "text": w["text"]})
    return cells


def _overlaps(cell: dict, heading: dict) -> bool:
    return min(cell["x1"], heading["x1"]) > max(cell["x0"], heading["x0"])


def _column_starts(header: list[dict], rows: list[list[dict]]) -> list[tuple[float, str]]:
    """Where each heading's column begins on the page. Data is often
    left-aligned under a centred heading, so a column starts at the
    leftmost cell found under that heading alone — a cell spanning two
    headings (a footer sentence) says nothing about either."""
    starts = {h["text"]: h["x0"] for h in header}
    for cells in rows:
        for cell in cells:
            hits = [h for h in header if _overlaps(cell, h)]
            if len(hits) == 1:
                starts[hits[0]["text"]] = min(starts[hits[0]["text"]], cell["x0"])
    return sorted((x, text) for text, x in starts.items())


# Points. A ruling is a filled rectangle this thin; words whose tops are
# this close are one printed line (rows on Clark County's list are 19pt
# apart, and a row's words share their top to within 1pt).
_RULE = 2.0
_SAME_LINE = 4.0


def ruled_table_rows(pages: list[tuple[list[dict], list[dict]]], names: list[str]) -> list[dict] | None:
    """Rows of a PDF table drawn with rules, one row per candidate, keyed by
    `names` (the columns left to right) -- for a list whose office cell is
    printed once, centred beside its candidates, rather than on their first
    row (Clark County, Nevada's "Candidates and Contests").

    The table's own drawing decides everything: the thin vertical rules
    are the column edges, and a horizontal rule spanning the whole table
    closes one office's group (a rule between two candidates of one
    office spans only the candidate columns). The first column's words in
    a group are its office, joined in reading order; every other printed
    line in the group is a row carrying that office.

    None when a page's rules do not divide it into exactly len(names)
    columns, or a group lists candidates with no office beside them (an
    office split across a page break): a row placed under a guessed office
    is worse than no list. `pages` is (words, rects) per page."""
    rows: list[dict] = []
    for words, rects in pages:
        thin_v = sorted({round((r["x0"] + r["x1"]) / 2) for r in rects
                         if r["x1"] - r["x0"] < _RULE and r["bottom"] - r["top"] > _RULE})
        edges: list[float] = []
        for x in thin_v:
            if not edges or x - edges[-1] > _RULE:
                edges.append(x)
        if not edges:
            continue  # a page with no table on it
        if len(edges) != len(names) + 1:
            logger.warning("ruled table has %d columns, expected %d", len(edges) - 1, len(names))
            return None
        left, right = edges[0], edges[-1]
        closes = sorted({round(r["top"]) for r in rects
                         if r["bottom"] - r["top"] < _RULE and r["x0"] <= left + _RULE and r["x1"] >= right - _RULE})
        for top, bottom in zip(closes, closes[1:]):
            inside = [w for w in words if top <= (w["top"] + w["bottom"]) / 2 < bottom
                      and left <= (w["x0"] + w["x1"]) / 2 <= right]
            column = {id(w): sum(1 for x in edges[1:-1] if (w["x0"] + w["x1"]) / 2 > x) for w in inside}
            office = " ".join(w["text"] for w in sorted(
                (w for w in inside if column[id(w)] == 0), key=lambda w: (round(w["top"]), w["x0"])))
            lines: list[list[dict]] = []
            for w in sorted((w for w in inside if column[id(w)] > 0), key=lambda w: w["top"]):
                if lines and w["top"] - lines[-1][0]["top"] < _SAME_LINE:
                    lines[-1].append(w)
                else:
                    lines.append([w])
            if lines and not office:
                logger.warning("ruled table lists candidates with no office beside them")
                return None
            for line in lines:
                row = {name: "" for name in names}
                row[names[0]] = office
                for w in sorted(line, key=lambda w: w["x0"]):
                    name = names[column[id(w)]]
                    row[name] = f"{row[name]} {w['text']}".strip()
                rows.append(row)
    return rows


def pdf_table_rows(pages: list[list[dict]], headings: list[str]) -> list[dict]:
    """Rows keyed by heading from each page's words (pdfplumber
    extract_words output). A page without the header row is skipped. Each
    cell belongs to the column whose start is the nearest at or left of it,
    so a short cell ("PO Box 33") that overlaps no heading still lands in
    its own column rather than the nearest heading's."""
    rows: list[dict] = []
    for words in pages:
        clustered = _clustered_rows(words)
        lines = [_cells(clustered[row_id]) for row_id in sorted(clustered)]
        at = next((i for i, cells in enumerate(lines) if set(headings) <= {c["text"] for c in cells}), None)
        if at is None:
            continue
        body = lines[at + 1:]
        starts = _column_starts(lines[at], body)
        for cells in body:
            row: dict[str, str] = {}
            for cell in cells:
                key = next((text for x, text in reversed(starts) if x <= cell["x0"] + 1.0), starts[0][1])
                row[key] = f"{row.get(key, '')} {cell['text']}".strip()
            rows.append(row)
    return rows


def html_table_rows(page: bytes, headings: list[str]) -> list[dict]:
    """Rows of the page's table whose header row names every heading. A
    repeated heading keeps its last column (New Mexico prints "Contest"
    twice; both hold the office).

    New Mexico's portal serves UTF-8 (in its Content-Type header) with no
    charset in the page itself, and lxml then reads the bytes as Latin-1:
    its 2026 Secretary of State nominee's "LÓPEZ" came out as two Latin-1 characters.
    Bytes that are valid UTF-8 are read as UTF-8."""
    try:
        page.decode("utf-8")
    except UnicodeDecodeError:
        tree = lxml_html.fromstring(page)
    else:
        tree = lxml_html.fromstring(page, parser=lxml_html.HTMLParser(encoding="utf-8"))
    for table in tree.iter("table"):
        trs = table.xpath("./tr|./thead/tr|./tbody/tr")
        if not trs:
            continue
        header = [" ".join(c.text_content().split()) for c in trs[0].xpath("./th|./td")]
        if set(headings) <= set(header):
            return [
                dict(zip(header, (" ".join(c.text_content().split()) for c in tr.xpath("./td"))))
                for tr in trs[1:]
            ]
    return []


def outline_rows(page: bytes) -> list[dict]:
    """Rows of a page printed as an indented outline: every line is a
    one-row table whose leading EMPTY cells indent it (Oklahoma's List of
    Elections: a section indented one cell, an office two, each candidate
    three beneath it). Each line becomes a row carrying itself and the
    nearest line above it at every shallower indent, keyed by its indent
    (outline_0 .. outline_N), so office_column can name the office's
    indent and the name columns the candidate's. A shallower line clears everything deeper,
    exactly as a new heading does in html_headings; the text outside any
    table (Oklahoma's county names) is not part of the outline."""
    try:
        page.decode("utf-8")
    except UnicodeDecodeError:
        tree = lxml_html.fromstring(page)
    else:
        tree = lxml_html.fromstring(page, parser=lxml_html.HTMLParser(encoding="utf-8"))
    rows: list[dict] = []
    stack: dict[int, str] = {}
    for tr in tree.iter("tr"):
        cells = [" ".join(td.text_content().split()) for td in tr.xpath("./td|./th")]
        depth = next((i for i, text in enumerate(cells) if text), None)
        if depth is None:
            continue
        stack = {k: v for k, v in stack.items() if k < depth}
        stack[depth] = cells[depth]
        rows.append({f"outline_{k}": v for k, v in stack.items()})
    return rows


def report_grid_rows(page: bytes, headings: list[str]) -> list[dict]:
    """Rows of a report laid out on one HTML grid (a JasperReports export
    -- Michigan's Official Candidate Listing): every line is a <tr> of
    cells placed by colspan, so a value belongs to the header column that
    starts where it starts. The header row is the first whose cells name
    every configured heading. A line whose only text is one cell spanning
    more than one header column is a heading, carried onto the rows below
    it as `heading` (Michigan's "U.S. Senate 6 Year Term (1) Position");
    a repeat of the header row (a new report page) is skipped."""
    try:
        page.decode("utf-8")
    except UnicodeDecodeError:
        tree = lxml_html.fromstring(page)
    else:
        tree = lxml_html.fromstring(page, parser=lxml_html.HTMLParser(encoding="utf-8"))
    columns: dict[int, str] | None = None
    heading = ""
    rows: list[dict] = []
    for tr in tree.iter("tr"):
        cells, at = [], 0
        for td in tr.xpath("./td|./th"):
            try:
                span = max(1, int(td.get("colspan") or 1))
            except ValueError:
                span = 1
            text = " ".join(td.text_content().split())
            if text:
                cells.append((at, span, text))
            at += span
        if not cells:
            continue
        texts = [text for _, _, text in cells]
        if columns is None:
            if set(headings) <= set(texts):
                columns = {start: text for start, _, text in cells}
            continue
        if texts == list(columns.values()):
            continue
        if len(cells) == 1:
            start, span, text = cells[0]
            if sum(1 for c in columns if start <= c < start + span) > 1:
                heading = text
                continue
        row = {columns[start]: text for start, _, text in cells if start in columns}
        if row:
            rows.append({**row, "heading": heading})
    return rows


def _reading_order(printed: str) -> tuple[str, str]:
    """("Given Surname Suffix", "Surname") for a "Surname, Given Suffix"
    name -- "Doe, John J. Jr." reads "John J. Doe Jr."."""
    surname_part, _, given = printed.partition(",")
    given, suffix = _SUFFIX_RE.subn("", clean_display_name(given))
    tail = _SUFFIX_RE.search(clean_display_name(printed.partition(",")[2]))
    return (
        f"{given.strip()} {surname_part.strip()}" + (f" {tail.group(1)}" if suffix else ""),
        surname_part,
    )


def _unwrap(rows: list[dict], columns: list[str]) -> list[dict]:
    """Rows with each wrapped tail (a row holding text only in `columns`)
    joined onto the row above it, which it continues."""
    wrapped = set(columns)
    out: list[dict] = []
    for row in rows:
        filled = {k for k, v in row.items() if str(v or "").strip()}
        if out and filled and filled <= wrapped:
            last = dict(out[-1])
            for k in filled:
                last[k] = f"{str(last.get(k) or '').strip()} {str(row[k]).strip()}".strip()
            out[-1] = last
            continue
        if filled:
            out.append(row)
    return out


def _party_columns(fmt: dict) -> list[str]:
    columns = fmt["party_column"]
    return list(columns) if isinstance(columns, list) else [columns]


def _headings(fmt: dict) -> list[str]:
    headings = [fmt["office_column"], _party_columns(fmt)[0], *fmt["name_columns"]]
    if fmt.get("district_column"):
        headings.append(fmt["district_column"])
    return headings


def _rows(payload: bytes, url: str, fmt: dict) -> list[dict] | None:
    if fmt.get("json_rows"):
        # A portal's search API: the rows are the list at this path, each
        # an object read like a spreadsheet row (true/false and numbers as
        # their text, null as blank). Anything else -- an empty body is how
        # Arkansas's refuses a request -- is a failed read, never an empty
        # list.
        try:
            data = json.loads(payload)
        except ValueError:
            return None
        found = _json_path(data, fmt["json_rows"])
        if not isinstance(found, list):
            return None
        if fmt.get("json_total") and _json_path(data, fmt["json_total"]) != len(found):
            # The API pages, and one page did not hold the whole list: half
            # a ballot would publish its unread offices as absent.
            logger.warning("certified list %s returned %d of %s rows", url, len(found),
                           _json_path(data, fmt["json_total"]))
            return None
        return [
            {k: "" if v is None else str(v) for k, v in row.items()}
            for row in found if isinstance(row, dict)
        ]
    if payload.lstrip()[:1] == b"<" or payload.lstrip()[:4] == b"\xef\xbb\xbf<":
        if fmt.get("outline_rows"):
            return outline_rows(payload)
        if fmt.get("report_grid"):
            return report_grid_rows(payload, [c for c in _headings(fmt) if c != fmt["office_column"]])
        if fmt.get("html_headings"):
            return _html_rows(payload, {})
        return html_table_rows(payload, _headings(fmt))
    if payload[:5] == b"%PDF-":
        headings = _headings(fmt)
        try:
            with pdfplumber.open(io.BytesIO(payload)) as pdf:
                if fmt.get("ruled_columns"):
                    return ruled_table_rows(
                        [(page.extract_words(), page.rects) for page in pdf.pages], fmt["ruled_columns"],
                    )
                return pdf_table_rows([page.extract_words() for page in pdf.pages], headings)
        except Exception:
            logger.exception("certified list PDF %s failed to parse", url)
            return None
    if payload[:2] == b"PK":  # an xlsx workbook is a zip
        return _xlsx_rows(payload)
    # Anything else is CSV, whatever the address ends in (Hawaii's export
    # is served from an .aspx page).
    try:
        return list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    except (UnicodeDecodeError, csv.Error):
        return None


def parse_certified_rows(
    rows: list[dict], fmt: dict, state_offices: bool = False, judicial: bool = False,
) -> list[dict]:
    """Federal candidate records from the list's rows — and, with
    `state_offices`, the state's executive and legislative ones too; with
    `judicial`, its elected judgeships (read only from a label the other
    gates refuse, and only for a state whose entry opts in with
    judicial_offices -- see parse_judicial_office).

    A state-office record keeps the whole printed name in `last_name`
    (there is no FEC surname to match it against; see
    _sync_statewide_nominees) and a legislative one carries its `seat`.
    A party the shared codes cannot name is kept under OTHER_PARTY with
    the list's own printing as `party_label` (ballot_list_party): the
    candidate is on the ballot either way, and dropping them would be the
    worse error. A row whose party column is blank (a non-partisan
    contest) has party "" -- no party printed."""
    codes = {" ".join(str(k).split()).upper(): v for k, v in (fmt.get("office_codes") or {}).items()}
    by_label = bool(fmt.get("office_parse"))
    statuses = {str(v).strip().upper() for v in fmt.get("status_values") or []}
    exclude = {col: str(val).strip().upper() for col, val in (fmt.get("exclude") or {}).items()}
    exclude_re = {col: re.compile(rx) for col, rx in (fmt.get("exclude_regex") or {}).items()}
    fill_down = bool(fmt.get("office_fill_down"))
    state_codes = {
        " ".join(str(k).split()).upper(): v for k, v in (fmt.get("state_office_codes") or {}).items()
    }
    mate_columns = fmt.get("running_mate_columns") or []
    party_names = {" ".join(str(k).split()).upper(): v for k, v in (fmt.get("party_names") or {}).items()}
    if fmt.get("wrapped_columns"):
        rows = _unwrap(rows, fmt["wrapped_columns"])
    carried = ""
    records: dict[tuple, dict] = {}
    for row in rows:
        if statuses and str(row.get(fmt["status_column"]) or "").strip().upper() not in statuses:
            continue
        if any(str(row.get(col) or "").strip().upper() == val for col, val in exclude.items()):
            continue
        if any(rx.search(str(row.get(col) or "")) for col, rx in exclude_re.items()):
            continue
        label = " ".join(str(row.get(fmt["office_column"]) or "").split())
        if fmt.get("office_parts"):
            # [[column, prefix], ...]: the office spread over several
            # fields, each printed after its prefix when it has a value
            # (Idaho: "State Representative" + "District 1" + "Seat A").
            label = " ".join(
                f"{prefix}{' '.join(str(row.get(col) or '').split())}"
                for col, prefix in fmt["office_parts"] if str(row.get(col) or "").strip()
            )
        if fmt.get("office_regex"):
            found = re.search(fmt["office_regex"], label)
            label = found.group(1).strip() if found else label
        party_label = next(
            (str(row[col]).strip() for col in _party_columns(fmt) if str(row.get(col) or "").strip()), "",
        )
        if fmt.get("party_regex"):
            found = re.search(fmt["party_regex"], party_label)
            party_label = found.group(1).strip() if found else ""
        party_label = party_names.get(" ".join(party_label.split()).upper(), party_label)
        printed = " ".join(str(row.get(col) or "").strip() for col in fmt["name_columns"]).strip()
        if fmt.get("name_regex"):
            found = re.search(fmt["name_regex"], printed)
            printed = found.group(1).strip() if found else ""
        printed_last = ""
        if fmt.get("name_last_first") and "," in printed:
            # A joint ticket prints both names, slash-separated ("Bronson,
            # Dave / Church, Josh" -- Alaska's Governor and Lieutenant
            # Governor), each in the same last-first order.
            # Annotations go first: a ticket's registrations share one
            # parenthesis ("(Registered Democrat / Nonpartisan)").
            parts = [_reading_order(part) for part in clean_display_name(printed).split("/")]
            # Joined " and ", the way every other ticket on the page reads.
            printed = " and ".join(name for name, _ in parts)
            printed_last = parts[0][1]
        display = clean_display_name(printed)
        if fill_down:
            # Only a candidate row sets the office carried to the rows below
            # it: a page footer never does, and a blank-office row under
            # "Governor" stays a governor's row rather than inheriting the
            # last congressional district.
            if not (party_label and display):
                continue
            label = label or carried
            carried = label
        if by_label:
            if fmt.get("district_column"):
                label = f"{label} {' '.join(str(row.get(fmt['district_column']) or '').split())}".strip()
            parsed = parse_office(label)
            if parsed is None:
                if state_offices and display:
                    state_record = _state_office_record(label, party_label, _with_mate(display, row, mate_columns))
                    if state_record is None and judicial:
                        state_record = _judicial_record(label, party_label, display)
                    if state_record is not None:
                        records[(state_record["office"], state_record["district"],
                                 state_record.get("seat"), display.lower())] = state_record
                continue
            office, district = parsed
        else:
            office = codes.get(label.upper())
            if office not in ("S", "H"):
                spelled = state_codes.get(label.upper())
                if state_offices and spelled and display:
                    digits = re.search(r"\d+", str(row.get(fmt.get("district_column") or "") or ""))
                    state_label = f"{spelled} District {digits.group()}" if digits else spelled
                    state_record = _state_office_record(
                        state_label, party_label, _with_mate(display, row, mate_columns),
                    )
                    if state_record is not None:
                        records[(state_record["office"], state_record["district"],
                                 state_record.get("seat"), display.lower())] = state_record
                continue
            district = None
            if office == "H":
                digits = "".join(ch for ch in str(row.get(fmt["district_column"]) or "") if ch.isdigit())
                district = int(digits) if digits else 0
        if fmt.get("surname_column"):
            last = str(row.get(fmt["surname_column"]) or "").strip()
        elif printed_last:
            last = clean_display_name(printed_last)  # the whole surname: "LEGER FERNANDEZ"
        else:
            last = surname(display) or ""
        if not last:
            continue
        records[(office, district, display.lower())] = {
            "office": office,
            "district": district,
            # A certified ballot: "Independent"/"Unenrolled" is an entry.
            "party": normalize_party(party_label, ballot_list=True),
            "last_name": last,
            "display_name": display,
            "party_label": party_label,
        }
    return list(records.values())


def _judicial_record(label: str, party_label: str, display: str) -> dict | None:
    """A judgeship's record for one list row, or None for anything the
    judicial gate refuses (a clerk of court, a district attorney)."""
    parsed = parse_judicial_office(label)
    if parsed is None:
        return None
    court, district, seat = parsed
    party, printed = ballot_list_party(party_label) or ("", None)
    record = {"office": court, "district": district, "seat": seat, "party": party, "last_name": display}
    if printed:
        record["party_label"] = printed
    return record


def _with_mate(display: str, row: dict, columns: list[str]) -> str:
    """`display` with the row's running mate appended, when it names one."""
    mate = clean_display_name(" ".join(str(row.get(col) or "").strip() for col in columns))
    return f"{display} and {mate}" if mate else display


def _state_office_record(label: str, party_label: str, display: str) -> dict | None:
    """A statewide-executive or legislative record for one list row, or
    None for anything else on the list (county offices, judges,
    commissions seated by district) — both gates refuse by default."""
    party, printed = ballot_list_party(party_label) or ("", None)
    statewide = parse_statewide_office(label)
    if statewide is not None:
        office, district = statewide
        record = {"office": office, "district": district, "party": party, "last_name": display}
    else:
        seat = parse_state_leg_office(label)
        if seat is None:
            return None
        chamber, district, seat_id = seat
        record = {"office": chamber, "district": district, "party": party, "last_name": display}
        if seat_id is not None:
            record["seat"] = seat_id
    if printed:
        record["party_label"] = printed
    return record


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    discovery = source.get("discovery") or {}
    fmt = source.get("format") or {}
    link_regexes = discovery.get("link_regexes") or ([discovery["link_regex"]] if discovery.get("link_regex") else [])
    if discovery.get("url") and not (discovery.get("year_regex") or discovery.get("election")):
        logger.warning("%s certified_table discovery.url needs year_regex or election", state)
        return None
    if not discovery.get("url") and (not (discovery.get("page_url") or discovery.get("index_url")) or not link_regexes):
        logger.warning("%s certified_table source needs discovery.page_url and link_regex", state)
        return None
    needed = ("office_column", "party_column", "name_columns") + (
        () if fmt.get("office_parse") else ("office_codes", "district_column")
    )
    missing = [k for k in needed if not fmt.get(k)]
    if missing:
        logger.warning("%s certified_table format is missing %s", state, missing)
        return None
    if source.get("statewide_offices") and not (fmt.get("office_parse") or fmt.get("state_office_codes")):
        # State offices are read from the office label; an exact code map
        # names only the federal ones, so the opt-in would claim a reading
        # that cannot happen.
        logger.warning("%s certified_table statewide_offices needs format.office_parse or state_office_codes", state)
        return None

    if discovery.get("url"):
        url = discovery["url"].replace("{year}", str(year))
        election = None
        if discovery.get("election"):
            election = await _election_id(client, discovery["election"], year, state)
            if election is None:
                return None
            if not election:
                return not_yet(year, state, "the portal lists no election for this year's general")
        payloads = await _download(client, url, discovery, year, state, election)
        if payloads is None:
            return None
        if not payloads:
            return not_yet(year, state, "the page does not name this year's election")
        return _records(
            state, [row for p in payloads for row in (_rows(p, url, fmt) or [])], fmt,
            bool(source.get("statewide_offices")), year, bool(source.get("judicial_offices")),
        )

    page_url = discovery.get("page_url")
    if discovery.get("index_url") and discovery.get("index_regex"):
        page_url = await discover_certification_link(
            client, _rate_limiter, discovery["index_url"], discovery["index_regex"], year, state,
        )
        if page_url is None:
            return None
    urls: list[str] = []
    if discovery.get("every_link"):
        page_url = page_url.replace("{year}", str(year))
        page = await fetch_text_with_retry(client, _rate_limiter, page_url, f"{state} candidate list index")
        if page is None:
            return None
        for link_regex in link_regexes:
            urls += sorted({urljoin(page_url, m.group(1))
                            for m in re.finditer(link_regex.replace("{year}", str(year)), page)})
        if not urls:
            logger.info("%s candidate list index links no list for %d", state, year)
            return None
    else:
        for link_regex in link_regexes:
            url = await discover_certification_link(client, _rate_limiter, page_url, link_regex, year, state)
            if url is None:
                return None
            urls.append(url)
    rows: list[dict] = []
    for url in urls:
        # Every file is required: a Senate list without its House list is
        # half a ballot, and half a ballot would unconfirm real nominees.
        payloads = await _download(client, url, discovery, year, state)
        if payloads is None:
            return None
        if not payloads:
            # Not published for this year yet: every file is required.
            return not_yet(year, state, f"{url} does not name this year's election")
        for payload in payloads:
            part = _rows(payload, url, fmt)
            if not part:
                logger.warning("%s certified list %s did not parse", state, url)
                return None
            rows += part
    return _records(
        state, rows, fmt, bool(source.get("statewide_offices")), year, bool(source.get("judicial_offices")),
    )


def _json_path(data, path: str):
    """The value at a dotted `path` ("data.candidates") in parsed JSON, or
    None where any step is missing."""
    for key in path.split("."):
        data = data.get(key) if isinstance(data, dict) else None
    return data


def _filled(value, election: str | None):
    """`value` (a JSON body from the config) with "{election}" replaced by
    the election id the portal's own election list gave this run."""
    if isinstance(value, dict):
        return {k: _filled(v, election) for k, v in value.items()}
    if isinstance(value, str) and election is not None:
        return value.replace("{election}", election)
    return value


async def _request(
    client: httpx.AsyncClient, url: str, conf: dict, election: str | None, label: str,
) -> bytes | None:
    """GET `url`, or POST conf["json_body"] to it as JSON when one is
    configured (a portal's search API) -- with conf["headers"] added to the
    browser's own."""
    headers = {**BROWSER_HEADERS, **(conf.get("headers") or {})}
    if conf.get("json_body") is None:
        return await fetch_bytes_with_retry(client, _rate_limiter, url, label, headers=headers)
    resp = await fetch_with_retry(
        client, _rate_limiter, "POST", url, json=_filled(conf["json_body"], election),
        headers=headers, log_label=label,
    )
    return resp.content if resp is not None else None


async def _election_id(client: httpx.AsyncClient, conf: dict, year: int, state: str) -> str | None:
    """The portal's id for this year's general election, read from its own
    election list (Idaho's candidate portal keys every search by an id
    that changes each election). None when the list could not be read or
    names more than one match; "" when it names none -- not created yet,
    which is not a failed fetch."""
    payload = await _request(client, conf["url"], conf, None, f"{state} candidate portal elections {year}")
    if payload is None:
        return None
    try:
        rows = _json_path(json.loads(payload), conf["rows"])
    except ValueError:
        rows = None
    if not isinstance(rows, list):
        logger.warning("%s candidate portal's election list did not parse", state)
        return None
    pattern = re.compile(conf["regex"].replace("{year}", str(year)))
    ids = {str(row.get(conf["value"])) for row in rows
           if isinstance(row, dict) and pattern.search(str(row.get(conf["name"]) or ""))}
    if len(ids) > 1:
        # Guessing which of two is this year's ballot is the one wrong answer.
        logger.warning("%s candidate portal lists %d elections matching %s", state, len(ids), conf["regex"])
        return None
    return ids.pop() if ids else ""


def _form_fields(page: bytes, button: str | None) -> dict | None:
    """The page's form as a browser posts it back: every hidden and text
    input, every dropdown at its current choice, and the button (when one
    is pressed) with its own value. None when the page has no form."""
    forms = lxml_html.fromstring(page).xpath("//form")
    if not forms:
        return None
    form = forms[0]
    base = {
        field.get("name"): field.get("value") or ""
        for field in form.xpath(".//input[@name]")
        if (field.get("type") or "text").lower() in ("hidden", "text")
    }
    for select in form.xpath(".//select[@name]"):
        current = select.xpath("./option[@selected]") or select.xpath("./option")
        if current:
            base[select.get("name")] = current[0].get("value") or ""
    if button:
        pressed = form.xpath(f'.//input[@name="{button}"]')
        base[button] = (pressed[0].get("value") or "") if pressed else ""
    return base


async def _download(
    client: httpx.AsyncClient, url: str, discovery: dict, year: int, state: str,
    election: str | None = None,
) -> list[bytes] | None:
    """The list's bytes: the file itself, or — with form_button — what the
    page's own button returns, once per `form_select` choice. None when any
    fetch fails or no choice is on offer; [] when the page does not name
    this year's election yet -- a list not published, which is the normal
    state for most of a cycle and not a failed fetch."""
    label = f"{state} certified list {year}"
    payload = await _request(client, url, discovery, election, label)
    if payload is None:
        return None
    year_regex = discovery.get("year_regex")
    if year_regex:
        # year_url: the list itself never names its election (a bare JSON
        # feed -- Arkansas's candidate search), so the page that publishes
        # it must.
        page = payload
        if discovery.get("year_url"):
            page = await fetch_bytes_with_retry(client, _rate_limiter, discovery["year_url"], f"{label} page")
            if page is None:
                return None
        if not re.search(year_regex.replace("{year}", str(year)), page.decode("utf-8", "replace")):
            logger.info("%s candidate list does not show the %d election yet", state, year)
            return []
    if discovery.get("final_path"):
        # The portal says itself whether this is the final list (Idaho's
        # isFinalList). Until it does, the list is not the ballot.
        try:
            final = _json_path(json.loads(payload), discovery["final_path"])
        except ValueError:
            logger.warning("%s candidate list did not parse", state)
            return None
        if final is not True:
            logger.info("%s candidate list is not final yet", state)
            return []
    if discovery.get("next_page_regex"):
        return await _pages(client, url, payload, discovery["next_page_regex"], year, state)
    button = discovery.get("form_button")
    if not button:
        return [payload]
    base = _form_fields(payload, button)
    if base is None:
        logger.warning("%s candidate list page has no form to post", state)
        return None
    form = lxml_html.fromstring(payload).xpath("//form")[0]

    posts: list[tuple[str | None, dict]] = []
    for field, texts in (discovery.get("form_select") or {}).items():
        offered = {
            " ".join(option.text_content().split()): option.get("value") or ""
            for option in form.xpath(f'.//select[@name="{field}"]/option')
        }
        # Each choice is a pattern the option's whole text must match, so
        # a label carrying the election's date can be named by its year
        # (Montana's "FEDERAL GENERAL 2026 (11/03/2026) (General)").
        for pattern in texts:
            rx = re.compile(pattern.replace("{year}", str(year)))
            posts += [(field, {**base, field: value}) for text, value in offered.items() if rx.fullmatch(text)]
    if discovery.get("form_select") and not posts:
        logger.warning("%s candidate list offers none of the configured choices", state)
        return None
    payloads = []
    for field, data in posts or [(None, base)]:
        post_url = url
        if field and discovery.get("form_select_postback"):
            # The dropdown posts the page back the moment it changes
            # (ASP.NET AutoPostBack -- Montana's election picker), and the
            # button on a page loaded with the old choice still exports the
            # old list. So choose first, as the dropdown does, then press
            # the button on the page that comes back.
            choose = {k: v for k, v in data.items() if k != button}
            choose["__EVENTTARGET"] = field
            resp = await fetch_with_retry(
                client, _rate_limiter, "POST", url, data=choose, headers=BROWSER_HEADERS,
                log_label=f"{label} choice",
            )
            if resp is None:
                return None
            chosen = _form_fields(resp.content, button)
            if chosen is None or chosen.get(field) != data[field]:
                logger.warning("%s candidate list did not switch to the chosen %s", state, field)
                return None
            data, post_url = chosen, str(resp.url)
        resp = await fetch_with_retry(
            client, _rate_limiter, "POST", post_url, data=data, headers=BROWSER_HEADERS,
            log_label=f"{state} candidate list {year}",
        )
        if resp is None:
            return None
        payloads.append(resp.content)
    return payloads


# A ceiling, not an expectation: Alaska's 2026 list is 3 pages. A link
# loop (a "next" that points back) must end rather than spin.
_MAX_PAGES = 30


async def _pages(
    client: httpx.AsyncClient, url: str, first: bytes, next_regex: str, year: int, state: str,
) -> list[bytes] | None:
    """Every page of a paged list, following each page's "next" link. None
    when a page fails to load, or the chain does not end: a list read only
    in part would publish its unread offices as absent."""
    payloads, seen, current, page_url = [first], {url}, first, url
    while True:
        m = re.search(next_regex, current.decode("utf-8", "replace"))
        if not m:
            return payloads
        page_url = urljoin(page_url, html.unescape(m.group(1)))
        if page_url in seen or len(payloads) >= _MAX_PAGES:
            logger.warning("%s candidate list pages do not end (%s)", state, page_url)
            return None
        seen.add(page_url)
        current = await fetch_bytes_with_retry(client, _rate_limiter, page_url, f"{state} certified list {year} page")
        if current is None:
            return None
        payloads.append(current)


def _records(
    state: str, rows: list[dict], fmt: dict, state_offices: bool = False, year: int | None = None,
    judicial: bool = False,
) -> list[dict] | None:
    over = _overfilled(rows, fmt)
    if over == _NO_SEAT_COUNT:
        logger.warning("%s certified list prints no seat count where one was configured -- layout changed?", state)
        return None
    if over:
        # A list that still holds more of one party's candidates for an
        # office than it has seats is not the November ballot yet -- it is
        # the filings before a primary settles them. Not yet, not broken --
        # until the ballot must be final (not_yet).
        return not_yet(year, state, over) if year is not None else []
    records = parse_certified_rows(rows, fmt, state_offices, judicial)
    federal = [r for r in records if r["office"] in ("S", "H")]
    if not federal:
        logger.warning("%s certified list has no federal candidate — columns or codes changed?", state)
        return None
    logger.info(
        "%s certified list: %d federal candidates, %d state-office candidates",
        state, len(federal), len(records) - len(federal),
    )
    missing = _slate_gaps(records, fmt) if state_offices else []
    if year is not None and not (missing and in_ballot_window(year)):
        from app.ops_alerts import resolve_ops_alert

        resolve_ops_alert(f"slate-incomplete-{state}-{year}")
    if missing:
        if year is not None and in_ballot_window(year):
            # The ballot is mailed and a party's slate is still short: not a
            # convention yet to come any more, but something to look at --
            # the state offices stay held (never published half-filled),
            # and someone is told, once a day.
            logger.warning("%s certified list's state offices still wait for %s", state, "; ".join(missing))
            try:
                from app.ops_alerts import send_ops_alert

                send_ops_alert(
                    f"{state} state offices held: party slate incomplete on the certified list",
                    f"{state}'s {year} certified list still lacks: {'; '.join(missing)}. Ballots are final, "
                    "so the statewide and legislative sections stay unpublished until the list is complete "
                    "or format.slate_complete is revisited.",
                    dedupe_key=f"slate-incomplete-{state}-{year}-{utcnow().date().isoformat()}",
                    condition=f"slate-incomplete-{state}-{year}",
                )
            except Exception:
                logger.exception("Could not send the %s slate-incomplete ops alert", state)
        else:
            logger.info("%s certified list's state offices wait for %s", state, "; ".join(missing))
        return federal_only(records)
    return records


_NO_SEAT_COUNT = "no seat count"


def _overfilled(rows: list[dict], fmt: dict) -> str | None:
    """The first office on the list holding more candidates of one party
    than it has seats, or None. Needs format.seats_regex, whose first group
    is the seat count printed in the office cell (Michigan's "(1) Position",
    "(2) Positions"). Read only where a gate reads the office (a judgeship
    is non-partisan), only for rows the status filter keeps, and never for
    independents, several of whom may run for one seat.

    _NO_SEAT_COUNT when an office the gates read prints no seat count at
    all: the layout changed, and a check that quietly turned itself off
    would let a pre-primary filing list through as the certified ballot."""
    seats_re = fmt.get("seats_regex")
    if not seats_re:
        return None
    statuses = {str(v).strip().upper() for v in fmt.get("status_values") or []}
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        if statuses and str(row.get(fmt.get("status_column") or "") or "").strip().upper() not in statuses:
            continue
        cell = " ".join(str(row.get(fmt["office_column"]) or "").split())
        label = cell
        if fmt.get("office_regex"):
            found = re.search(fmt["office_regex"], cell)
            label = found.group(1).strip() if found else cell
        if not (parse_office(label) or parse_statewide_office(label) or parse_state_leg_office(label)):
            continue
        seats = re.search(seats_re, cell)
        if not seats:
            return _NO_SEAT_COUNT
        party = " ".join(
            str(row[col]).strip() for col in _party_columns(fmt) if str(row.get(col) or "").strip()
        ).upper()
        if not party or normalize_party(party, ballot_list=True) == "I":
            continue
        key = (cell, party)
        counts[key] = counts.get(key, 0) + 1
        if counts[key] > int(seats.group(1)):
            return f"{party} x{counts[key]} for {cell}"
    return None


def _slate_gaps(records: list[dict], fmt: dict) -> list[str]:
    """What format.slate_complete says the list still lacks. Michigan's
    parties nominate their Secretary of State and Attorney General at
    conventions held weeks after the primary (2026: August 24 and 31), and
    until they do the list names the Governor's ticket without them --
    publishing it then would record those offices as the whole ballot.

    Read from the list itself, never a date: a party with a candidate for
    `office` that ALREADY lists one of the `requires` offices -- it holds
    conventions for them -- must list all of them. A party that lists none
    (an independent governor's petition ticket, a minor party that fields
    only a governor) is never waited for: it may never field one, and
    waiting would hold every state office back all cycle. Independents
    and non-partisan rows have no slate at all."""
    rule = fmt.get("slate_complete") or {}
    if not rule.get("office"):
        return []
    requires = list(rule.get("requires") or [])

    def key(r):
        return (r.get("party"), r.get("party_label"))
    have: dict[str, set] = {}
    for r in records:
        have.setdefault(r["office"], set()).add(key(r))
    gaps = []
    for party in sorted(have.get(rule["office"], set()), key=str):
        if party[0] in ("I", NONPARTISAN, "", None):
            continue
        listed = [office for office in requires if party in have.get(office, set())]
        if not listed:
            continue
        gaps += [f"{party[1] or party[0]} {office}" for office in requires if office not in listed]
    return gaps
