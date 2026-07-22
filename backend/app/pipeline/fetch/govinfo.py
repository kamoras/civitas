"""Fetch modules for the GovInfo API (bill text)."""

import logging
import re

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S, fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

GOVINFO_API_BASE = "https://api.govinfo.gov"
MAX_RETRIES = 3
RETRY_BACKOFF_S = 2.0

_rate_limiter = RateLimiter(settings.GOVINFO_RPS)


async def _fetch_with_retry(
    client: httpx.AsyncClient, url: str, retries: int = MAX_RETRIES
) -> dict | None:
    """Fetch a GovInfo API URL with rate limiting, retries, and API key injection.

    Thin wrapper over the shared http_utils.fetch_with_retry. A 404 is an
    expected miss (bill text not published yet), so it terminates immediately
    via no_retry_statuses rather than being retried; the api-key-bearing URL
    is passed via request_url so the key is never part of the logged `url`.
    """
    separator = "&" if "?" in url else "?"
    full_url = f"{url}{separator}api_key={settings.DATA_GOV_API_KEY}"
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url,
        retries=retries,
        backoff_s=RETRY_BACKOFF_S,
        no_retry_statuses=(404,),
        timeout=DEFAULT_FETCH_TIMEOUT_S,
        log_label="GovInfo API",
        request_url=full_url,
    )
    return resp.json() if resp is not None else None


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
    # Try enrolled first, then engrossed, then reported, then introduced.
    # Both chambers' versions are listed: the original list had "is"/"rs"
    # (Senate introduced/reported) but no "ih"/"rh", so a House bill that
    # hadn't passed a chamber never resolved to any package and got no
    # full text at all — a systematic chamber asymmetry in classifier input.
    versions = ["enr", "eas", "es", "eh", "rs", "rh", "is", "ih"]
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
