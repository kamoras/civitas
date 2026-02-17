"""Fetch modules for the FEC (Federal Election Commission) API."""

import asyncio
import logging
import re
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

FEC_API_BASE = "https://api.open.fec.gov/v1"
MAX_RETRIES = 3
RETRY_BACKOFF_S = 2.0

_rate_limiter = RateLimiter(settings.FEC_RPS)

# Set to True once we detect the by_contributor endpoint is broken for this run,
# so we skip all 3 URL variants for every remaining senator instead of retrying.
_by_contributor_broken = False


async def _fetch_with_retry(
    client: httpx.AsyncClient, url: str, retries: int = MAX_RETRIES
) -> dict | None:
    """Fetch an FEC API URL with rate limiting, retries, and API key injection."""
    await _rate_limiter.acquire()
    separator = "&" if "?" in url else "?"
    full_url = f"{url}{separator}api_key={settings.DATA_GOV_API_KEY}"

    for attempt in range(1, retries + 1):
        try:
            logger.debug("FEC API: %s (attempt %d)", url, attempt)
            resp = await client.get(full_url, timeout=30.0)

            if resp.status_code == 429:
                wait = RETRY_BACKOFF_S * attempt * 2  # FEC rate limits are tighter
                logger.warning("FEC rate limited, waiting %.1fs...", wait)
                await asyncio.sleep(wait)
                continue

            if resp.status_code >= 400:
                err = httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}: {resp.reason_phrase}",
                    request=resp.request,
                    response=resp,
                )
                # 4xx errors (except 429) are client errors — retrying won't help
                if 400 <= resp.status_code < 500:
                    logger.error("FEC API client error (no retry): %s — %s", url, err)
                    return None
                raise err

            return resp.json()
        except httpx.HTTPStatusError as e:
            if attempt == retries:
                logger.error(
                    "FEC API failed after %d attempts: %s — %s",
                    retries, url, str(e),
                )
                return None
            await asyncio.sleep(RETRY_BACKOFF_S * attempt)
        except Exception as e:
            if attempt == retries:
                logger.error(
                    "FEC API failed after %d attempts: %s — %s",
                    retries, url, str(e),
                )
                return None
            await asyncio.sleep(RETRY_BACKOFF_S * attempt)

    return None


async def find_candidate(
    client: httpx.AsyncClient, db: Session, name: str, state: str
) -> dict | None:
    """Search for a Senate candidate in FEC data.

    Args:
        name: Senator name
        state: Two-letter state code

    Returns:
        Best matching candidate record, or None.
    """
    cache_key = f"candidate-search-{re.sub(r'\\s+', '_', name)}-{state}"
    cached = api_cache_get(db, "fec", cache_key)
    if cached is not None:
        return cached

    # FEC search uses last name
    name_parts = name.split()
    last_name = name_parts[-1] if name_parts else name

    data = await _fetch_with_retry(
        client,
        f"{FEC_API_BASE}/candidates/search/?name={quote(last_name)}&state={state}&office=S&per_page=20",
    )

    if not data or not data.get("results"):
        logger.warning("No FEC candidate found for %s (%s)", name, state)
        api_cache_set(db, "fec", cache_key, None)
        return None

    # Try to match by full name
    results = data["results"]
    match = None
    for c in results:
        c_name = (c.get("name") or "").upper()
        # FEC uses "LASTNAME, FIRSTNAME" format
        if all(part.upper() in c_name for part in name_parts):
            match = c
            break

    if match is None:
        match = results[0]  # Fallback to first result

    logger.debug(
        "FEC candidate match for %s: %s (%s)",
        name, match.get("name"), match.get("candidate_id"),
    )
    api_cache_set(db, "fec", cache_key, match)
    return match


async def fetch_candidate_financials(
    client: httpx.AsyncClient, db: Session, candidate_id: str
) -> list[dict]:
    """Fetch candidate financial totals."""
    cache_key = f"candidate-financials-{candidate_id}"
    cached = api_cache_get(db, "fec", cache_key)
    if cached is not None:
        return cached

    data = await _fetch_with_retry(
        client,
        f"{FEC_API_BASE}/candidate/{candidate_id}/totals/?sort=-cycle&per_page=4",
    )
    results = (data or {}).get("results", [])
    api_cache_set(db, "fec", cache_key, results)
    return results


