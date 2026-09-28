"""Kansas's ballot-measure strategy — the Secretary of State's own
"Proposed Constitutional Amendments" page, read as HTML (see
ballot_measures_pdf.py for the shared contract; this is one of the
MULTI_DOCUMENT_STRATEGIES because there is no PDF to hand the shared
pipeline — the page itself carries the ballot text).

sos.ks.gov/elections/proposed-constitutional-amendments.html is an
evergreen URL the office rewrites for each election: verified live
2026-07-10 (Wayback) carrying the August 4, 2026 primary's judicial-
election amendment, and 2026-09-28 carrying the November 3, 2026
general's citizenship amendment, each introduced by the same sentence:

    "The following constitutional amendment will be voted on during the
     November 3, 2026 General Election."

This module reads only a section whose introducing sentence names
`year` AND "General Election". A page still showing a primary's
amendment, or last cycle's, raises NotYetPublished (not yet covered, no
alert) — never [] — and a page with no such introducing sentence at all
is None (a layout this reader doesn't know).

Each amendment on the page is printed as the ballot prints it (verified
against the office's own downloadable ballot PDF for 2026): a
"Explanatory statement." paragraph, then "A vote for this proposition
would ..." and "A vote against this proposition would ..." — Kansas's
own yes/no framing, written into the legislature's concurrent
resolution and lifted here verbatim (the opening curly quote the page
reproduces from the resolution is the only character dropped). Then
"Shall the following be adopted?" and the amendment's text, which is
not stored (no BallotMeasure field holds full text).

Numbering: the page itself prints no number. The official ballot PDF
it links prints "Constitutional Amendment 1" for the single 2026
amendment; Kansas numbers amendments in the order the Secretary of
State lists them, so the number here is the amendment's position in
this page's own list and the title is "Constitutional Amendment <n>",
the ballot's own heading — a label, so no official_title is claimed. The
explanatory statement and for/against sentences it quotes are written
into the Legislature's concurrent resolution, hence title_authority. Only single-amendment pages have been seen
live; a multi-amendment page is split on each "Explanatory statement."
and each part must carry its own for/against pair or the whole read is
refused (None) rather than published short.

Kansas has no citizen-initiative process — every statewide question is
a legislature-referred constitutional amendment — so origin is fixed.
"""

import logging
import re

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measure_text import NotYetPublished
from app.pipeline.fetch.http_utils import fetch_text_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

URL = "https://sos.ks.gov/elections/proposed-constitutional-amendments.html"
ORIGIN = "Kansas Legislature"

_rate_limiter = RateLimiter(rps=1.0)

_INTRO_RE = re.compile(
    r"The following constitutional amendments? (?:will|shall) be voted on during the "
    r"([A-Z][a-z]+ \d{1,2}, (\d{4})) General Election",
    re.IGNORECASE,
)
# The same introducing sentence for ANY election — the primary's too. The
# page carrying one that isn't `year`'s general is the known page, just
# not yet rewritten for this election.
_ANY_ELECTION_INTRO_RE = re.compile(
    r"The following constitutional amendments? (?:will|shall) be voted on during the "
    r"[A-Z][a-z]+ \d{1,2}, \d{4} (?:General|Primary|Special) Election",
    re.IGNORECASE,
)
_EXPLANATORY_RE = re.compile(r"^[“\"]?\s*Explanatory statement\.\s*", re.IGNORECASE)
_FOR_RE = re.compile(r"^[“\"]?\s*(A vote for this proposition .*)$", re.IGNORECASE)
_AGAINST_RE = re.compile(r"^[“\"]?\s*(A vote against this proposition .*?)[”\"]?$", re.IGNORECASE)
_QUESTION_RE = re.compile(r"^Shall the following be adopted\?$", re.IGNORECASE)


def _paragraphs(page_html: str) -> list[str]:
    tree = lxml_html.fromstring(page_html)
    return [t for t in (clean_text(p.text_content()) for p in tree.xpath("//p")) if t]


def parse_page(page_html: str, year: int) -> list[dict] | None:
    """Every amendment in `year`'s general-election section, or None
    when the page has no such section (not yet published for this
    cycle) or a part of it doesn't have the shape verified above."""
    paragraphs = _paragraphs(page_html)
    start = next(
        (
            i for i, p in enumerate(paragraphs)
            if (m := _INTRO_RE.search(p)) and m.group(2) == str(year)
        ),
        None,
    )
    if start is None:
        if any(_ANY_ELECTION_INTRO_RE.search(p) for p in paragraphs):
            # The page is the one we know, carrying another election's
            # amendment (the August primary's, or last cycle's): this
            # election's isn't posted yet.
            raise NotYetPublished(f"the Kansas Secretary of State's amendment page for the {year} general election")
        return None

    blocks: list[list[str]] = []
    for p in paragraphs[start + 1:]:
        if _INTRO_RE.search(p):
            break  # another election's section
        if _EXPLANATORY_RE.match(p):
            blocks.append([p])
        elif blocks:
            blocks[-1].append(p)

    if not blocks:
        return None

    results = []
    for index, block in enumerate(blocks, start=1):
        summary = clean_text(_EXPLANATORY_RE.sub("", block[0], count=1))
        yes_means = next((m.group(1) for p in block if (m := _FOR_RE.match(p))), None)
        no_means = next((m.group(1) for p in block if (m := _AGAINST_RE.match(p))), None)
        has_question = any(_QUESTION_RE.match(p) for p in block)
        if not summary or not yes_means or not no_means or not has_question:
            logger.warning("KS amendment %d didn't match the verified shape — refusing the page", index)
            return None
        results.append({
            "number": str(index),
            "title": f"Constitutional Amendment {index}",
            "origin": ORIGIN,
            "official_summary": summary,
            "fiscal_impact": None,
            "yes_means": clean_text(yes_means),
            "no_means": clean_text(no_means),
            # The explanatory statement and the for/against sentences are
            # written into the Legislature's concurrent resolution.
            "title_authority": ORIGIN,
            "fiscal_authority": None,
        })
    return results


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    page_html = await fetch_text_with_retry(client, _rate_limiter, URL, "KS proposed amendments")
    if page_html is None:
        return None
    try:
        parsed = parse_page(page_html, year)
    except NotYetPublished:
        raise
    except Exception:
        logger.exception("KS proposed amendments page was not parseable")
        return None
    if parsed is None:
        return None
    return [(m, URL) for m in parsed]
