"""North Dakota's ballot-measure strategy — the Secretary of State's
"Measures on Ballot" page plus each measure's "Official Ballot
Language" PDF (one of MULTI_DOCUMENT_STRATEGIES in ballot_measures_pdf.py:
one PDF per measure, discovered from an index page, like Virginia).

sos.nd.gov/elections/voter/ballot-measures/measures-ballot is evergreen.
Verified live 2026-09-28: an <h2> "2026 General Election", the sentence
"The following are approved measures that will appear on the 2026
General Election Ballot", then one <h3> per measure ("Constitutional
Measure 1 – HCR 3003", "Constitutional Measure 2"), each followed by a
list linking its "Official Ballot Language" PDF. This module reads only
the <h2> section naming `year` and "General Election" (North Dakota
also holds June primary-ballot measures, which get their own heading),
and only the "Official Ballot Language" link under each <h3>.

Each ballot-language PDF is one page (verified on both 2026 measures):

    Constitutional Measure No. 1
    (House Concurrent Resolution No. 3003, 2025 Session Laws, Ch. 614)
    This constitutional measure would amend ... for enactment.
    The estimated fiscal impact of this measure is none.
    Yes – Means you approve the measure as summarized above.
    No – Means you reject the measure as summarized above.

— number and title from the heading, the explanation as the official
summary, the fiscal sentence as the fiscal impact, and the state's own
"Yes – Means ..." / "No – Means ..." lines as yes/no framing, all
verbatim. The fiscal estimate is the Legislative Council's (the
office's own "Analysis" PDF for each measure says so, citing NDCC
16.1-01-17). Origin: a heading reading "Initiated ..." is a citizen
initiative; one carrying a legislative resolution citation is
legislature-referred; anything else is left None.

A measure named on the index page whose PDF fails to fetch or parse
makes the whole read None — a shorter list is never cached as if it
were the complete ballot. A section that exists but names no measure
reads as [] (the office's own "approved measures" list for that
election, empty).
"""

import io
import logging
import re
from urllib.parse import urljoin

import httpx
import pdfplumber
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measure_text import join_lines
from app.pipeline.fetch.http_utils import fetch_bytes_with_retry, fetch_text_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

INDEX_URL = "https://www.sos.nd.gov/elections/voter/ballot-measures/measures-ballot"
FISCAL_AUTHORITY = "North Dakota Legislative Council"

_rate_limiter = RateLimiter(rps=1.0)

_HEADING_RE = re.compile(r"^((?:Initiated\s+)?(?:Constitutional|Statutory|Referred)?\s*Measure\s+No\.\s*(\d+))\s*$", re.I)
_FISCAL_RE = re.compile(r"^The estimated fiscal impact of this measure is\b", re.I)
_YES_RE = re.compile(r"^Yes\s*[–—-]\s*(Means .*)$", re.DOTALL)
_NO_RE = re.compile(r"^No\s*[–—-]\s*(Means .*)$", re.DOTALL)
_LEG_CITATION_RE = re.compile(r"^\((?:House|Senate) Concurrent Resolution No\.", re.I)


def ballot_language_links(index_html: str, year: int) -> list[str] | None:
    """The "Official Ballot Language" hrefs under `year`'s general-
    election <h2>, in page order — None when there's no such section,
    [] when the section names no measure."""
    tree = lxml_html.fromstring(index_html)
    heading = next(
        (
            h for h in tree.xpath("//h2")
            if str(year) in h.text_content() and "general election" in h.text_content().lower()
        ),
        None,
    )
    if heading is None:
        return None
    links: list[str] = []
    measures_seen = 0
    el = heading.getnext()
    while el is not None and el.tag != "h2":
        if el.tag == "h3":
            measures_seen += 1
        for a in el.xpath(".//a[@href]") if el.tag in ("ul", "p", "ol") else []:
            if " ".join(a.text_content().split()).lower() == "official ballot language":
                links.append(a.get("href"))
        el = el.getnext()
    if measures_seen != len(links):
        logger.warning(
            "ND %d: %d measure heading(s) but %d ballot-language link(s) — refusing",
            year, measures_seen, len(links),
        )
        return None
    return links


