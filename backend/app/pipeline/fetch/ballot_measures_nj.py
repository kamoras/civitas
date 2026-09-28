"""New Jersey's ballot-measure strategy — the Division of Elections'
own per-year "Election Information" page plus the one-page "Public
Question No. N" PDFs it links (one of potentially many per-state
strategies; see ballot_measures_pdf.py for the shared single-PDF
contract this module does NOT use, and why).

Source: https://www.nj.gov/state/elections/election-information-{year}.shtml
— one page per election year, a stable {year} url_pattern verified live
for 2019 through 2026 (all 200). In a year with statewide public
questions the page links each one as "Public Question No. N - English"
(plus Spanish/Gujarati/Korean versions) under an "Official Public
Questions" heading; verified against the real 2019 (1 question), 2020
(3) and 2021 (2) pages.

HTML comments are stripped before any link is read, and that is the
load-bearing detail of this module. The Division keeps an old
public-question block (2023's CCRC veterans question) in its page
template as commented-out markup and re-dates its hrefs every year —
verified live on the 2024, 2025 AND 2026 pages, where it points at
"{year}-public-question-1-english.pdf" files that all 404. A reader
that doesn't strip comments reports a phantom Question 1 every year.
Stripping follows the browser's own rule (a comment ends at the first
"-->"), so what's read is exactly what a visitor sees.

"None this cycle" ([] -> MeasureCoverage.CONFIRMED_NONE) is only
returned when the page is demonstrably this year's live general-
election record: it has to carry a visible link to this year's
official general-election candidate certification (the Division posts
it after the primary; verified live on the 2026 page, dated 09/04/26).
A question certified for the ballot is published on this same page
alongside those certifications, so a page that is maintained for the
general and lists no question is the Division's own statement that
there is none. A page without that anchor (not yet updated, restyled)
is None — "we don't know", never "none". This speaks only to the
CURRENT cycle's page: the Division edits a year's page after its
election (the real 2023 page, as served in 2026, hides that year's real
CCRC question entirely), so this module is never pointed at a past
year to reconstruct what was on it. Re-checked every run
(ballot_measures_pdf.CACHE_TTL_HOURS), so a late certification is
picked up.

Each question PDF is one page: "PUBLIC QUESTION NO. N", an all-caps
title, the ballot question itself, then "INTERPRETIVE STATEMENT" and
its text. The YES/NO voting boxes sit in a narrow left column that
pdfplumber interleaves into the body text ("... events? YES"), so it is
cropped off first — at the body's own left margin, read from the
page's words, not a fixed coordinate. The interpretive statement is
stored as official_summary, the title as printed; the ballot question
is used only to confirm the document's shape (no BallotMeasure field
holds it — same reasoning as ballot_measures_va.py). New Jersey
publishes no "a yes vote means" framing and no fiscal statement on
these documents, so yes_means/no_means/fiscal_impact stay null.

Every statewide public question in New Jersey is placed on the ballot
by the Legislature (constitutional amendments by concurrent resolution
under Art. IX; bond and other referenda by statute) — the state has no
statewide citizen initiative — and the question and its interpretive
statement are both part of that legislative act. Origin and
title_authority are therefore fixed.

2026: the live page (fetched 2026-09-28) carries the certification
anchor and no public-question link — no statewide question this cycle.
"""

import io
import logging
import re
from urllib.parse import urljoin

import httpx
import pdfplumber

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.http_utils import fetch_bytes_with_retry, fetch_text_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

URL_PATTERN = "https://www.nj.gov/state/elections/election-information-{year}.shtml"

ORIGIN = "New Jersey Legislature"
TITLE_AUTHORITY = ORIGIN

_rate_limiter = RateLimiter(rps=1.0)

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_ANCHOR_RE = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
# "Public Question No. 1 - English" (2019/2020/2026 template) and
# "Public Questions No. 1 - English" (2021) — both real.
_QUESTION_LINK_TEXT_RE = re.compile(r"Public\s+Questions?\s+No\.?\s*(\d+)\s*-\s*English", re.IGNORECASE)
_ANY_QUESTION_TEXT_RE = re.compile(r"Public\s+Questions?\b", re.IGNORECASE)
_CERTIFICATION_TEXT_RE = re.compile(r"Official\s+General\s+Election\s+Candidates", re.IGNORECASE)

