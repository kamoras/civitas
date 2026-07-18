"""Fetch Senate floor proceedings from the Congressional Record via GovInfo API.

The Congressional Record (CREC) is published daily when Congress is in session.
Each day's record contains transcripts of all floor proceedings.  We extract
Senate sections and parse them into per-senator speaking turns to measure
whether senators are actively advocating for their stated platform promises —
even when gridlock prevents bills from passing.

Data flow:
  1. List recent CREC daily packages via the GovInfo collections endpoint.
  2. For each day, list granules and filter to Senate-class sections.
  3. Fetch the HTML text of each Senate granule.
  4. Parse speaker-attributed segments using standard Congressional Record
     formatting ("Mr. LASTNAME.", "Mrs. LASTNAME.", etc.).
  5. Aggregate speaking turns per senator (by uppercase last name).

All intermediate results are cached via the pipeline cache layer.
"""

import logging
import re
from datetime import datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S, fetch_with_retry, redact_url
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

GOVINFO_API_BASE = "https://api.govinfo.gov"

_rate_limiter = RateLimiter(settings.GOVINFO_RPS)


# ── Internal HTTP helpers ────────────────────────────────────────


async def _fetch_json(client: httpx.AsyncClient, url: str) -> dict | None:
    """Fetch a GovInfo JSON endpoint with rate limiting and retries."""
    separator = "&" if "?" in url else "?"
    full_url = f"{url}{separator}api_key={settings.DATA_GOV_API_KEY}"
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", full_url,
        retry_on_4xx=False, log_label="GovInfo CREC",
    )
    return resp.json() if resp is not None else None


async def _fetch_htm(client: httpx.AsyncClient, url: str) -> str:
    """Fetch raw HTML from a GovInfo granule endpoint."""
    await _rate_limiter.acquire()
    separator = "&" if "?" in url else "?"
    full_url = f"{url}{separator}api_key={settings.DATA_GOV_API_KEY}"
    try:
        resp = await client.get(full_url, timeout=DEFAULT_FETCH_TIMEOUT_S)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        # This request bypasses fetch_with_retry (no retry needed for a raw
        # HTML fetch), so the api_key redaction it does isn't applied here —
        # the exception message can embed full_url, so redact directly.
        logger.debug("GovInfo HTM fetch failed: %s", redact_url(str(e)))
    return ""


def _strip_html(html: str) -> str:
    """Strip HTML tags and normalize whitespace/entities."""
    text = re.sub(r"<[^>]+>", " ", html)
    for entity, char in [
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
    ]:
        text = text.replace(entity, char)
    return re.sub(r"\s+", " ", text).strip()


# ── Public fetch API ─────────────────────────────────────────────


async def fetch_crec_packages(
    client: httpx.AsyncClient,
    db: Session,
    days_back: int = 60,
) -> list[str]:
    """List recent CREC daily package IDs (e.g. ``CREC-2025-02-20``).

    Uses the GovInfo collections endpoint to find Congressional Record
    issues published in the last *days_back* days.
    """
    cache_key = f"crec-packages-{days_back}d"
    cached = api_cache_get(db, "govinfo", cache_key)
    if cached is not None:
        return cached

    start = (datetime.utcnow() - timedelta(days=days_back)).strftime(
        "%Y-%m-%dT00:00:00Z"
    )
    logger.info("Fetching CREC package index (last %d days)...", days_back)

    packages: list[str] = []
    offset = 0

    while True:
        data = await _fetch_json(
            client,
            f"{GOVINFO_API_BASE}/collections/CREC/{start}"
            f"?pageSize=100&offset={offset}",
        )
        if not data:
            break

        batch = data.get("packages", [])
        for pkg in batch:
            pid = pkg.get("packageId", "")
            if pid.startswith("CREC-"):
                packages.append(pid)

        if len(batch) < 100:
            break
        offset += 100

    logger.info("Found %d CREC daily packages", len(packages))
    api_cache_set(db, "govinfo", cache_key, packages)
    return packages


async def fetch_senate_granules(
    client: httpx.AsyncClient,
    db: Session,
    package_id: str,
) -> list[dict]:
    """List Senate-section granules within a daily CREC package.

    Filters to granules whose ``granuleClass`` contains ``SENATE``.
    """
    cache_key = f"crec-senate-gran-{package_id}"
    cached = api_cache_get(db, "govinfo", cache_key)
    if cached is not None:
        return cached

    data = await _fetch_json(
        client,
        f"{GOVINFO_API_BASE}/packages/{package_id}/granules?pageSize=100&offsetMark=*",
    )
    if not data:
        api_cache_set(db, "govinfo", cache_key, [])
        return []

    granules: list[dict] = []
    for g in data.get("granules", []):
        gc = (g.get("granuleClass") or "").upper()
        if "SENATE" not in gc:
            continue
        granules.append({
            "granuleId": g.get("granuleId", ""),
            "title": g.get("title", ""),
            "granuleClass": gc,
        })

    api_cache_set(db, "govinfo", cache_key, granules)
    return granules


