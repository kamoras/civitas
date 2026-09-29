"""Connecticut's ballot-measure strategy — read from the official sample
ballots the Secretary of the State publishes for every town (one of
potentially many per-state strategies; see ballot_measures_pdf.py for the
shared single-PDF contract this module does NOT use, and why).

Connecticut has no citizen initiative. A statewide question can only be
a constitutional amendment the General Assembly refers (Const. art.
XII) or the constitutional-convention question (art. XIII), and either
one is printed on EVERY town's ballot for that election, in a "Vote on
the Question(s)" column beside the offices (verified on the real 2024
Eastford ballot, which carries 2024's no-excuse-absentee amendment
exactly that way). So one complete, official ballot for the state
election with no question column on it is proof that no statewide
question is on the ballot anywhere in the state. That is what this
module checks, and all it can conclude: it returns [] (-> MeasureCoverage.
CONFIRMED_NONE) or None. It never returns a measure — the sample ballot
carries only the question's text, not the explanatory text a measure
record needs, so a cycle WITH a statewide question comes back None
(ingest_failed, which alerts) until a parser for the Secretary's
explanatory-text page is added. Failing loudly there is deliberate:
this is a check that can prove absence, not a reader that could
under-report presence.

Local questions exist and are printed in the same column (verified: the
real 2026 Burlington ballot carries a Regional School District 10 bond
question), so a ballot WITH a question proves nothing either way; the
module keeps sampling towns until it finds one without, and gives up
(None) after MAX_BALLOTS. A ballot only counts if it is demonstrably
this year's November state-election ballot and complete — its own text
must say "State Election", "November" and the year, and "Sheet 1 of 1"
(a multi-sheet ballot could carry a question on a sheet this check
didn't see).

Discovery is evergreen, not a fixed URL: the Secretary's sample-ballot
index (town-ballots/ballots) links each election's page by a slug that
changes shape between cycles ("2024-general-election-sample-ballots",
"2026-november-town-election-ballots" — both real), so the page is
chosen by year plus general/november wording, excluding primary,
special, presidential-preference and municipal pages.

2026 (fetched live 2026-09-28): 19 town ballots posted; 15 carry no
question column at all (e.g. Eastford, "Sheet 1 of 1"), the other four
carry only local questions — no statewide question this cycle. Re-checked
every run (ballot_measures_pdf.CACHE_TTL_HOURS).
"""

import io
import logging
import re
from urllib.parse import urljoin

import httpx
import pdfplumber

from app.pipeline.fetch.http_utils import fetch_bytes_with_retry, fetch_text_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

INDEX_URL = "https://portal.ct.gov/sots/election-services/town-ballots/ballots"
MAX_BALLOTS = 12

_rate_limiter = RateLimiter(rps=1.0)

_HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
_EXCLUDE_SLUG_WORDS = ("primary", "special", "ppp", "municipal")


def election_page_url(index_html: str, year: int) -> str | None:
    """This year's November state-election sample-ballot page from the
    Secretary's index, or None if there isn't exactly one candidate."""
    candidates = set()
    for href in _HREF_RE.findall(index_html):
        slug = href.split("?")[0].rstrip("/").rsplit("/", 1)[-1].lower()
        if "/town-ballots/" not in href or not slug.startswith(f"{year}-"):
            continue
        if any(w in slug for w in _EXCLUDE_SLUG_WORDS):
            continue
        if "november" in slug or "general" in slug:
            candidates.add(urljoin(INDEX_URL, href.split("?")[0]))
    return candidates.pop() if len(candidates) == 1 else None


def ballot_pdf_urls(page_html: str, page_url: str) -> list[str]:
    urls = []
    for href in _HREF_RE.findall(page_html):
        href = href.replace("&amp;", "&")
        if ".pdf" in href.lower():
            url = urljoin(page_url, href)
            if url not in urls:
                urls.append(url)
    return urls


def _squash(text: str) -> str:
    # pdfplumber drops the spaces on some towns' ballots ("StateElection
    # November3,2026" — real, 2026) and keeps them on others ("State
    # Election November 5, 2024" — real, 2024); compare without them.
    return re.sub(r"\s+", "", text).lower()


def is_question_free_state_ballot(text: str, year: int) -> bool:
    """True when `text` is a complete November state-election ballot for
    `year` with no question column on it."""
    t = _squash(text)
    if "stateelection" not in t or "november" not in t or str(year) not in t:
        return False
    if "sheet1of1" not in t:
        return False
    return "question" not in t and "pregunta" not in t


def _extract_text(raw: bytes) -> str:
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    """[] when an official, complete, question-free state-election ballot
    for `year` is found; None otherwise (see module docstring — this
    strategy proves absence and never returns a measure)."""
    index_html = await fetch_text_with_retry(client, _rate_limiter, INDEX_URL, "CT sample-ballot index")
    if index_html is None:
        return None
    page_url = election_page_url(index_html, year)
    if page_url is None:
        logger.warning("CT: no single %d November sample-ballot page on the index", year)
        return None
    page_html = await fetch_text_with_retry(client, _rate_limiter, page_url, f"CT {year} sample ballots")
    if page_html is None:
        return None

    for pdf_url in ballot_pdf_urls(page_html, page_url)[:MAX_BALLOTS]:
        raw = await fetch_bytes_with_retry(client, _rate_limiter, pdf_url, "CT sample ballot")
        if raw is None:
            continue
        try:
            text = _extract_text(raw)
        except Exception:
            logger.warning("CT sample ballot %s did not parse as a PDF", pdf_url)
            continue
        if is_question_free_state_ballot(text, year):
            logger.info("CT %d: %s is a complete state ballot with no question — no statewide question", year, pdf_url)
            return []
    logger.warning(
        "CT %d: no question-free state ballot among the first %d sample ballots — not concluding",
        year, MAX_BALLOTS,
    )
    return None