_HEADING_RE = re.compile(r"PUBLIC\s+QUESTION\s+NO\.?\s*(\d+)", re.IGNORECASE)
_STATEMENT_RE = re.compile(r"\bINTERPRETIVE\s+STATEMENT\b\s*(.*)$", re.DOTALL)
_BOX_LABELS = {"YES", "NO"}


def visible_html(page_html: str) -> str:
    """The page with HTML comments removed — see module docstring."""
    return _COMMENT_RE.sub("", page_html)


def question_links(page_html: str, base_url: str) -> dict[str, str] | None:
    """{number: english_pdf_url} for every VISIBLE public-question link,
    or None if the page isn't recognisably this cycle's live general-
    election record (see module docstring for why that gate exists)."""
    html = visible_html(page_html)
    anchors = [(href, clean_text(_TAG_RE.sub(" ", text)) or "") for href, text in _ANCHOR_RE.findall(html)]
    if not any(_CERTIFICATION_TEXT_RE.search(text) for _, text in anchors):
        return None
    links: dict[str, str] = {}
    for href, text in anchors:
        m = _QUESTION_LINK_TEXT_RE.search(text)
        if m and m.group(1) not in links:
            links[m.group(1)] = urljoin(base_url, href)
    if not links and any(_ANY_QUESTION_TEXT_RE.search(text) for _, text in anchors):
        # A visible public-question link of some other kind (results,
        # a certification) with no question document beside it: the
        # page contradicts itself, so it isn't read as "none".
        return None
    return links


def page_text(page) -> str:
    """One pdfplumber page's text with the YES/NO ballot-box column
    cropped off at the body's own left margin."""
    words = page.extract_words()
    body = [w for w in words if w["text"] not in _BOX_LABELS]
    if not body:
        return page.extract_text() or ""
    left = min(w["x0"] for w in body) - 2
    return page.crop((left, 0, page.width, page.height)).extract_text() or ""


def parse_document(full_text: str, number: str) -> dict | None:
    """One question's cropped text -> the strategy dict, or None when the
    document doesn't have the verified shape (heading matching `number`,
    an all-caps title, a question, and an interpretive statement)."""
    lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
    if not lines:
        return None
    heading = _HEADING_RE.fullmatch(lines[0])
    if heading is None or heading.group(1).lstrip("0") != number.lstrip("0"):
        return None

    title_lines = []
    for ln in lines[1:]:
        if any(c.isalpha() for c in ln) and ln == ln.upper():
            title_lines.append(ln)
        else:
            break
    title = clean_text(" ".join(title_lines))
    rest = "\n".join(lines[1 + len(title_lines):])
    statement = _STATEMENT_RE.search(rest)
    if not title or statement is None:
        return None
    question = clean_text(rest[: statement.start()])
    official_summary = clean_text(statement.group(1))
    if not question or not official_summary:
        return None

    return {
        "number": number,
        "title": title,
        "origin": ORIGIN,
        "official_summary": official_summary,
        "fiscal_impact": None,
        "yes_means": None,
        "no_means": None,
        "title_authority": TITLE_AUTHORITY,
        "fiscal_authority": None,
    }


def _extract_text(raw: bytes) -> str:
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return "\n".join(page_text(p) for p in pdf.pages)


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    """[(parsed, source_url), ...] for every statewide public question on
    `year`'s general-election ballot; [] when the live page lists none
    (see module docstring for the gate that makes that a real answer);
    None on any fetch/parse failure, including ONE question failing —
    a shorter list cached as complete would hide a real question."""
    url = URL_PATTERN.format(year=year)
    page_html = await fetch_text_with_retry(client, _rate_limiter, url, f"NJ election information {year}")
    if page_html is None:
        return None
    links = question_links(page_html, url)
    if links is None:
        logger.warning("NJ %d election page carries no general-election certification yet — not concluding", year)
        return None

    results = []
    for number in sorted(links, key=int):
        pdf_url = links[number]
        raw = await fetch_bytes_with_retry(client, _rate_limiter, pdf_url, f"NJ public question {number}")
        if raw is None:
            logger.warning("NJ public question %s PDF fetch failed", number)
            return None
        try:
            parsed = parse_document(_extract_text(raw), number)
        except Exception:
            logger.exception("NJ public question %s PDF parse failed", number)
            return None
        if parsed is None:
            logger.warning("NJ public question %s: PDF didn't match the expected shape", number)
            return None
        results.append((parsed, pdf_url))
    return results