async def fetch_granule_text(
    client: httpx.AsyncClient,
    db: Session,
    package_id: str,
    granule_id: str,
    max_chars: int = 15_000,
) -> str:
    """Fetch the plain-text content of a single CREC granule."""
    cache_key = f"crec-gtext-{granule_id}"
    cached = api_cache_get(db, "govinfo", cache_key)
    if cached is not None:
        return cached

    html = await _fetch_htm(
        client,
        f"{GOVINFO_API_BASE}/packages/{package_id}/granules/{granule_id}/htm",
    )
    if not html:
        api_cache_set(db, "govinfo", cache_key, "")
        return ""

    text = _strip_html(html)
    if len(text) > max_chars:
        text = text[:max_chars]

    api_cache_set(db, "govinfo", cache_key, text)
    return text


# ── Parsing ──────────────────────────────────────────────────────

_SPEAKER_RE = re.compile(
    r"(?:Mr|Mrs|Ms)\.\s+([A-Z][A-Z\-\' ]{1,25})\."
)

_SKIP_SPEAKERS = frozenset({
    "PRESIDENT", "PRESIDING OFFICER", "CHAIR", "CHAIRMAN",
    "CHAIRWOMAN", "SPEAKER", "CLERK",
})


def parse_speaking_turns(text: str) -> list[dict]:
    """Split Congressional Record text into speaker-attributed segments.

    Each turn is identified by the standard ``Mr. LASTNAME.`` pattern.
    Returns list of dicts with ``speaker`` (uppercase last name) and
    ``text`` (first 400 chars of the segment).
    """
    markers = list(_SPEAKER_RE.finditer(text))
    if not markers:
        return []

    turns: list[dict] = []
    for i, m in enumerate(markers):
        speaker = m.group(1).strip().rstrip(".")
        if speaker in _SKIP_SPEAKERS:
            continue

        start = m.end()
        end = (
            markers[i + 1].start()
            if i + 1 < len(markers)
            else min(start + 600, len(text))
        )
        segment = text[start:end].strip()

        if len(segment) > 40:
            turns.append({"speaker": speaker, "text": segment[:400]})

    return turns


# ── High-level aggregation ───────────────────────────────────────


async def fetch_floor_remarks(
    client: httpx.AsyncClient,
    db: Session,
    days_back: int = 60,
    max_granules_per_day: int = 8,
) -> dict[str, list[dict]]:
    """Fetch and parse all Senate floor remarks, grouped by speaker.

    Returns a dict mapping **UPPERCASE last name** to a list of remark
    dicts with keys ``date``, ``text``, and ``title``.

    Args:
        client: HTTP client.
        db: Database session for caching.
        days_back: How far back to search for CREC packages.
        max_granules_per_day: Cap on granule fetches per daily package
            to keep API request volume manageable.
    """
    cache_key = f"floor-remarks-{days_back}d-v1"
    cached = api_cache_get(db, "govinfo", cache_key)
    if cached is not None:
        return cached

    packages = await fetch_crec_packages(client, db, days_back)

    remarks: dict[str, list[dict]] = {}
    days_processed = 0

    for pkg_id in packages:
        date_str = pkg_id.replace("CREC-", "")

        granules = await fetch_senate_granules(client, db, pkg_id)
        if not granules:
            continue

        for granule in granules[:max_granules_per_day]:
            text = await fetch_granule_text(
                client, db, pkg_id, granule["granuleId"]
            )
            if not text:
                continue

            for turn in parse_speaking_turns(text):
                speaker = turn["speaker"]
                remarks.setdefault(speaker, []).append({
                    "date": date_str,
                    "text": turn["text"],
                    "title": granule.get("title", ""),
                })

        days_processed += 1

    total_remarks = sum(len(v) for v in remarks.values())
    logger.info(
        "Parsed floor remarks: %d speakers, %d days, %d total remarks",
        len(remarks), days_processed, total_remarks,
    )
    api_cache_set(db, "govinfo", cache_key, remarks)
    return remarks
