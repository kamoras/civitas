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

# How old a cached package or text may be and still stand in for a failed read.
_STALE_TEXT_HOURS = 24 * 365 * 2


class GovinfoUnavailable(RuntimeError):
    """GovInfo could not be read for a bill (not a 404) and nothing earlier
    is cached: the caller names the bill rather than classify it as one with
    no text published."""


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
    cached = api_cache_get(db, "govinfo", cache_key, spread=True)
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

    failed = False
    for version in versions:
        package_id = f"BILLS-{congress}{govinfo_type}{bill_number}{version}"
        url = f"{GOVINFO_API_BASE}/packages/{package_id}/summary"
        resp = await fetch_with_retry(
            client, _rate_limiter, "GET", url,
            retries=MAX_RETRIES,
            backoff_s=RETRY_BACKOFF_S,
            expected_statuses=(404,),
            timeout=DEFAULT_FETCH_TIMEOUT_S,
            log_label="GovInfo API",
            request_url=f"{url}?api_key={settings.DATA_GOV_API_KEY}",
        )
        if resp is None:
            failed = True  # not a 404: this version is unknown, not absent
            continue
        if resp.status_code == 404:
            continue
        try:
            data = resp.json()
        except ValueError:
            failed = True
            continue
        if data:
            api_cache_set(db, "govinfo", cache_key, data)
            return data

    if failed:
        # Some version couldn't be read, so "no text published" isn't known:
        # the last package read stands in, and with none the caller is told.
        stale = api_cache_get(db, "govinfo", cache_key, max_age_hours=_STALE_TEXT_HOURS)
        if stale is not None:
            return stale
        raise GovinfoUnavailable(f"package of {bill_type.upper()}.{bill_number} ({congress})")
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
        Bill text content (HTML stripped), or None when none is published;
        the last text read when GovInfo fails, GovinfoUnavailable with none.
    """
    cache_key = f"bill-text-{congress}-{bill_type}-{bill_number}"
    cached = api_cache_get(db, "govinfo", cache_key, spread=True)
    if cached is not None:
        return cached

    try:
        pkg = await fetch_bill_package(client, db, congress, bill_type, bill_number)
    except GovinfoUnavailable:
        return _last_text(db, cache_key, f"package of {bill_type.upper()}.{bill_number} ({congress})")
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
        return _last_text(db, cache_key, f"text of {pkg['packageId']}")
    else:
        if resp.status_code != 404:
            return _last_text(db, cache_key, f"text of {pkg['packageId']} (HTTP {resp.status_code})")

    return None


def _last_text(db: Session, cache_key: str, label: str) -> str:
    """The last text read for a bill whose read just failed, or
    GovinfoUnavailable with none: an outage is not a bill with no text."""
    stale = api_cache_get(db, "govinfo", cache_key, max_age_hours=_STALE_TEXT_HOURS)
    if stale is not None:
        logger.warning("GovInfo %s failed — using the last text read", label)
        return stale
    raise GovinfoUnavailable(label)
