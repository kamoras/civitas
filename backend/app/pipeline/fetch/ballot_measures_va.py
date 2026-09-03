"""Virginia's ballot-measure strategy — the Department of Elections'
per-question "Explanation for Proposed Constitutional Amendment" PDFs
(one of potentially many per-state strategies; see ballot_measures_pdf.py
for the shared single-PDF contract this module does NOT use, and why).

Structurally different from every other state registered so far: CA/MA/
CO publish ONE combined guide covering every measure, and LA's is one
PDF split by "Proposed Amendment No. N" — Virginia publishes a SEPARATE
two-page PDF per question, discovered from a stable index page
(elections.virginia.gov/election-law/referenda/) that links each
question's own explanation page, which in turn links that question's
PDF in four languages. There is no single URL this module can hand to
ballot_measures_pdf.py's fetch-one-PDF-then-parse pipeline, so it does
its own fetching end to end and is dispatched separately (see
MULTI_DOCUMENT_STRATEGIES in ballot_measures_pdf.py) rather than
plugging into STRATEGIES' `pdf.pages -> list[dict]` shape.

Each PDF is plain, clearly labeled text (no column geometry to
reconstruct): "EXPLANATION FOR VOTERS" / "QUESTION N" followed by
"Present Law" and "Proposed Amendment" paragraphs (combined here as
official_summary), then a page break to "BALLOT QUESTION" (the literal
text put to voters) and "FULL TEXT OF AMENDMENT" (the legal redline,
not extracted — same "narrative summary only" scope as every other
state here). No "A YES vote means.../A NO vote means..." framing is
published (unlike CA/MA/CO) — same as Louisiana, yes_means/no_means
stay null rather than inferred from the question's phrasing. No fiscal-
impact statement either (constitutional amendments, not spending
measures) — also null. All Virginia constitutional amendments are
General-Assembly-referred — the state constitution has no citizen-
initiative process — so origin is fixed, not derived per-amendment,
same reasoning as Louisiana's ORIGIN constant.

Verified live 2026-09-03 against all 3 real questions on the November 3,
2026 ballot (abortion/reproductive-freedom, same-sex marriage, felony
voting-rights restoration) — 3/3 parse cleanly with a real official
Present-Law/Proposed-Amendment summary and the real ballot question
text, English PDF correctly distinguished from the Spanish/Korean/
Vietnamese versions the same page links (language versions are named
with a "-ES"/"-KO"/"-VI" suffix; English has none).
"""

import io
import logging
import re

import httpx
import pdfplumber

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

ORIGIN = "Virginia General Assembly"
TITLE_AUTHORITY = "Virginia Department of Elections"

_INDEX_URL = "https://www.elections.virginia.gov/election-law/referenda/"
_QUESTION_LINK_RE = re.compile(
    r'href="(/election-law/proposed-constitutional-amendment-question-(\d+)/)"',
)
_PDF_LINK_RE = re.compile(r'href="([^"]+\.pdf)"', re.IGNORECASE)
# The four language versions share one filename with a trailing
# "-XX.pdf" code; English is the one WITHOUT that suffix. Checked
# against a 2-letter code specifically so a filename that legitimately
# ends in two consonants (unlikely, but this is text from a real .gov
# site, not a controlled vocabulary) doesn't accidentally match.
_LANGUAGE_SUFFIX_RE = re.compile(r"-[A-Z]{2}\.pdf$")

_QUESTION_RE = re.compile(r"QUESTION\s+(\d+)", re.IGNORECASE)
_SECTION_TITLE_RE = re.compile(r"Section\s+\S+\.\s*([^.\n]+)\.")
_SUMMARY_RE = re.compile(
    r"Present Law\s*(.*?)\s*Word Count:", re.IGNORECASE | re.DOTALL,
)
_BALLOT_QUESTION_RE = re.compile(
    r"BALLOT QUESTION\s*(.*?)\s*FULL TEXT OF AMENDMENT", re.IGNORECASE | re.DOTALL,
)

