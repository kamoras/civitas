"""Fetch the @unitedstates/congress-legislators bioguide<->FEC-candidate-ID
crosswalk — a community-maintained, publicly-hosted dataset (used across
the congressional civic-data ecosystem: ProPublica, GovTrack, and other
projects reference it or an equivalent) mapping each member's stable
bioguide_id directly to their real FEC candidate ID(s), with zero name-
matching guesswork.

This replaces name-based FEC search (fec.py's find_candidate) as the
PRIMARY lookup mechanism wherever a bioguide_id is available (i.e. every
sitting senator/representative, since this platform already stores
bioguide_id — see Senator/Representative models). A hand-maintained
nickname table is inherently fragile: it only covers nicknames someone
has already seen and added, and breaks silently for the next member
whose common name diverges from their FEC-filed legal name in a new way.
This crosswalk is authoritative for any member it covers and needs no
guessing at all. fec.py's nickname-matching fallback still exists for the
rare case of a member genuinely missing from this crosswalk (e.g. a
brand-new special-election winner not yet added upstream).

Verified live (2026-07): Bill Cassidy's real entry —
    id:
      bioguide: C001075
      fec:
      - H8LA00017
      - S4LA00107
— confirming both the field names and that a member's fec id list has one
entry per distinct office run for (House vs. Senate), not one per cycle.
"""

import logging

import httpx
import yaml
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

LEGISLATORS_URL = (
    "https://raw.githubusercontent.com/unitedstates/congress-legislators/main/legislators-current.yaml"
)

_RATE_LIMITER = RateLimiter(rps=1.0)
_CACHE_TIER = "congress-legislators"
_CACHE_KEY = "bioguide-to-fec"
# Membership changes (new members seated, deaths, resignations) are rare,
# event-driven, and this is a single static file fetch (not a per-member
# API call) — a day's staleness is harmless, and refetching more often
# than that just adds load to GitHub's raw-content CDN for no benefit.
_CACHE_MAX_AGE_HOURS = 24


async def fetch_bioguide_to_fec_ids(client: httpx.AsyncClient, db: Session) -> dict[str, list[str]]:
    """Returns bioguide_id -> list of FEC candidate IDs (one per distinct
    office the member has ever run for). Empty dict (never None) on
    failure — callers should fall back to name-based search."""
    cached = api_cache_get(db, _CACHE_TIER, _CACHE_KEY, max_age_hours=_CACHE_MAX_AGE_HOURS)
    if cached is not None:
        return cached["data"]

    resp = await fetch_with_retry(
        client, _RATE_LIMITER, "GET", LEGISLATORS_URL,
        log_label="congress-legislators crosswalk",
    )
    if resp is None or resp.status_code != 200:
        logger.warning("Failed to fetch congress-legislators crosswalk (%s)", LEGISLATORS_URL)
        return {}

    try:
        legislators = yaml.safe_load(resp.text)
    except Exception:
        logger.exception("Failed to parse congress-legislators YAML")
        return {}

    result: dict[str, list[str]] = {}
    for legislator in legislators or []:
        ids = (legislator or {}).get("id") or {}
        bioguide = ids.get("bioguide")
        fec_ids = ids.get("fec")
        if bioguide and fec_ids:
            result[bioguide] = list(fec_ids)

    if not result:
        logger.warning("congress-legislators crosswalk parsed to zero entries — format may have changed")
        return {}

    api_cache_set(db, _CACHE_TIER, _CACHE_KEY, {"data": result})
    return result


def select_fec_id_for_office(fec_ids: list[str], office: str) -> str | None:
    """Each real FEC candidate ID encodes its office as its first
    character (H=House, S=Senate, P=President) — picks the id matching
    the office being looked up. A member who has run for more than one
    chamber (e.g. a House member later elected to the Senate) has one id
    per chamber in the crosswalk's list; this picks theirs for THIS
    office, not just the first/most recent entry."""
    for fec_id in fec_ids:
        if fec_id[:1].upper() == office.upper():
            return fec_id
    return None