async def fetch_candidate_committees(
    client: httpx.AsyncClient, db: Session, candidate_id: str
) -> list[dict]:
    """Fetch the candidate's principal campaign committee."""
    cache_key = f"candidate-committees-{candidate_id}"
    cached = api_cache_get(db, "fec", cache_key)
    if cached is not None:
        return cached

    data = await _fetch_with_retry(
        client,
        f"{FEC_API_BASE}/candidate/{candidate_id}/committees/?designation=P&per_page=5",
    )
    results = (data or {}).get("results", [])
    api_cache_set(db, "fec", cache_key, results)
    return results


async def fetch_committee_receipts(
    client: httpx.AsyncClient, db: Session, committee_id: str
) -> list[dict]:
    """Fetch individual contribution receipts to a committee."""
    cache_key = f"committee-receipts-indiv-{committee_id}"
    cached = api_cache_get(db, "fec", cache_key)
    if cached is not None:
        return cached

    # Get individual contributions only (for employer grouping)
    data = await _fetch_with_retry(
        client,
        f"{FEC_API_BASE}/schedules/schedule_a/?committee_id={committee_id}"
        f"&sort=-contribution_receipt_amount&per_page=100&is_individual=true",
    )
    results = (data or {}).get("results", [])
    api_cache_set(db, "fec", cache_key, results)
    return results


async def fetch_pac_receipts(
    client: httpx.AsyncClient, db: Session, committee_id: str
) -> list[dict]:
    """Fetch PAC/committee contributions to a candidate's campaign committee.

    These are contributions from PACs, party committees, and other committees
    directly to the senator's campaign -- the core corporate money flow.
    """
    cache_key = f"committee-receipts-pac-{committee_id}"
    cached = api_cache_get(db, "fec", cache_key)
    if cached is not None:
        return cached

    # is_individual=false returns committee-to-committee contributions (PACs)
    data = await _fetch_with_retry(
        client,
        f"{FEC_API_BASE}/schedules/schedule_a/?committee_id={committee_id}"
        f"&sort=-contribution_receipt_amount&per_page=100&is_individual=false",
    )
    results = (data or {}).get("results", [])
    api_cache_set(db, "fec", cache_key, results)
    return results


async def fetch_aggregated_contributors(
    client: httpx.AsyncClient, db: Session, committee_id: str
) -> list[dict]:
    """Fetch aggregated totals by contributor for a committee.

    Uses best-effort fallbacks for FEC endpoints that don't support the
    preferred `-total` sort field (some committees return 422). The
    function will try a small set of alternative queries before giving up
    and returning an empty list — the pipeline will continue.
    """
    global _by_contributor_broken

    cache_key = f"aggregated-contributors-{committee_id}"
    cached = api_cache_get(db, "fec", cache_key)
    if cached is not None:
        return cached

    # If a previous senator already proved the endpoint is down, skip entirely.
    if _by_contributor_broken:
        logger.debug("Skipping by_contributor for %s (endpoint known broken)", committee_id)
        api_cache_set(db, "fec", cache_key, [])
        return []

    # Try preferred query first, then fall back to alternatives when a
    # 422/other failures are encountered.
    urls = [
        f"{FEC_API_BASE}/schedules/schedule_a/by_contributor/?committee_id={committee_id}&sort=-total&per_page=20",
        f"{FEC_API_BASE}/schedules/schedule_a/by_contributor/?committee_id={committee_id}&sort=-contribution_receipt_amount&per_page=20",
        f"{FEC_API_BASE}/schedules/schedule_a/by_contributor/?committee_id={committee_id}&per_page=20",
    ]

    data = None
    for idx, url in enumerate(urls):
        data = await _fetch_with_retry(client, url)
        if data is not None:
            if idx > 0:
                logger.info("FEC fallback used for %s: %s", committee_id, url)
            break

    if data is None:
        logger.warning(
            "FEC aggregated contributors failed for %s — continuing with empty result",
            committee_id,
        )
        _by_contributor_broken = True

    results = (data or {}).get("results", [])
    api_cache_set(db, "fec", cache_key, results)
    return results
