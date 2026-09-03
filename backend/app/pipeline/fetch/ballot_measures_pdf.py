"""Generic ballot-measure PDF pipeline stage: fetch, cache, and normalize
statewide ballot-measure PDFs for ANY state that has a registered source
and parsing strategy — no API key, no Vote Smart.

This is deliberately NOT one parser that guesses an arbitrary state's PDF
layout. Every state's Secretary of State (or equivalent) publishes its
own format — California's Voter Information Guide has a two-level nested-
column "Quick Reference Guide" page (see ballot_measures_ca.py); other
states may turn out to need a completely different geometric strategy, or
none at all. Guessing a layout risks exactly the failure mode this
codebase treats as worst-case: a plausible-looking but WRONG yes/no
framing (AGENTS.md Core Design Principle 7). So each state gets its own
small, hand-verified page-parser function, built against that state's
real, currently-fetched document — but the fetch/cache/error-handling/
output-shaping code around it (this module) is shared, so adding a state
means writing one parse function + one registry entry
(ballot_measure_pdf_sources.json), not a whole new pipeline.

STRATEGIES maps a source's "strategy" key to that state's whole-document
parser function: `pages -> list[dict]` (pdfplumber's `pdf.pages`, not one
page) with keys number/title/origin/official_summary/fiscal_impact/
yes_means/no_means/title_authority/fiscal_authority (see
ballot_measures_ca.parse_document for the reference implementation and
field-by-field contract). Operating on the whole document rather than one
page at a time is deliberate: California's format happens to fit one
proposition-pair per page, but Massachusetts's does not — a long ballot
question's summary can fill an entire page on its own, pushing that
question's "WHAT YOUR VOTE WILL DO" and fiscal-impact sections onto the
NEXT page (verified on the real document). A strategy that only ever saw
one page at a time could never stitch those back together; one that
scans however many pages it needs can.
"""

import io
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
import pdfplumber

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.ballot_measure_pdf_sources import source_for_state
from app.pipeline.fetch.ballot_measures_ca import parse_document as parse_ca_document
from app.pipeline.fetch.ballot_measures_co import parse_document as parse_co_document
from app.pipeline.fetch.ballot_measures_la import parse_document as parse_la_document
from app.pipeline.fetch.ballot_measures_ma import parse_information_for_voters as parse_ma_document
from app.pipeline.fetch.ballot_measures_mo import fetch_measures as mo_fetch_measures
from app.pipeline.fetch.ballot_measures_va import fetch_measures as va_fetch_measures

logger = logging.getLogger(__name__)

