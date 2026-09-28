"""Washington's ballot-measure strategy — the Secretary of State's
"Proposed Ballot Measure Information" page plus each measure's own
Attorney General ballot-title letter and Office of Financial Management
fiscal impact statement (one of potentially many per-state strategies;
see ballot_measures_pdf.py, MULTI_DOCUMENT_STRATEGIES, for why several
documents per state need their own fetch).

sos.wa.gov/elections/voters/proposed-ballot-measure-information keeps
one <h2><year></h2> section per cycle: a "Certification of Measures to
General Election" PDF link, then an <h3> per measure ("Initiative No.
IL26-001") followed by that measure's documents (Ballot Title Letter,
Full text, Explanatory Statement, Fiscal Impact Statement, Arguments).
The certification PDF is a scanned image with no text layer, so it
can't be read here — but its presence is what says the list is final:
until the Secretary certifies (no later than August 21), the page lists
measures whose signatures are still being checked. So a year section
without a certification link reads as None (not yet certified), never as
a list. Verified live 2026-09-28: three certified measures for November
3, 2026 — IL26-001 (parents' rights), IL26-638 (girls' school sports),
IP26-645 (income-tax repeal).

From each measure, verbatim:
- the Attorney General's BALLOT TITLE (Statement of Subject, Concise
  Description, "Should this measure be enacted into law?") and BALLOT
  MEASURE SUMMARY, from the ballot-title letter — the letter's own
  "to the Legislature" / "to the People" names the initiative type;
- the "Summary" paragraph of OFM's Fiscal Impact Statement.

This PDF font draws "ff" as a single glyph pdfplumber can't map
("e(cid:431)ective", "O(cid:431)ice"); that one glyph is restored as "ff"
(verified on all three 2026 statements — every occurrence sits inside
"effective" or "Office"). Any other unmapped glyph leaves the fiscal
summary null rather than publishing garbled text.

Washington publishes no "A YES vote means / A NO vote means" framing —
the Explanatory Statement describes the law as it exists and the effect
of the measure, but not as vote framing — so yes_means/no_means stay
null. Any measure whose ballot title can't be fetched or read — or
whose documents sit under a heading this module can't read as a
measure — makes the whole state return None: a partial list would read
as the whole ballot.
"""

import io
import logging
import re
from urllib.parse import urljoin

import httpx
import pdfplumber
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.http_utils import fetch_bytes_with_retry, fetch_text_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

URL = "https://www.sos.wa.gov/elections/voters/proposed-ballot-measure-information"
TITLE_AUTHORITY = "Washington Attorney General (ballot title and ballot measure summary)"
FISCAL_AUTHORITY = "Washington Office of Financial Management"

_rate_limiter = RateLimiter(rps=1.0)

_MEASURE_HEADING_RE = re.compile(r"\bNo\.\s*([A-Z]{0,3}\d{2}-\d+[A-Z]?|\d+)\s*$")
_FF_LIGATURE = "(cid:431)"


def measure_links(page_html: str, year: int) -> list[tuple[str, str, dict[str, str]]] | None:
    """[(heading, measure id, {link label: href}), ...] for `year`'s
    section. None when the section is missing or has no certification
    link yet (the list isn't final)."""
    tree = lxml_html.fromstring(page_html)
    in_year = False
    certified = False
    measures: list[tuple[str, str, dict[str, str]]] = []
    for el in tree.xpath("//h2 | //h3 | //p"):
        text = clean_text(el.text_content()) or ""
        if el.tag == "h2" and re.fullmatch(r"\d{4}", text):
            if in_year:
                break
            in_year = text == str(year)
            continue
        if not in_year:
            continue
        if el.tag == "h3":
            m = _MEASURE_HEADING_RE.search(text)
            if m:
                measures.append((text, m.group(1), {}))
            elif measures and text:
                # An <h3> we can't read as a measure: close the previous
                # measure so this heading's documents can't attach to it
                # (a second ballot-title link is then caught below).
                measures.append((text, "", {}))
            continue
        for a in el.xpath(".//a[@href]"):
            label = (clean_text(a.text_content()) or "").lower()
            if "certification of measures" in label:
                certified = True
            elif measures:
                measures[-1][2][label] = a.get("href")
            elif "ballot title" in label:
                # A measure's documents with no heading we recognised
                # above them: never publish the rest as the whole list.
                return None
    if not in_year and not measures:
        return None
    if not certified:
        return None
    if any(not mid and any("ballot title" in label for label in links) for _, mid, links in measures):
        return None
    return [m for m in measures if m[1]]


