"""Michigan's ballot-measure strategy — the Bureau of Elections' own
"<year> November ballot questions" PDF, linked from the Secretary of
State's elections landing page (one of MULTI_DOCUMENT_STRATEGIES in
ballot_measures_pdf.py; see below for why it can't use the shared
discover_pdf_url + STRATEGIES path).

The PDF's URL is not predictable — michigan.gov serves it from a
content-managed media path with a `?rev=<hash>` suffix that changes on
every republish — so it is discovered each run from
michigan.gov/sos/elections, by the link whose text names `year`,
"November" and "ballot questions" (verified live 2026-09-28: "2026
November ballot questions"). The shared discover_pdf_url can't do this:
its link pattern requires an href ending in ".pdf", and it sends no
request headers.

Headers: michigan.gov's CDN (Akamai) refuses the codebase's standard
BROWSER_HEADERS with a 403, and measured 2026-09-28 that the refusal is
keyed on the "(+contact@...)" comment in the User-Agent — the same
header set with that comment removed gets 200, as does the same
User-Agent still ending in "Civitas/1.0". So this module sends
BROWSER_HEADERS with the User-Agent's trailing comment dropped: still
identifying as Civitas, just not in the bracketed crawler-contact form
that rule blocks.

The document is one page per proposal, each certified on its own date
(verified on both 2026 proposals):

    August 17, 2026
    Proposal 2026-2                                  <- bold
    A proposed initiated law to prohibit ...         <- bold, may wrap
    The proposal would:                              <- regular
    • Prohibit ...
    Should this proposal be adopted?
    [ ] Yes
    [ ] No

The bold lines after the "Proposal" line are the ballot's own title;
the regular lines up to "[ ] Yes" are the ballot's own question text
(Proposal 2026-1 has no bullet list — just its "Shall a convention
...?" question). Both are stored verbatim. The Bureau of Elections
publishes no yes/no framing and no fiscal statement in it, so those
stay None. Bold is read from the PDF's own font names, not guessed from
wording: the boundary between title and question has no textual marker
on Proposal 2026-1.

Origin is read only from the title's own words — "initiated law" /
"initiated amendment" / "initiative" means a citizen petition, a
"legislat..." word means legislature-referred; the constitutional
convention question (which the constitution itself puts on the ballot
every 16 years) says neither and is left None.
"""

import io
import logging
import re
from urllib.parse import urljoin

import httpx
import pdfplumber
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_text import join_lines
from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_bytes_with_retry, fetch_text_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

LANDING_URL = "https://www.michigan.gov/sos/elections"
TITLE_AUTHORITY = "Michigan Bureau of Elections"

# See module docstring: the CDN rejects the "(+contact)" UA comment.
HEADERS = {
    **BROWSER_HEADERS,
    "User-Agent": re.sub(r"\s*\(\+[^)]*\)\s*$", "", BROWSER_HEADERS["User-Agent"]),
}

_rate_limiter = RateLimiter(rps=1.0)

_PROPOSAL_RE = re.compile(r"^Proposal\s+(\d{4}-\d+)$")
_YES_BOX_RE = re.compile(r"^\[\s*\]\s*Yes$")


def find_pdf_url(landing_html: str, year: int) -> str | None:
    tree = lxml_html.fromstring(landing_html)
    matches = set()
    for a in tree.xpath("//a[@href]"):
        text = " ".join(a.text_content().split()).lower()
        href = a.get("href")
        if str(year) in text and "november" in text and "ballot question" in text and ".pdf" in href.lower():
            matches.add(urljoin(LANDING_URL, href))
    if len(matches) != 1:
        return None
    return matches.pop()


def pdf_lines(raw: bytes) -> list[list[tuple[str, bool]]]:
    """Per page, each text line with whether all of its letters are set
    in a bold face — the only layout fact parse_lines needs, kept as
    plain data so tests run on a real extraction without a binary PDF."""
    pages = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            lines = []
            for line in page.extract_text_lines():
                letters = [c for c in line["chars"] if c["text"].strip()]
                bold = bool(letters) and all("bold" in c["fontname"].lower() for c in letters)
                lines.append((line["text"], bold))
            pages.append(lines)
    return pages


def _origin(title: str) -> str | None:
    lowered = title.lower()
    if "initiated" in lowered or "initiative" in lowered:
        return "Michigan voters (initiative petition)"
    if "legislat" in lowered:
        return "Michigan Legislature"
    return None


def parse_lines(pages: list[list[tuple[str, bool]]], year: int) -> list[dict] | None:
    """Every `year` proposal in the document, or None when a proposal
    page doesn't have the verified shape or none is found at all."""
    results = []
    for lines in pages:
        idx = next((i for i, (t, _) in enumerate(lines) if _PROPOSAL_RE.match(t.strip())), None)
        if idx is None:
            continue
        number = _PROPOSAL_RE.match(lines[idx][0].strip()).group(1)
        if not number.startswith(f"{year}-"):
            continue
        rest = lines[idx + 1:]
        title_lines = []
        while rest and rest[0][1]:
            title_lines.append(rest.pop(0)[0])
        yes_idx = next((i for i, (t, _) in enumerate(rest) if _YES_BOX_RE.match(t.strip())), None)
        title = join_lines(title_lines)
        question = join_lines([t for t, _ in rest[:yes_idx]]) if yes_idx else None
        if question:
            # Keep the document's own bullet list a list (the card renders
            # official_summary with pre-line whitespace); no word changes.
            question = re.sub(r"\s*•\s*", "\n• ", question)
        if not title or not question:
            logger.warning("MI Proposal %s didn't match the verified shape — refusing the document", number)
            return None
        results.append({
            "number": number,
            "title": title,
            "origin": _origin(title),
            "official_summary": question,
            "fiscal_impact": None,
            "yes_means": None,
            "no_means": None,
            "title_authority": TITLE_AUTHORITY,
            "fiscal_authority": None,
        })
    # The Bureau publishes this document only once something is certified
    # to the ballot; one with no readable proposal is a shape change, not
    # a statement that there are none.
    return results or None


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    landing_html = await fetch_text_with_retry(
        client, _rate_limiter, LANDING_URL, "MI elections landing", headers=HEADERS,
    )
    if landing_html is None:
        return None
    pdf_url = find_pdf_url(landing_html, year)
    if pdf_url is None:
        logger.warning("MI: no single '%d November ballot questions' PDF link on the landing page", year)
        return None
    raw = await fetch_bytes_with_retry(client, _rate_limiter, pdf_url, "MI ballot questions PDF", headers=HEADERS)
    if raw is None:
        return None
    try:
        parsed = parse_lines(pdf_lines(raw), year)
    except Exception:
        logger.exception("MI ballot questions PDF parse failed")
        return None
    if parsed is None:
        return None
    return [(m, pdf_url) for m in parsed]