def parse_ballot_language(text: str) -> dict | None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # "Initiated Constitutional" / "Measure No. 2" can wrap onto two lines.
    joined: list[str] = []
    for ln in lines:
        if joined and _HEADING_RE.match(f"{joined[-1]} {ln}"):
            joined[-1] = f"{joined[-1]} {ln}"
        else:
            joined.append(ln)
    head_idx = next((i for i, ln in enumerate(joined) if _HEADING_RE.match(ln)), None)
    if head_idx is None:
        return None
    heading_match = _HEADING_RE.match(joined[head_idx])
    title, number = clean_text(heading_match.group(1)), heading_match.group(2)

    body = joined[head_idx + 1:]
    origin = None
    if title.lower().startswith("initiated"):
        origin = "North Dakota voters (initiative petition)"
    if body and _LEG_CITATION_RE.match(body[0]):
        origin = "North Dakota Legislative Assembly"
        body = body[1:]

    fiscal_idx = next((i for i, ln in enumerate(body) if _FISCAL_RE.match(ln)), None)
    yes_idx = next((i for i, ln in enumerate(body) if _YES_RE.match(ln)), None)
    no_idx = next((i for i, ln in enumerate(body) if _NO_RE.match(ln)), None)
    if fiscal_idx is None or yes_idx is None or no_idx is None or not (fiscal_idx < yes_idx < no_idx):
        return None
    summary = join_lines(body[:fiscal_idx])
    fiscal = join_lines(body[fiscal_idx:yes_idx])
    if not summary or not fiscal:
        return None
    # Each "Yes – Means ..." / "No – Means ..." sentence is kept whole,
    # however many lines it wraps over: Yes runs to the No line, No to its
    # full stop. Anything after No's sentence, or a sentence that never
    # reaches a full stop, refuses the document rather than store part of
    # the state's framing (the first line alone used to be kept).
    yes_means = _sentence(body[yes_idx:no_idx], _YES_RE)
    no_lines = body[no_idx:]
    end = next((i for i, ln in enumerate(no_lines) if ln.rstrip().endswith(".")), None)
    if end is None or no_lines[end + 1:]:
        return None
    no_means = _sentence(no_lines[:end + 1], _NO_RE)
    if yes_means is None or no_means is None:
        return None
    return {
        "number": number,
        "title": title,
        "origin": origin,
        "official_summary": summary,
        "fiscal_impact": fiscal,
        "yes_means": yes_means,
        "no_means": no_means,
        "title_authority": None,
        "fiscal_authority": FISCAL_AUTHORITY,
    }


def _sentence(lines: list[str], lead_re: re.Pattern) -> str | None:
    """A "Yes – Means ..." / "No – Means ..." sentence from its first line
    and any wrapped continuation lines, or None if it doesn't end in a
    full stop."""
    joined = join_lines(lines)
    m = lead_re.match(joined or "")
    if m is None or not m.group(1).endswith("."):
        return None
    return m.group(1)


def _pdf_text(raw: bytes) -> str:
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    index_html = await fetch_text_with_retry(client, _rate_limiter, INDEX_URL, "ND measures on ballot")
    if index_html is None:
        return None
    try:
        links = ballot_language_links(index_html, year)
    except Exception:
        logger.exception("ND measures page was not parseable")
        return None
    if links is None:
        return None

    results = []
    for href in links:
        url = urljoin(INDEX_URL, href)
        raw = await fetch_bytes_with_retry(client, _rate_limiter, url, "ND ballot language PDF")
        if raw is None:
            return None
        try:
            parsed = parse_ballot_language(_pdf_text(raw))
        except Exception:
            logger.exception("ND ballot language PDF parse failed: %s", url)
            return None
        if parsed is None:
            logger.warning("ND ballot language PDF didn't match the verified shape: %s", url)
            return None
        results.append((parsed, url))
    return results
