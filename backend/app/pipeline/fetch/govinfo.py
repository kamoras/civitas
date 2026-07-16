"""Fetch modules for the GovInfo API (bill text)."""

import asyncio
import logging
import re

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

GOVINFO_API_BASE = "https://api.govinfo.gov"
MAX_RETRIES = 3
RETRY_BACKOFF_S = 2.0

_rate_limiter = RateLimiter(settings.GOVINFO_RPS)


async def _fetch_with_retry(
    client: httpx.AsyncClient, url: str, retries: int = MAX_RETRIES
) -> dict | None:
    """Fetch a GovInfo API URL with rate limiting, retries, and API key injection."""
    await _rate_limiter.acquire()
    separator = "&" if "?" in url else "?"
    full_url = f"{url}{separator}api_key={settings.DATA_GOV_API_KEY}"

    for attempt in range(1, retries + 1):
        try:
            logger.debug("GovInfo API: %s (attempt %d)", url, attempt)
            resp = await client.get(full_url, timeout=DEFAULT_FETCH_TIMEOUT_S)

            if resp.status_code == 429:
                wait = RETRY_BACKOFF_S * attempt
                logger.warning("GovInfo rate limited, waiting %.1fs...", wait)
                await asyncio.sleep(wait)
                continue

            if resp.status_code == 404:
                logger.debug("GovInfo 404: %s", url)
                return None

            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}: {resp.reason_phrase}",
                    request=resp.request,
                    response=resp,
                )

            return resp.json()
        except Exception as e:
            if attempt == retries:
                logger.error(
                    "GovInfo API failed after %d attempts: %s — %s",
                    retries, url, str(e),
                )
                return None
            await asyncio.sleep(RETRY_BACKOFF_S * attempt)

    return None


async def fetch_bill_package(
    client: httpx.AsyncClient,
    db: Session,
    congress: int,
    bill_type: str,
    bill_number: int,
) -> dict | None:
    """Fetch bill text/summary from GovInfo.

    Returns:
        Bill package info with text links, or None.
    """
    cache_key = f"bill-package-{congress}-{bill_type}-{bill_number}"
    cached = api_cache_get(db, "govinfo", cache_key)
    if cached is not None:
        return cached

    # GovInfo uses a specific package ID format for bills
    # BILLS-{congress}{type}{number}{version}
    # Try enrolled version first, then engrossed, then introduced
    versions = ["enr", "eas", "es", "eh", "is", "rs"]
    type_map = {"hr": "hr", "s": "s", "hjres": "hjres", "sjres": "sjres"}
    govinfo_type = type_map.get(bill_type, bill_type)

    for version in versions:
        package_id = f"BILLS-{congress}{govinfo_type}{bill_number}{version}"
        data = await _fetch_with_retry(
            client,
            f"{GOVINFO_API_BASE}/packages/{package_id}/summary",
        )
        if data:
            api_cache_set(db, "govinfo", cache_key, data)
            return data

    logger.warning(
        "No GovInfo package found for %d-%s-%d", congress, bill_type, bill_number
    )
    api_cache_set(db, "govinfo", cache_key, None)
    return None


async def fetch_bill_text(
    client: httpx.AsyncClient,
    db: Session,
    congress: int,
    bill_type: str,
    bill_number: int,
) -> str | None:
    """Fetch the plain text content of a bill from GovInfo.

    Returns:
        Bill text content (HTML stripped), or None.
    """
    cache_key = f"bill-text-{congress}-{bill_type}-{bill_number}"
    cached = api_cache_get(db, "govinfo", cache_key)
    if cached is not None:
        return cached

    pkg = await fetch_bill_package(client, db, congress, bill_type, bill_number)
    if not pkg or not pkg.get("packageId"):
        return None

    # Try to get the HTML version and extract text
    await _rate_limiter.acquire()
    htm_url = (
        f"{GOVINFO_API_BASE}/packages/{pkg['packageId']}/htm"
        f"?api_key={settings.DATA_GOV_API_KEY}"
    )

    try:
        resp = await client.get(htm_url, timeout=DEFAULT_FETCH_TIMEOUT_S)
        if resp.status_code == 200:
            text = resp.text
            # Strip HTML tags for a rough plain text version
            text = re.sub(r"<[^>]+>", " ", text)
            text = text.replace("&nbsp;", " ")
            text = text.replace("&amp;", "&")
            text = text.replace("&lt;", "<")
            text = text.replace("&gt;", ">")
            text = re.sub(r"\s+", " ", text).strip()

            # Truncate to ~8000 chars to keep LLM costs reasonable
            if len(text) > 8000:
                text = text[:8000] + "\n[TRUNCATED]"

            api_cache_set(db, "govinfo", cache_key, text)
            return text
    except Exception as e:
        logger.warning(
            "Failed to fetch bill text for %s: %s", pkg["packageId"], str(e)
        )

    return None
