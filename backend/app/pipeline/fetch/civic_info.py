"""Town-level ballot content via Google Civic Information API's
voterInfoQuery — candidate contests and referendums for one CURATED town's
representative address (see town_directory.py).

Never called with a visitor's address. voterInfoQuery is address-keyed, and
the whole reason the statewide-only feature (ballot_measures.py) exists
instead of this is that sending a VISITOR's address off-box is the one
thing this platform's architecture exists to prevent. This module only ever
sends a fixed, publicly-known civic-building address that WE chose — the
input is the same for every visitor who picks that town, so nothing
visitor-specific ever leaves the server.

That said, this is a real approximation, not a precinct-accurate lookup:
voterInfoQuery resolves to the ONE precinct the representative address is
in, and a town can contain more than one (a different school board seat,
water district, or city council ward a few blocks over). Town-level, not
county- or state-level, was chosen specifically to keep that error small —
see docs/ballot-measures.md's town-selection notes — but it is still an
error, and the frontend must say so next to whatever renders from here.

SCHEMA CAVEAT: parsed from Google's public documentation, not a live
response — no GOOGLE_CIVIC_API_KEY was available to verify against in the
environment this was written in. Every field is read defensively (`_text`)
for exactly that reason: an unverified assumption about a field name should
cost us that field, not the whole lookup. Verify against a real response
before trusting this in production (see town_directory.json's `_note`).
"""

import logging

import httpx

from app.config import settings
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.town_directory import address_for_town

logger = logging.getLogger(__name__)

CIVIC_BASE = "https://www.googleapis.com/civicinfo/v2"

# Shorter than the platform's default 72h API cache, same reasoning as
# ballot_measures.MEASURE_CACHE_TTL_HOURS: election content is corrected
# continuously through a cycle, and a stale local ballot is exactly the
# failure this feature can't have.
TOWN_CACHE_TTL_HOURS = 12


def _text(raw: dict, *keys: str) -> str | None:
    """First non-empty string among `keys`, or None. Same contract as
    ballot_measures._text — not shared across modules since the two
    response shapes are unrelated, but the discipline is identical: a
    shape change should cost us a field, not the whole lookup."""
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def is_configured() -> bool:
    """Whether town lookups can run at all. Independent of
    ballot_measures.is_configured() — a deployment can run the statewide
    feature without this one, or (once verified) this without that one."""
    return bool(settings.GOOGLE_CIVIC_API_KEY)


def _parse_candidate_contest(raw: dict) -> dict | None:
    office = _text(raw, "office")
    if not office:
        return None
    candidates = []
    for cand in raw.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        name = _text(cand, "name")
        if not name:
            continue
        candidates.append({
            "name": name,
            "party": _text(cand, "party"),
            "candidateUrl": _text(cand, "candidateUrl"),
        })
    return {"kind": "contest", "office": office, "candidates": candidates}


def _parse_referendum(raw: dict) -> dict | None:
    title = _text(raw, "referendumTitle")
    if not title:
        return None
    return {
        "kind": "measure",
        "title": title,
        "subtitle": _text(raw, "referendumSubtitle"),
        "text": _text(raw, "referendumText", "referendumBrief"),
        "url": _text(raw, "referendumUrl"),
        "passageThreshold": _text(raw, "referendumPassageThreshold"),
    }


def _parse_contests(payload: dict) -> list[dict]:
    parsed = []
    for raw in payload.get("contests") or []:
        if not isinstance(raw, dict):
            continue
        # Referendum fields take priority: a contest carrying
        # referendumTitle is a measure regardless of what `type` says,
        # since `type` free-texts across jurisdictions ("Referendum",
        # "Ballot Measure", ...) and the referendum fields are the
        # reliable discriminator.
        item = _parse_referendum(raw) or _parse_candidate_contest(raw)
        if item:
            parsed.append(item)
    return parsed


async def fetch_town_ballot(
    client: httpx.AsyncClient, db, state: str, town: str,
) -> dict | None:
    """Contests and measures at `town`'s representative address, or None
    on missing config, an unknown town, or a fetch/parse failure.

    None here means "we could not get an answer" — the caller renders
    that as the town's own ingest_failed, same tri-state discipline
    ballot_measures.fetch_state_measures uses, never as "no local races".
    """
    if not is_configured():
        return None

    address = address_for_town(state, town)
    if address is None:
        return None

    cache_key = f"civic-town-{state.upper()}-{town.casefold()}"
    cached = api_cache_get(db, "google_civic", cache_key, max_age_hours=TOWN_CACHE_TTL_HOURS)
    if cached is not None:
        return {"contests": _parse_contests(cached), "address": address}

    try:
        response = await client.get(
            f"{CIVIC_BASE}/voterinfo",
            params={"key": settings.GOOGLE_CIVIC_API_KEY, "address": address},
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        logger.exception("Civic Info lookup failed for %s, %s", town, state)
        return None

    api_cache_set(db, "google_civic", cache_key, payload, normal_ttl_hours=TOWN_CACHE_TTL_HOURS)
    return {"contests": _parse_contests(payload), "address": address}