_PDF_LINK_RE = re.compile(r'<a[^>]+href=["\']([^"\']+\.pdf)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_LINK_RE = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

# No state is required to name its guide any particular thing — there's
# no format spec to target, only convention. These are generic election-
# vocabulary terms, not any one state's branding, used to recognize a
# PAGE worth following, not to identify a specific document.
_FOLLOW_KEYWORDS = ("ballot", "measure", "amendment", "proposition", "referendum", "initiative", "voter guide", "pamphlet", "blue book")


def _matches(haystack: str, year: int, keywords: tuple[str, ...], exclude: tuple[str, ...]) -> bool:
    if any(x in haystack for x in exclude):
        return False
    if str(year) not in haystack:
        return False
    if any(k.lower() not in haystack for k in keywords):
        return False
    return True


async def discover_pdf_url(
    client: httpx.AsyncClient, start_url: str, year: int,
    keyword: str | tuple[str, ...] | None = None, exclude: tuple[str, ...] = ("primary",),
    max_pages: int = 8, max_depth: int = 2,
) -> str | None:
    """This cycle's ballot-guide PDF, starting from a state's own election
    site — evergreen against BOTH failure modes states show in practice:
    a PDF filename that changes every cycle (verified: Colorado's real
    filename has no two years alike back to 2012), and a listing page
    that itself moves or gets restructured (no state is bound to any
    particular site layout — there's no format spec here, only whatever
    convention that state's web team happens to use this year).

    `start_url` doesn't have to be the exact page carrying the PDF link —
    a shallow, bounded crawl (`max_depth` hops, `max_pages` total fetches)
    follows same-domain links whose text/href matches generic election
    vocabulary (_FOLLOW_KEYWORDS — "ballot", "voter guide", ... — terms
    no state owns, not any one state's branding) until it finds a PDF
    link matching `year` (and `keyword`, if given — one or more durable
    branding terms like "blue book", not a filename; ALL must be present
    when more than one is given — verified necessary on Louisiana's real
    archive, where "nov" alone also matched an unrelated "November 2024
    Presidential Election" link, needing "nov" AND "constitutional"
    together to pick the right document). Never leaves the starting
    domain, so a page that happens to link an outside site (a news
    article, a different state, Ballotpedia) can't pull this off course.

    None if no confident match anywhere in the crawl — never guesses.
    """
    keywords = (keyword,) if isinstance(keyword, str) else tuple(keyword or ())
    start_domain = urlparse(start_url).netloc
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(start_url, 0)]
    fetched = 0

    while queue and fetched < max_pages:
        url, depth = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            response = await client.get(url, timeout=30.0)
            response.raise_for_status()
            html = response.text
        except Exception:
            continue
        fetched += 1

        for href, text in _PDF_LINK_RE.findall(html):
            haystack = f"{href} {_TAG_RE.sub('', text)}".lower()
            if _matches(haystack, year, keywords, exclude):
                return urljoin(url, href)

        if depth >= max_depth:
            continue
        for href, text in _LINK_RE.findall(html):
            if href.lower().endswith(".pdf"):
                continue
            haystack = f"{href} {_TAG_RE.sub('', text)}".lower()
            if not any(k in haystack for k in _FOLLOW_KEYWORDS):
                continue
            next_url = urljoin(url, href)
            if urlparse(next_url).netloc == start_domain and next_url not in visited:
                queue.append((next_url, depth + 1))

    return None

STRATEGIES = {
    "ca_quick_reference": parse_ca_document,
    "ma_information_for_voters": parse_ma_document,
    "co_quick_ballot_reference": parse_co_document,
    "la_proposed_amendments": parse_la_document,
}

# A strategy here doesn't fit STRATEGIES' `pdf.pages -> list[dict]`
# shape at all — the fetch/parse work can't be reduced to "hand this
# module a PDF's pages", either because the state's measures are several
# SEPARATE documents (Virginia — discovered from an index page, not one
# URL config can name) or because there's no PDF at all (Missouri — a
# single HTML page pdfplumber has nothing to do with). Either way the
# strategy function does its own end-to-end fetching and hands back
# (parsed, source_url) pairs directly. See each module's own docstring
# for why that state specifically needs this and most states don't.
MULTI_DOCUMENT_STRATEGIES = {
    "va_referenda": va_fetch_measures,
    "mo_ballot_measures": mo_fetch_measures,
}

# Longer than Vote Smart's 12h (MEASURE_CACHE_TTL_HOURS in
# ballot_measures.py) — that shorter window exists because Vote Smart's
# own feed can change under us mid-cycle; these are static PDFs a state
# republishes wholesale on the rare occasion they change, so there is
# nothing to catch by polling more often. Matches the platform's general
# 72h API-cache default instead.
CACHE_TTL_HOURS = 72


def is_configured(state: str) -> bool:
    """Whether `state` has both a registered source AND a strategy
    function for it — a source entry with a typo'd/unregistered strategy
    key is a config bug, not a signal to guess at parsing."""
    source = source_for_state(state)
    strategy = source.get("strategy") if source else None
    return strategy in STRATEGIES or strategy in MULTI_DOCUMENT_STRATEGIES


