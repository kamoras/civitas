"""A state's certified general-election candidate list, published as a
spreadsheet (xlsx or csv) — one row per candidate on the November ballot.

Maine is the live case, and the reason this exists. Maine's adapter read
the June primary's official results and confirmed Graham Platner as the
Democratic Senate nominee. He was: he won with 77.7%. He then withdrew on
2026-07-10 and the party nominated Troy Jackson at a convention on July
25. Primary results cannot see that, and they never will — the same way
South Carolina's results named Lindsey Graham after a special primary had
replaced him. The Secretary of State's "2026 General Candidate List" is
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
  discovery.form_button              the list is the page's own "Export to CSV"
                                     button (Hawaii's candidate report): the
                                     page's form is posted back with that
                                     button, exactly as a visitor's click does
  format.name_last_first             names are printed "BERNING, Nathan M."

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
import io
import logging
import re

import httpx
import pdfplumber
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import rows as _clustered_rows
from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_bytes_with_retry, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import (
    clean_display_name,
    discover_certification_link,
    normalize_party,
    parse_office,
    surname,
)
from app.pipeline.fetch.state_candidates_tabular import _xlsx_rows
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)


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
    twice; both hold the office)."""
    tree = lxml_html.fromstring(page)
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


def _headings(fmt: dict) -> list[str]:
    headings = [fmt["office_column"], fmt["party_column"], *fmt["name_columns"]]
    if fmt.get("district_column"):
        headings.append(fmt["district_column"])
    return headings


def _rows(payload: bytes, url: str, fmt: dict) -> list[dict] | None:
    if payload.lstrip()[:1] == b"<":
        return html_table_rows(payload, _headings(fmt))
    if payload[:5] == b"%PDF-":
        headings = _headings(fmt)
        try:
            with pdfplumber.open(io.BytesIO(payload)) as pdf:
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


def parse_certified_rows(rows: list[dict], fmt: dict) -> list[dict]:
    """Federal candidate records from the list's rows."""
    codes = {" ".join(str(k).split()).upper(): v for k, v in (fmt.get("office_codes") or {}).items()}
    by_label = bool(fmt.get("office_parse"))
    statuses = {str(v).strip().upper() for v in fmt.get("status_values") or []}
    exclude = {col: str(val).strip().upper() for col, val in (fmt.get("exclude") or {}).items()}
    fill_down = bool(fmt.get("office_fill_down"))
    carried = ""
    records: dict[tuple, dict] = {}
    for row in rows:
        if statuses and str(row.get(fmt["status_column"]) or "").strip().upper() not in statuses:
            continue
        if any(str(row.get(col) or "").strip().upper() == val for col, val in exclude.items()):
            continue
        label = " ".join(str(row.get(fmt["office_column"]) or "").split())
        party_label = str(row.get(fmt["party_column"]) or "").strip()
        printed = " ".join(str(row.get(col) or "").strip() for col in fmt["name_columns"]).strip()
        printed_last = ""
        if fmt.get("name_last_first") and "," in printed:
            printed_last, _, given = printed.partition(",")
            printed = f"{given.strip()} {printed_last.strip()}"
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
                continue
            office, district = parsed
        else:
            office = codes.get(label.upper())
            if office not in ("S", "H"):
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


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    discovery = source.get("discovery") or {}
    fmt = source.get("format") or {}
    link_regexes = discovery.get("link_regexes") or ([discovery["link_regex"]] if discovery.get("link_regex") else [])
    if discovery.get("url") and not discovery.get("year_regex"):
        logger.warning("%s certified_table discovery.url needs year_regex", state)
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

    if discovery.get("url"):
        payload = await _download(client, discovery["url"], discovery, year, state)
        if payload is None:
            return None
        return _records(state, _rows(payload, discovery["url"], fmt) or [], fmt)

    page_url = discovery.get("page_url")
    if discovery.get("index_url") and discovery.get("index_regex"):
        page_url = await discover_certification_link(
            client, _rate_limiter, discovery["index_url"], discovery["index_regex"], year, state,
        )
        if page_url is None:
            return None
    rows: list[dict] = []
    for link_regex in link_regexes:
        # Every file is required: a Senate list without its House list is
        # half a ballot, and half a ballot would unconfirm real nominees.
        url = await discover_certification_link(client, _rate_limiter, page_url, link_regex, year, state)
        if url is None:
            return None
        payload = await _download(client, url, discovery, year, state)
        if payload is None:
            return None
        part = _rows(payload, url, fmt)
        if not part:
            logger.warning("%s certified list %s did not parse", state, url)
            return None
        rows += part
    return _records(state, rows, fmt)


async def _download(
    client: httpx.AsyncClient, url: str, discovery: dict, year: int, state: str,
) -> bytes | None:
    """The list's bytes: the file itself, or — with form_button — what the
    page's own export button returns. None when either fetch fails or the
    page does not name this year's election."""
    payload = await fetch_bytes_with_retry(client, _rate_limiter, url, f"{state} certified list {year}")
    if payload is None:
        return None
    year_regex = discovery.get("year_regex")
    if year_regex and not re.search(year_regex.replace("{year}", str(year)), payload.decode("utf-8", "replace")):
        logger.info("%s candidate list does not show the %d election yet", state, year)
        return None
    if not discovery.get("form_button"):
        return payload
    forms = lxml_html.fromstring(payload).xpath("//form")
    if not forms:
        logger.warning("%s candidate list page has no form to export from", state)
        return None
    data = {
        field.get("name"): field.get("value") or ""
        for field in forms[0].xpath(".//input[@name]")
        if (field.get("type") or "text").lower() in ("hidden", "text")
    }
    data[discovery["form_button"]] = ""
    resp = await fetch_with_retry(
        client, _rate_limiter, "POST", url, data=data, headers=BROWSER_HEADERS,
        log_label=f"{state} candidate list export {year}",
    )
    return resp.content if resp is not None else None


def _records(state: str, rows: list[dict], fmt: dict) -> list[dict] | None:
    records = parse_certified_rows(rows, fmt)
    if not records:
        logger.warning("%s certified list has no federal candidate — columns or codes changed?", state)
        return None
    logger.info("%s certified list: %d federal candidates", state, len(records))
    return records
