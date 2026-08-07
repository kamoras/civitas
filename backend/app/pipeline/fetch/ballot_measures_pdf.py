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

STRATEGIES maps a source's "strategy" key to that state's page-parser
function: `page -> list[dict]` with keys number/title/origin/
official_summary/fiscal_impact/yes_means/no_means/title_authority/
fiscal_authority (see ballot_measures_ca.parse_quick_reference_page for
the reference implementation and field-by-field contract). Add a new
state's strategy function here.
"""

import io
import logging

import httpx
import pdfplumber

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.ballot_measure_pdf_sources import source_for_state
from app.pipeline.fetch.ballot_measures_ca import parse_quick_reference_page

logger = logging.getLogger(__name__)

STRATEGIES = {
    "ca_quick_reference": parse_quick_reference_page,
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
    return source is not None and source.get("strategy") in STRATEGIES


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
    strategy = STRATEGIES.get(source["strategy"])
    if strategy is None:
        logger.error(
            "Ballot measure PDF source for %s references unknown strategy %r",
            state, source.get("strategy"),
        )
        return None

    cache_key = f"{state}-{year}"
    cached = api_cache_get(db, "ballot_measure_pdf", cache_key, max_age_hours=CACHE_TTL_HOURS)
    if cached is not None:
        return cached.get("measures")

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
        measures = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                for parsed in strategy(page):
                    measures.append(_to_measure(state, parsed, election_date, url))
    except Exception:
        logger.exception("Ballot measure PDF parse failed for %s %d", state, year)
        return None

    api_cache_set(
        db, "ballot_measure_pdf", cache_key, {"measures": measures},
        normal_ttl_hours=CACHE_TTL_HOURS,
    )
    return measures