def _to_measure(state: str, parsed: dict, election_date: str, source_url: str) -> dict:
    """One parsed proposition -> the combined raw+detail shape
    election_pipeline._upsert_measure expects. Unlike Vote Smart (a list
    call, then a per-item detail call), a strategy function already
    carries every field in one pass, so the same dict is passed to
    _upsert_measure as both `raw` and `detail` — there's nothing a second
    fetch would add.
    """
    return {
        "id": f"{state}-{election_date}-{parsed['number']}",
        "state": state,
        "election_date": election_date,
        "number": parsed["number"],
        "title": parsed["title"] or f"Proposition {parsed['number']}",
        "official_title": parsed["title"],
        "official_summary": parsed["official_summary"],
        "fiscal_impact": parsed["fiscal_impact"],
        "yes_means": parsed["yes_means"],
        "no_means": parsed["no_means"],
        "measure_type": None,
        "origin": parsed["origin"],
        "source_url": source_url,
    }


async def fetch_state_measures_pdf(
    client: httpx.AsyncClient, db, state: str, year: int, election_date: str,
) -> list[dict] | None:
    """Every statewide ballot measure for `state`'s `year` general
    election, parsed directly from that state's own registered PDF
    source — or None if `state` has no registered source/strategy at all.

    None on a fetch/parse failure too (including the document simply not
    being published yet, which is indistinguishable from a real failure
    at fetch time — same as ballot_measures.fetch_state_measures for Vote
    Smart); [] if the document is real and parses but genuinely contains
    no measure content this cycle (verified real case for CA: a primary
    guide with zero propositions). Same None-vs-[] discipline throughout.
    """
    source = source_for_state(state)
    if source is None:
        return None
    strategy_key = source["strategy"]
    strategy = STRATEGIES.get(strategy_key)
    multi_strategy = MULTI_DOCUMENT_STRATEGIES.get(strategy_key)
    if strategy is None and multi_strategy is None:
        logger.error(
            "Ballot measure PDF source for %s references unknown strategy %r",
            state, strategy_key,
        )
        return None

    cache_key = f"{state}-{year}"
    cached = api_cache_get(db, "ballot_measure_pdf", cache_key, max_age_hours=CACHE_TTL_HOURS)
    if cached is not None:
        return cached.get("measures")

    if multi_strategy is not None:
        try:
            pairs = await multi_strategy(client, year)
        except Exception:
            logger.exception("Multi-document ballot measure fetch failed for %s %d", state, year)
            return None
        if pairs is None:
            return None
        measures = [_to_measure(state, parsed, election_date, url) for parsed, url in pairs]
        api_cache_set(
            db, "ballot_measure_pdf", cache_key, {"measures": measures},
            normal_ttl_hours=CACHE_TTL_HOURS,
        )
        return measures

    if "landing_page_url" in source:
        keyword = source.get("keyword")
        discover_kwargs = {}
        if isinstance(keyword, list):
            keyword = tuple(keyword)
        if "exclude" in source:
            discover_kwargs["exclude"] = tuple(source["exclude"])
        url = await discover_pdf_url(
            client, source["landing_page_url"], year, keyword, **discover_kwargs,
        )
        if url is None:
            logger.warning("Could not discover current ballot measure PDF for %s %d", state, year)
            return None
    else:
        url = source["url_pattern"].format(year=year)
    try:
        response = await client.get(url, timeout=60.0)
        response.raise_for_status()
        pdf_bytes = response.content
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Ballot measure PDF fetch failed for %s %d: HTTP %d",
            state, year, exc.response.status_code,
        )
        return None
    except Exception:
        logger.exception("Ballot measure PDF fetch failed for %s %d", state, year)
        return None

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            measures = [
                _to_measure(state, parsed, election_date, url)
                for parsed in strategy(pdf.pages)
            ]
    except Exception:
        logger.exception("Ballot measure PDF parse failed for %s %d", state, year)
        return None

    api_cache_set(
        db, "ballot_measure_pdf", cache_key, {"measures": measures},
        normal_ttl_hours=CACHE_TTL_HOURS,
    )
    return measures