def _section(text: str, start: str, end: str | None) -> str | None:
    i = text.find(start)
    if i < 0:
        return None
    i += len(start)
    j = text.find(end, i) if end else -1
    return clean_text(text[i:j] if j >= 0 else text[i:])


def parse_ballot_title_letter(text: str, measure_id: str, heading: str) -> dict | None:
    """The BALLOT TITLE and BALLOT MEASURE SUMMARY from the Attorney
    General's letter text, or None if the letter isn't that shape."""
    # A line that ends in a hyphen continues the same word on the next
    # line ("public-" / "school children"): rejoin without a space.
    text = re.sub(r"-\n(?=\w)", "-", text)
    title_block = _section(text, "BALLOT TITLE", "BALLOT MEASURE SUMMARY")
    summary = _section(text, "BALLOT MEASURE SUMMARY", "Sincerely,")
    if not title_block or not summary or "Statement of Subject:" not in title_block:
        return None
    # The ballot's own "Yes [ ] No [ ]" boxes are option marks, not text.
    title_block = clean_text(re.sub(r"Yes \[ ?\] No \[ ?\]", "", title_block))
    subject = _section(title_block, "Statement of Subject:", "Concise Description:")
    lowered = clean_text(text.lower()) or ""
    if f"{measure_id.lower()} to the legislature" in lowered:
        origin = "Washington voters (initiative to the Legislature)"
    elif f"{measure_id.lower()} to the people" in lowered:
        origin = "Washington voters (initiative to the people)"
    else:
        origin = None
    return {
        "number": measure_id,
        "title": subject or heading,
        "origin": origin,
        "official_summary": f"{title_block} Ballot Measure Summary: {summary}",
        "fiscal_impact": None,
        "yes_means": None,
        "no_means": None,
        "title_authority": TITLE_AUTHORITY,
        "fiscal_authority": None,
    }


def parse_fiscal_summary(text: str) -> str | None:
    """The "Summary" paragraph of an OFM Fiscal Impact Statement."""
    text = text.replace(_FF_LIGATURE, "ff")
    lines = [ln.strip() for ln in text.splitlines()]
    try:
        start = lines.index("Summary") + 1
    except ValueError:
        return None
    end = next((i for i in range(start, len(lines)) if lines[i] == "General assumptions"), None)
    if end is None:
        return None
    summary = clean_text(" ".join(lines[start:end]))
    if summary is None or "(cid:" in summary:
        return None
    return summary


async def _pdf_text(client: httpx.AsyncClient, url: str, label: str, pages: int | None = None) -> str | None:
    body = await fetch_bytes_with_retry(client, _rate_limiter, url, label)
    if body is None or body[:5] != b"%PDF-":
        return None
    try:
        with pdfplumber.open(io.BytesIO(body)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages[:pages])
    except Exception:
        logger.exception("%s: unreadable PDF", label)
        return None


def _link(links: dict[str, str], *needles: str) -> str | None:
    return next((href for label, href in links.items() if all(n in label for n in needles)), None)


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    page_html = await fetch_text_with_retry(client, _rate_limiter, URL, f"WA ballot measures {year}")
    if page_html is None:
        return None
    try:
        measures = measure_links(page_html, year)
    except Exception:
        logger.exception("WA ballot-measure page was not parseable HTML")
        return None
    if measures is None:
        logger.warning("WA %d: no certified measure section yet", year)
        return None

    results: list[tuple[dict, str]] = []
    for heading, measure_id, links in measures:
        title_href = _link(links, "ballot title")
        if title_href is None:
            logger.warning("WA %s: no ballot-title document linked — failing the state", measure_id)
            return None
        title_url = urljoin(URL, title_href)
        letter = await _pdf_text(client, title_url, f"WA {measure_id} ballot title")
        parsed = parse_ballot_title_letter(letter or "", measure_id, heading)
        if parsed is None:
            logger.warning("WA %s: ballot-title letter unreadable — failing the state", measure_id)
            return None
        fiscal_href = _link(links, "fiscal impact")
        if fiscal_href is not None:
            fis = await _pdf_text(client, urljoin(URL, fiscal_href), f"WA {measure_id} fiscal impact", pages=1)
            fiscal = parse_fiscal_summary(fis or "")
            if fiscal:
                parsed["fiscal_impact"] = fiscal
                parsed["fiscal_authority"] = FISCAL_AUTHORITY
        results.append((parsed, title_url))
    return results
