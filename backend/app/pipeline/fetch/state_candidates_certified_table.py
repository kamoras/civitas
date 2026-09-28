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
  discovery.next_page_regex          the list is paged; each page's link to
                                     the next is followed until there is
                                     none (Alaska's 3 pages: its House
                                     districts run onto pages 2 and 3)
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
    normalize_party,
    parse_office,
    parse_state_leg_office,
    parse_statewide_office,
    surname,
)
from app.pipeline.fetch.state_candidates_tabular import _html_rows, _xlsx_rows
from app.pipeline.rate_limiter import RateLimiter

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


def _reading_order(printed: str) -> tuple[str, str]:
    """("Given Surname Suffix", "Surname") for a "Surname, Given Suffix"
    name -- "Sullivan, Daniel J. Jr." reads "Daniel J. Sullivan Jr."."""
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
    if payload.lstrip()[:1] == b"<":
        if fmt.get("html_headings"):
            return _html_rows(payload, {})
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


def parse_certified_rows(rows: list[dict], fmt: dict, state_offices: bool = False) -> list[dict]:
    """Federal candidate records from the list's rows — and, with
    `state_offices`, the state's executive and legislative ones too.

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
        party_label = next(
            (str(row[col]).strip() for col in _party_columns(fmt) if str(row.get(col) or "").strip()), "",
        )
        if fmt.get("party_regex"):
            found = re.search(fmt["party_regex"], party_label)
            party_label = found.group(1).strip() if found else ""
        printed = " ".join(str(row.get(col) or "").strip() for col in fmt["name_columns"]).strip()
        printed_last = ""
        if fmt.get("name_last_first") and "," in printed:
            # A joint ticket prints both names, slash-separated ("Bronson,
            # Dave / Church, Josh" -- Alaska's Governor and Lieutenant
            # Governor), each in the same last-first order.
            # Annotations go first: a ticket's registrations share one
            # parenthesis ("(Registered Democrat / Nonpartisan)").
            parts = [_reading_order(part) for part in clean_display_name(printed).split("/")]
            printed = " / ".join(name for name, _ in parts)
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
    if source.get("statewide_offices") and not (fmt.get("office_parse") or fmt.get("state_office_codes")):
        # State offices are read from the office label; an exact code map
        # names only the federal ones, so the opt-in would claim a reading
        # that cannot happen.
        logger.warning("%s certified_table statewide_offices needs format.office_parse or state_office_codes", state)
        return None

    if discovery.get("url"):
        payloads = await _download(client, discovery["url"], discovery, year, state)
        if payloads is None:
            return None
        return _records(
            state, [row for p in payloads for row in (_rows(p, discovery["url"], fmt) or [])], fmt,
            bool(source.get("statewide_offices")),
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
        for payload in payloads:
            part = _rows(payload, url, fmt)
            if not part:
                logger.warning("%s certified list %s did not parse", state, url)
                return None
            rows += part
    return _records(state, rows, fmt, bool(source.get("statewide_offices")))


async def _download(
    client: httpx.AsyncClient, url: str, discovery: dict, year: int, state: str,
) -> list[bytes] | None:
    """The list's bytes: the file itself, or — with form_button — what the
    page's own button returns, once per `form_select` choice. None when any
    fetch fails, the page does not name this year's election, or no choice
    is on offer."""
    payload = await fetch_bytes_with_retry(client, _rate_limiter, url, f"{state} certified list {year}")
    if payload is None:
        return None
    year_regex = discovery.get("year_regex")
    if year_regex and not re.search(year_regex.replace("{year}", str(year)), payload.decode("utf-8", "replace")):
        logger.info("%s candidate list does not show the %d election yet", state, year)
        return None
    if discovery.get("next_page_regex"):
        return await _pages(client, url, payload, discovery["next_page_regex"], year, state)
    button = discovery.get("form_button")
    if not button:
        return [payload]
    forms = lxml_html.fromstring(payload).xpath("//form")
    if not forms:
        logger.warning("%s candidate list page has no form to post", state)
        return None
    form = forms[0]
    # Posted back as a browser would: every hidden and text input, every
    # dropdown at its current choice, and the button with its own value.
    base = {
        field.get("name"): field.get("value") or ""
        for field in form.xpath(".//input[@name]")
        if (field.get("type") or "text").lower() in ("hidden", "text")
    }
    for select in form.xpath(".//select[@name]"):
        current = select.xpath("./option[@selected]") or select.xpath("./option")
        if current:
            base[select.get("name")] = current[0].get("value") or ""
    pressed = form.xpath(f'.//input[@name="{button}"]')
    base[button] = (pressed[0].get("value") or "") if pressed else ""

    posts = []
    for field, texts in (discovery.get("form_select") or {}).items():
        offered = {
            " ".join(option.text_content().split()): option.get("value") or ""
            for option in form.xpath(f'.//select[@name="{field}"]/option')
        }
        posts += [{**base, field: offered[text]} for text in texts if text in offered]
    if discovery.get("form_select") and not posts:
        logger.warning("%s candidate list offers none of the configured choices", state)
        return None
    payloads = []
    for data in posts or [base]:
        resp = await fetch_with_retry(
            client, _rate_limiter, "POST", url, data=data, headers=BROWSER_HEADERS,
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


def _records(state: str, rows: list[dict], fmt: dict, state_offices: bool = False) -> list[dict] | None:
    records = parse_certified_rows(rows, fmt, state_offices)
    federal = [r for r in records if r["office"] in ("S", "H")]
    if not federal:
        logger.warning("%s certified list has no federal candidate — columns or codes changed?", state)
        return None
    logger.info(
        "%s certified list: %d federal candidates, %d state-office candidates",
        state, len(federal), len(records) - len(federal),
    )
    return records
