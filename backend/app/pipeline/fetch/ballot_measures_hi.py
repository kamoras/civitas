"""Hawaii's ballot-measure strategy — the Office of Elections' own
"<year> Proposed Amendments to the Hawaii State Constitution" HTML page
(one of potentially many per-state strategies; see ballot_measures_pdf.py
for the shared contract, and MULTI_DOCUMENT_STRATEGIES there for why an
HTML source does its own fetching).

No PDF: elections.hawaii.gov publishes each cycle's questions directly
as HTML at an evergreen, {year}-substituted URL, verified live on two
real generals (2024 and 2026, both 200; 2022 and 2020 are not on this
pattern and 404, which reads as a fetch failure, never as "none").
Verified live 2026-09-28 against both questions on the November 3, 2026
ballot (judicial-appointment timeframe; county RISE bonds).

Each question is a bold paragraph "QUESTION #N: <title>" followed —
not always as its next sibling: 2026's second question is wrapped in
four nested <div>s that the first is not — by a <blockquote> holding the
ballot question itself, verbatim ("Shall the Constitution of the State
of Hawaii be amended ...?"). This module walks the page in document
order and pairs each heading with the first blockquote after it and
before the next heading, so that wrapper inconsistency doesn't matter. A
heading left unpaired fails the whole page (None), so a question is
never silently missing from an otherwise-published list.

Hawaii has no citizen initiative or referendum: every statewide question
is a constitutional amendment proposed by the Legislature (or the
decennial convention question, which is also placed by law), so origin
is fixed. No "A YES vote means / A NO vote means" framing and no fiscal
statement are published on this page — both stay null rather than being
inferred from the question's wording.
"""

import logging
import re

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.http_utils import fetch_text_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

URL_PATTERN = "https://elections.hawaii.gov/voting/{year}-proposed-amendments-to-the-hawaii-state-constitution/"
ORIGIN = "Hawaii State Legislature"
TITLE_AUTHORITY = "Hawaii State Legislature (ballot question), as published by the Hawaii Office of Elections"

_rate_limiter = RateLimiter(rps=1.0)

_QUESTION_HEADING_RE = re.compile(r"^QUESTION\s*#\s*(\d+)\s*:\s*(.+)$", re.IGNORECASE | re.DOTALL)


def parse_page(page_html: str) -> list[dict]:
    """Every "QUESTION #N" on the page, each paired with the first
    blockquote that follows it. A heading with no blockquote before the
    next heading is dropped, not guessed at."""
    tree = lxml_html.fromstring(page_html)
    current: tuple[str, str] | None = None
    results: list[dict] = []
    for el in tree.xpath("//p | //blockquote"):
        text = clean_text(el.text_content()) or ""
        if el.tag == "p":
            m = _QUESTION_HEADING_RE.match(text)
            if m:
                current = (m.group(1), clean_text(m.group(2)))
            continue
        if current is None:
            continue
        number, title = current
        current = None
        if not text:
            continue
        results.append({
            "number": number,
            "title": title,
            "origin": ORIGIN,
            "official_summary": text,
            "fiscal_impact": None,
            "yes_means": None,
            "no_means": None,
            "title_authority": TITLE_AUTHORITY,
            "fiscal_authority": None,
        })
    return results


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    """[(parsed, source_url), ...] for `year`'s general-election
    questions, or None when the page can't be fetched (including a 404
    because it isn't published yet — indistinguishable from an outage)
    or carries no question it can read. Never []: this page states no
    count and no "none", so an empty read can't be a checked answer."""
    url = URL_PATTERN.format(year=year)
    page_html = await fetch_text_with_retry(client, _rate_limiter, url, f"HI amendments {year}")
    if page_html is None:
        return None
    try:
        parsed = parse_page(page_html)
    except Exception:
        logger.exception("HI amendments page for %d was not parseable HTML", year)
        return None
    headings = len(set(re.findall(r"QUESTION\s*#\s*(\d+)\s*:", lxml_html.fromstring(page_html).text_content(), re.IGNORECASE)))
    if headings == 0:
        # The Office of Elections publishes this page for a year because
        # that year has amendments; a page with no question on it is a
        # changed layout (or a placeholder), never a statement of "none".
        logger.warning("HI %d: amendments page has no QUESTION heading — failing, not 'none'", year)
        return None
    if headings != len(parsed):
        # A question heading we couldn't pair with its ballot text: never
        # publish the others as if they were the whole ballot.
        logger.warning("HI %d: %d question headings but %d parsed — failing", year, headings, len(parsed))
        return None
    return [(p, url) for p in parsed]