_rate_limiter = RateLimiter(rps=1.0)


async def _get_text(client: httpx.AsyncClient, url: str, label: str) -> str | None:
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=30.0, log_label=label, headers=BROWSER_HEADERS,
    )
    return resp.text if resp is not None else None


async def _get_bytes(client: httpx.AsyncClient, url: str, label: str) -> bytes | None:
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=60.0, log_label=label, headers=BROWSER_HEADERS,
    )
    return resp.content if resp is not None else None


def _english_pdf_url(question_page_html: str, base_url: str) -> str | None:
    candidates = [m.group(1) for m in _PDF_LINK_RE.finditer(question_page_html)]
    english = [c for c in candidates if not _LANGUAGE_SUFFIX_RE.search(c)]
    if len(english) != 1:
        return None
    href = english[0]
    return href if href.startswith("http") else f"https://www.elections.virginia.gov{href}"


def _extract_text(raw: bytes) -> str:
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def parse_document(full_text: str, number: str) -> dict | None:
    """One question's PDF, already reduced to extract_text() output — kept
    separate from the bytes/HTTP layer above so a test can feed real,
    text fixtures directly (same convention every other state's parser
    here follows), not a binary PDF."""
    summary_match = _SUMMARY_RE.search(full_text)
    question_match = _BALLOT_QUESTION_RE.search(full_text)
    if summary_match is None or question_match is None:
        return None
    official_summary = clean_text(summary_match.group(1))
    question_text = clean_text(question_match.group(1))
    if not official_summary or not question_text:
        return None

    title_match = _SECTION_TITLE_RE.search(full_text)
    title = title_match.group(1).strip() if title_match else f"Question {number}"

    return {
        "number": number,
        "title": title,
        "origin": ORIGIN,
        # The document's Present-Law/Proposed-Amendment explanation is the
        # narrative summary; the literal ballot question (what appears on
        # the ballot itself) is appended after it rather than dropped —
        # both are real, sourced text, neither fabricated.
        "official_summary": f"{official_summary} Ballot question: {question_text}",
        "fiscal_impact": None,
        "yes_means": None,
        "no_means": None,
        "title_authority": TITLE_AUTHORITY,
        "fiscal_authority": None,
    }


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    """[(parsed, source_url), ...] for every question on `year`'s general-
    election ballot, or None on a fetch failure. [] if the index page
    fetches fine but names no general-election question this cycle (a
    year with none referred, or one whose only amendment is a special
    election VA keys separately — verified real case: April 2026's
    special-election amendment uses a different URL shape entirely and is
    correctly not matched by _QUESTION_LINK_RE)."""
    index_html = await _get_text(client, _INDEX_URL, "VA referenda index")
    if index_html is None:
        return None

    question_urls = {
        m.group(2): f"https://www.elections.virginia.gov{m.group(1)}"
        for m in _QUESTION_LINK_RE.finditer(index_html)
    }
    if not question_urls:
        return []

    results = []
    for number, question_url in sorted(question_urls.items(), key=lambda kv: int(kv[0])):
        page_html = await _get_text(client, question_url, f"VA question {number} page")
        if page_html is None:
            logger.warning("VA question %s page fetch failed — skipping", number)
            continue
        pdf_url = _english_pdf_url(page_html, question_url)
        if pdf_url is None:
            logger.warning("VA question %s: no unambiguous English PDF link found", number)
            continue
        pdf_bytes = await _get_bytes(client, pdf_url, f"VA question {number} PDF")
        if pdf_bytes is None:
            continue
        try:
            parsed = parse_document(_extract_text(pdf_bytes), number)
        except Exception:
            logger.exception("VA question %s PDF parse failed", number)
            continue
        if parsed is None:
            logger.warning("VA question %s: PDF didn't match the expected section shape", number)
            continue
        results.append((parsed, pdf_url))

    return results
