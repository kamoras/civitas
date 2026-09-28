"""Indiana's ballot-measure strategy — the Indiana Election Division's
annual "Summary of <year> Election Legislation" PDF, which prints the
text of each constitutional-amendment public question on that year's
ballot (one of MULTI_DOCUMENT_STRATEGIES in ballot_measures_pdf.py: the
PDF's link carries a `?language_id=1` suffix the shared
discover_pdf_url's ".pdf"-suffix link pattern doesn't match).

Indiana has no citizen initiative. A constitutional amendment reaches
the ballot only after two separately elected General Assemblies agree
to it, and the ballot text of the resulting public question is then set
by statute (2026: SEA 3 for Public Question #1, HEA 1019 for #2). The
Election Division's legislation summary for the year the amendment is
voted on prints that statutory text under an "INDIANA CONSTITUTIONAL
AMENDMENTS" heading, introduced as "The text of the public question
will read as follows:" and quoted in full. Verified live 2026-09-28
against the 2026 summary (22 pages), which prints both public
questions on the November 3, 2026 ballot:

    "Public Question #1
     Currently, under the Constitution ... Shall the Constitution of the
     State of Indiana be amended to provide that ...
     (This question concerns Article 1, Section 17 of the Constitution
     of the State of Indiana.)"

Each quoted question is stored whole and verbatim as official_summary;
the title is the ballot's own label ("Public Question #1"). The
Division's topic headings ("Limitations on the Right to Bail") are its
own summary wording, not ballot text, so they are not stored. Each
question is only accepted when the paragraph introducing it names
`year`'s general election ("... on the ballot at the November 3, 2026,
general election"), so a question summarized in a legislation year
other than its ballot year is not mis-dated.

Discovery: the legislation-summaries index page
(in.gov/sos/elections/statistics-and-maps/indiana-election-legislation-
summaries) links every year's summary with the link text "Summary of
<year> Election Legislation"; the file name has changed format several
times (2022-Legislative-Summary.FINAL.pdf, 2026-Indiana-Election-
Legislation-Summary.FINAL.pdf), the link text has not.

A summary with no public question does NOT establish that the ballot
has none — it is a summary of legislation, not a certified list — so
this module never returns []: no question found reads as None.
"""

import io
import logging
import re
from urllib.parse import urljoin

import httpx
import pdfplumber
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_text import join_lines
from app.pipeline.fetch.http_utils import fetch_bytes_with_retry, fetch_text_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

INDEX_URL = "https://www.in.gov/sos/elections/statistics-and-maps/indiana-election-legislation-summaries"
ORIGIN = "Indiana General Assembly"

_rate_limiter = RateLimiter(rps=1.0)

_QUESTION_RE = re.compile(r"“Public Question #(\d+)\s*\n(.*?)”", re.DOTALL)
_INTRO = "The text of the public question will read as follows:"
_PAGE_NUMBER_RE = re.compile(r"\n\s*\d{1,3}\s*$")


def find_summary_url(index_html: str, year: int) -> str | None:
    tree = lxml_html.fromstring(index_html)
    wanted = f"summary of {year} election legislation"
    hrefs = {
        urljoin(INDEX_URL, a.get("href"))
        for a in tree.xpath("//a[@href]")
        if " ".join(a.text_content().split()).lower() == wanted
    }
    return hrefs.pop() if len(hrefs) == 1 else None


def pdf_text(raw: bytes) -> str:
    """Page texts joined, each page's trailing page-number line dropped
    (a bare "21" lands mid-paragraph otherwise — verified on the real
    2026 summary, where #2's introduction spans pages 20-21)."""
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    return "\n".join(_PAGE_NUMBER_RE.sub("", p) for p in pages)


def parse_summary(text: str, year: int) -> list[dict] | None:
    results = []
    for m in _QUESTION_RE.finditer(text):
        number, body = m.group(1), m.group(2)
        # The introducing paragraph is what dates the question; it must
        # directly precede the quote and name this year's general election.
        intro_end = text.rfind(_INTRO, 0, m.start())
        if intro_end == -1 or text[intro_end + len(_INTRO):m.start()].strip():
            logger.warning("IN Public Question #%s: not introduced by the expected sentence — refusing", number)
            return None
        preceding = " ".join(text[max(0, intro_end - 400):intro_end].split()).lower()
        if f", {year}, general election" not in preceding:
            continue
        question = join_lines(body)
        if not question:
            return None
        results.append({
            "number": number,
            "title": f"Public Question #{number}",
            "origin": ORIGIN,
            "official_summary": question,
            "fiscal_impact": None,
            "yes_means": None,
            "no_means": None,
            "title_authority": ORIGIN,
            "fiscal_authority": None,
        })
    return results or None


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    index_html = await fetch_text_with_retry(client, _rate_limiter, INDEX_URL, "IN legislation summaries index")
    if index_html is None:
        return None
    url = find_summary_url(index_html, year)
    if url is None:
        logger.warning("IN: no single 'Summary of %d Election Legislation' link", year)
        return None
    raw = await fetch_bytes_with_retry(client, _rate_limiter, url, "IN legislation summary PDF")
    if raw is None:
        return None
    try:
        parsed = parse_summary(pdf_text(raw), year)
    except Exception:
        logger.exception("IN legislation summary parse failed")
        return None
    if parsed is None:
        return None
    return [(m, url) for m in parsed]
