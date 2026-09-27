"""A state's general-election candidate list printed as a PDF with no
column headings: each office is a heading line, each candidate a line
under it, and a later change of status printed on the candidate's next line.

Illinois is the live case. Its State Board of Elections prints the filed
candidates for an election as a "Website Candidate List":

    1ST CONGRESS                                   <- office heading
    DEMOCRATIC    Jonathan L. Jackson   10/31/2025 10:00 AM
    ...
    INDEPENDENT   Mayra Macias          5/26/2026 5:00 PM
                  8445 S Kostner Ave    REMOVED 7/21/2026   <- off the ballot

so the party is the text left of `name_x`, the name the text between
`name_x` and `date_x`, a line with nothing left of `name_x` is a heading
when parse_office recognises it, and a candidate whose next line matches
`removed_regex` is dropped. Address lines are never read.

The list's address carries the election id, which the state's own home
page links as its "Next Election"; `id_regex` finds it there each run, and
`year_regex` must find this year's general election in the PDF before a
row is read. Every URL in `url_templates` is required (one per office
group — Illinois prints the House and the Senate separately).
"""

import logging
import re
from io import BytesIO

import httpx
import pdfplumber

from app.pipeline.fetch.ballot_measure_pdf_geometry import rows as clustered_rows
from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_bytes_with_retry, fetch_text_with_retry
from app.pipeline.fetch.state_candidates_common import clean_display_name, normalize_party, parse_office, surname
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

_DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")


def parse_grouped_list(pages: list[list[dict]], fmt: dict) -> list[dict]:
    """Federal candidates from each page's words (pdfplumber extract_words)."""
    name_x, date_x = float(fmt["name_x"]), float(fmt["date_x"])
    removed = re.compile(fmt["removed_regex"], re.IGNORECASE)
    lines: list[list[dict]] = []
    for words in pages:
        clustered = clustered_rows(words)
        lines += [sorted(clustered[k], key=lambda w: w["x0"]) for k in sorted(clustered)]

    records: list[dict] = []
    office: tuple[str, int | None] | None = None
    for i, line in enumerate(lines):
        party_text = " ".join(w["text"] for w in line if w["x0"] < name_x - 1)
        if not party_text:
            # Only a line that names an office moves to it. A page's own
            # header ("ILLINOIS STATE BOARD OF ELECTIONS") and an address
            # line leave the office as it was, so a district that runs onto
            # the next page keeps its candidates; each list holds one office
            # group, so there is no other office to fall into.
            heading = parse_office(" ".join(w["text"] for w in line))
            if heading is not None:
                office = heading
            continue
        # A candidate's line carries its filing date in the date column; a
        # page's own header ("9/26/2026 5:21PM WEBSITE CANDIDATE LIST")
        # has text on the left too, but never there.
        if office is None or not any(
            w["x0"] >= date_x - 1 and _DATE_RE.match(w["text"]) for w in line
        ):
            continue
        name = clean_display_name(" ".join(w["text"] for w in line if name_x - 1 <= w["x0"] < date_x - 1))
        last = surname(name)
        if len(name.split()) < 2 or not last:
            continue
        following = lines[i + 1] if i + 1 < len(lines) else []
        if following and not any(w["x0"] < name_x - 1 for w in following) and removed.search(
            " ".join(w["text"] for w in following)
        ):
            continue
        records.append({
            "office": office[0],
            "district": office[1],
            "party": normalize_party(party_text, ballot_list=True),
            "last_name": last,
            "display_name": name,
            "party_label": party_text,
        })
    return records


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    discovery = source.get("discovery") or {}
    fmt = source.get("format") or {}
    needed = [k for k in ("page_url", "id_regex", "url_templates", "year_regex") if not discovery.get(k)]
    needed += [k for k in ("name_x", "date_x", "removed_regex") if not fmt.get(k)]
    if needed:
        logger.warning("%s grouped_list_pdf source is missing %s", state, needed)
        return None

    page = await fetch_text_with_retry(client, _rate_limiter, discovery["page_url"], f"{state} election page")
    if page is None:
        return None
    ids = set(re.findall(discovery["id_regex"], page))
    if len(ids) != 1:
        logger.info("%s election page links %d election ids for the list", state, len(ids))
        return None
    election_id = ids.pop()

    year_re = re.compile(discovery["year_regex"].replace("{year}", str(year)))
    records: list[dict] = []
    for template in discovery["url_templates"]:
        url = template.replace("{id}", election_id)
        payload = await fetch_bytes_with_retry(
            client, _rate_limiter, url, f"{state} candidate list {year}", headers=BROWSER_HEADERS,
        )
        if payload is None or payload[:5] != b"%PDF-":
            return None
        try:
            with BytesIO(payload) as buf, pdfplumber.open(buf) as pdf:
                pages = [p.extract_words() for p in pdf.pages]
                text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        except Exception:
            logger.exception("%s candidate list PDF failed to parse", state)
            return None
        if not year_re.search(text):
            logger.info("%s candidate list is not for the %d general election", state, year)
            return None
        part = parse_grouped_list(pages, fmt)
        if not part:
            logger.warning("%s candidate list %s had no federal candidate", state, url)
            return None
        records += part
    logger.info("%s candidate list: %d federal candidates", state, len(records))
    return records
