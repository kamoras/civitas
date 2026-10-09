"""Oregon's ballot-measure strategy — proves absence only, from the
Secretary of State's State Voters' Pamphlet for the general election (one
of MULTI_DOCUMENT_STRATEGIES in ballot_measures_pdf.py).

ORS 251.185 puts every state measure in the State Voters' Pamphlet — its
ballot title, explanatory statement, financial estimate and arguments —
and sos.oregon.gov/elections/Voters-Pamphlet/Pages/Voters-Pamphlet-English
.aspx links the pamphlet "Prepared by Jurisdiction", one book per group of
counties (VP_G26-Book1_web.pdf ... Book19). Verified 2026-10-08 on the
November 3, 2026 pamphlet (Book1, 48 pages): its table of contents has no
measure section — "Candidates & Measures" lists only "List of Candidates
& Measures", "Index of Candidates" and the candidate statements — and no
page carries a "Measure <number>" heading, so the state has no measure on
that ballot.

Returns [] only when all of these hold, and None otherwise — it never
returns a measure, so a pamphlet that does carry one reads as not covered
rather than being read by a parser built without one:
- the English page links at least one book for the year
  (VP_G<yy>-Book<n>_web.pdf) and no other pamphlet document for it (a
  separate state-measures volume would be one);
- the book's cover names the general election and its date;
- its table of contents names its "Candidates & Measures" section and no
  measure entry;
- no line of the book opens "Measure <number>".
No book linked for the year is NotYetPublished: the pamphlet is posted in
October.
"""

import io
import logging
import re
from urllib.parse import urljoin

import httpx
import pdfplumber
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_text import NotYetPublished
from app.pipeline.fetch.ballot_measures_state_common import election_day, get_bytes, get_text, names_date

logger = logging.getLogger(__name__)

PAGE_URL = "https://sos.oregon.gov/elections/Voters-Pamphlet/Pages/Voters-Pamphlet-English.aspx"

_MEASURE_HEADING_RE = re.compile(r"^Measure\s+\d+\b")
_TOC_MEASURE_RE = re.compile(r"\bMeasures?\s+\d+\b|^Measures?\b")


def book_url(page_html: str, year: int) -> tuple[str | None, bool]:
    """(url, page_ok): the lowest-numbered book for `year`. page_ok False:
    the page also links a pamphlet document for the year that isn't a book."""
    tree = lxml_html.fromstring(page_html)
    prefix = f"VP_G{year % 100:02d}"
    book_re = re.compile(rf"/{prefix}-Book(\d+)([a-z]?)_web\.pdf$", re.IGNORECASE)
    books: dict[tuple[int, str], str] = {}
    for a in tree.xpath("//a[@href]"):
        href = a.get("href").strip()
        if prefix.lower() not in href.lower() or not href.lower().endswith(".pdf"):
            continue
        m = book_re.search(href)
        if m is None:
            logger.warning("OR voters' pamphlet page links a %d document that isn't a book: %s", year, href)
            return None, False
        books[(int(m.group(1)), m.group(2).lower())] = urljoin(PAGE_URL, href)
    if not books:
        return None, True
    return books[min(books)], True


def no_state_measures(page_texts: list[str], year: int) -> bool:
    """Whether the book is `year`'s general-election pamphlet and carries
    no state measure (see module docstring)."""
    cover = page_texts[0] if page_texts else ""
    if "General Election" not in cover or not names_date(cover, election_day(year)):
        logger.warning("OR pamphlet cover does not name the %d general election", year)
        return False
    toc = next((t for t in page_texts if "Table of Contents" in t), None)
    if toc is None or "Candidates & Measures" not in toc:
        logger.warning("OR pamphlet: no table of contents with its Candidates & Measures section")
        return False
    toc_lines = [ln.strip() for ln in toc.splitlines() if ln.strip()]
    entries = [ln for ln in toc_lines if ln not in ("Candidates & Measures",) and not ln.startswith("List of Candidates & Measures")]
    if any(_TOC_MEASURE_RE.search(ln) for ln in entries):
        logger.info("OR pamphlet lists a measure in its table of contents — not proving absence")
        return False
    if any(_MEASURE_HEADING_RE.match(ln.strip()) for t in page_texts for ln in t.splitlines()):
        logger.info("OR pamphlet carries a measure heading — not proving absence")
        return False
    return True


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    page = await get_text(client, PAGE_URL, "OR voters' pamphlet page")
    if page is None:
        return None
    try:
        url, page_ok = book_url(page, year)
    except Exception:
        logger.exception("OR voters' pamphlet page was not parseable")
        return None
    if not page_ok:
        return None
    if url is None:
        raise NotYetPublished(f"the Oregon Secretary of State's {year} State Voters' Pamphlet")
    raw = await get_bytes(client, url, "OR voters' pamphlet book")
    if raw is None:
        return None
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            texts = [p.extract_text() or "" for p in pdf.pages]
    except Exception:
        logger.exception("OR voters' pamphlet book was not parseable")
        return None
    return [] if no_state_measures(texts, year) else None
