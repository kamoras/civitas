"""Statewide ballot-measure ingestion.

Source: Vote Smart's Measure API (api.votesmart.org) — a free, keyed,
nonpartisan API with a dedicated ballot-measure class returning the
measure's official title, summary, ballot text, and the state's own
yes/no framing. Chosen over the alternatives because:

  - Google Civic's voterInfoQuery is keyed on a voter ADDRESS, so using it
    would mean shipping visitor addresses off-box — the one thing this
    platform's architecture exists to prevent.
  - CTCL's Ballot Information Project stopped updating in Jan 2026 (and
    was a candidate dataset, not a measure one).
  - Ballotpedia's terms bar automated extraction and its pricing is out of
    reach for this project.
  - Per-state Secretary of State adapters are the authoritative source and
    remain the intended phase-2 upgrade, but that is ~50 bespoke HTML/PDF
    parsers against independently-redesigned government sites; this gets
    the feature honest and shipping first, with the state's own page
    always linked as the authority.

DESIGN CONSTRAINT that shapes everything below: every field this module
returns is stored and rendered verbatim. Nothing here summarizes,
paraphrases, infers, or normalizes prose. In particular `yes_means` /
`no_means` are lifted from the source's own framing or left None — never
derived from the title — because the obvious derivation (yes = enact) is
exactly inverted on a veto referendum, where "approved" RETAINS the law
under challenge.

Fields are read defensively (`_text`) rather than by strict schema: an
upstream shape change should cost us a field, not the whole state's
ballot.
"""

import logging

import httpx

from app.config import settings
from app.pipeline.cache import api_cache_get, api_cache_set

logger = logging.getLogger(__name__)

VOTESMART_BASE = "https://api.votesmart.org"

# Vote Smart's own numeric state ids are just the 2-letter postal codes for
# every state, so no crosswalk table is needed — but the API rejects
# lowercase, hence the explicit upper() at the call site.

# Cache TTL for measure lookups. Shorter than the platform's default 72h
# API cache: measures are certified and struck continuously through a
# cycle (courts removed seven measures in a single two-week window in
# July 2026), and a stale ballot is the specific failure this feature
# cannot have.
MEASURE_CACHE_TTL_HOURS = 12


def _text(raw: dict, *keys: str) -> str | None:
    """First non-empty string among `keys`, or None.

    Vote Smart returns absent fields as empty strings and occasionally as
    a nested {"@attributes": ...} object; anything that isn't a non-empty
    string is treated as absent rather than coerced, so a shape change
    yields a missing field instead of the literal text "{}" rendered on a
    ballot page.
    """
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _as_list(payload) -> list[dict]:
    """Vote Smart collapses single-element lists to a bare object."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def is_configured() -> bool:
    """Whether measure ingestion can run at all.

    Without a key the phase is skipped and every state stays
    NOT_YET_COVERED — which the frontend renders as an explicit "we don't
    have this state's measures yet, here's the official lookup" block.
    That is the intended un-configured behavior: no key must never
    produce a page that reads as "this state has no measures."
    """
    return bool(settings.VOTESMART_API_KEY)


async def fetch_state_measures(
    client: httpx.AsyncClient, db, state: str, year: int,
) -> list[dict] | None:
    """Every measure Vote Smart lists for `state` in `year`.

    Returns None on a fetch/parse failure — distinct from [] ("this state
    genuinely has no measures this year"). The caller maps those two onto
    MeasureCoverage.INGEST_FAILED and CONFIRMED_NONE respectively;
    collapsing them would let a broken parser render as "no measures on
    your ballot", which is the worst output this feature can produce.
    """
    if not is_configured():
        return None

    cache_key = f"votesmart-measures-{state}-{year}"
    cached = api_cache_get(db, "votesmart", cache_key, max_age_hours=MEASURE_CACHE_TTL_HOURS)
    if cached is not None:
        return _parse_measure_list(cached, state)

    url = f"{VOTESMART_BASE}/Measure.getMeasuresByYearState"
    params = {
        "key": settings.VOTESMART_API_KEY,
        "o": "JSON",
        "year": str(year),
        "stateId": state.upper(),
    }
    try:
        response = await client.get(url, params=params, timeout=30.0)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        logger.exception("Vote Smart measure fetch failed for %s %d", state, year)
        return None

    # Vote Smart signals "no measures for this state/year" with an error
    # envelope rather than an empty list, so an errored payload is only
    # trusted as "none" when it says so explicitly; anything else is a
    # failure and must not be reported as an empty ballot.
    error_text = ((payload or {}).get("error") or {}).get("errorMessage")
    if error_text:
        if "no measures" in error_text.lower() or "not found" in error_text.lower():
            api_cache_set(db, "votesmart", cache_key, payload,
                          normal_ttl_hours=MEASURE_CACHE_TTL_HOURS)
            return []
        logger.warning("Vote Smart error for %s %d: %s", state, year, error_text)
        return None

    api_cache_set(db, "votesmart", cache_key, payload,
                  normal_ttl_hours=MEASURE_CACHE_TTL_HOURS)
    return _parse_measure_list(payload, state)


def _parse_measure_list(payload: dict, state: str) -> list[dict] | None:
    measures = ((payload or {}).get("measures") or {}).get("measure")
    if measures is None:
        return None
    parsed = []
    for raw in _as_list(measures):
        measure_id = _text(raw, "measureId")
        if not measure_id:
            continue
        parsed.append({
            "id": f"vs-{measure_id}",
            "source_measure_id": measure_id,
            "state": state.upper(),
            "number": _text(raw, "measureCode", "code") or "",
            "title": _text(raw, "title") or "",
            "election_date": _text(raw, "electionDate", "date"),
            "outcome": _text(raw, "outcome"),
        })
    return parsed


async def fetch_measure_detail(
    client: httpx.AsyncClient, db, source_measure_id: str,
) -> dict | None:
    """Official title/summary/text and the state's own yes/no framing.

    Every value returned here is verbatim source text. `yes_means` /
    `no_means` come from the source's own fields and are None when it
    publishes none — see this module's docstring for why they are never
    derived.
    """
    if not is_configured():
        return None

    cache_key = f"votesmart-measure-{source_measure_id}"
    cached = api_cache_get(db, "votesmart", cache_key, max_age_hours=MEASURE_CACHE_TTL_HOURS)
    payload = cached
    if payload is None:
        url = f"{VOTESMART_BASE}/Measure.getMeasure"
        params = {
            "key": settings.VOTESMART_API_KEY,
            "o": "JSON",
            "measureId": source_measure_id,
        }
        try:
            response = await client.get(url, params=params, timeout=30.0)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            logger.exception("Vote Smart measure detail failed for %s", source_measure_id)
            return None
        api_cache_set(db, "votesmart", cache_key, payload,
                      normal_ttl_hours=MEASURE_CACHE_TTL_HOURS)

    raw = (payload or {}).get("measure")
    if not isinstance(raw, dict):
        return None

    return {
        "official_title": _text(raw, "title"),
        "official_summary": _text(raw, "summary", "summaryText"),
        "measure_text": _text(raw, "measureText", "text"),
        "fiscal_impact": _text(raw, "fiscalImpact", "fiscalNote"),
        "yes_means": _text(raw, "yes", "proText"),
        "no_means": _text(raw, "no", "conText"),
        "measure_type": _text(raw, "type", "measureType"),
        "origin": _text(raw, "origin", "source"),
        "source_url": _text(raw, "url", "sourceUrl"),
        "election_date": _text(raw, "electionDate", "date"),
    }
