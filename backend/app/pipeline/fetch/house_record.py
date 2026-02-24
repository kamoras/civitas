"""Fetch House floor proceedings from the Congressional Record via GovInfo API.

Mirrors the Senate Congressional Record fetcher but filters to HOUSE
granule classes. Proceedings are parsed into speaker-attributed segments
for ingestion into the explore document store.
"""

import logging
import re

import httpx
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.congressional_record import (
    _fetch_json,
    _strip_html,
    _fetch_htm,
    fetch_crec_packages,
)

logger = logging.getLogger(__name__)

GOVINFO_API_BASE = "https://api.govinfo.gov"

_SPEAKER_RE = re.compile(
    r"(?:Mr|Mrs|Ms|Miss)\.\s+([A-Z][A-Z\-\' ]{1,25})\."
)

_SKIP_SPEAKERS = frozenset({
    "SPEAKER", "SPEAKER pro tempore",
    "CHAIR", "CHAIRMAN", "CHAIRWOMAN",
    "CLERK", "PRESIDENT",
})


async def fetch_house_granules(
    client: httpx.AsyncClient,
    db: Session,
    package_id: str,
) -> list[dict]:
    """List House-section granules within a daily CREC package."""
    cache_key = f"crec-house-gran-{package_id}"
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
        if "HOUSE" not in gc or "SENATE" in gc:
            continue
        granules.append({
            "granuleId": g.get("granuleId", ""),
            "title": g.get("title", ""),
            "granuleClass": gc,
        })

    api_cache_set(db, "govinfo", cache_key, granules)
    return granules


async def fetch_house_granule_text(
    client: httpx.AsyncClient,
    db: Session,
    package_id: str,
    granule_id: str,
    max_chars: int = 15_000,
) -> str:
    """Fetch the plain-text content of a single House CREC granule."""
    cache_key = f"crec-htext-{granule_id}"
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


def parse_house_speaking_turns(text: str) -> list[dict]:
    """Split House Congressional Record text into speaker-attributed segments."""
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
            turns.append({"speaker": speaker, "text": segment[:500]})

    return turns


async def fetch_house_floor_remarks(
    client: httpx.AsyncClient,
    db: Session,
    days_back: int = 60,
    max_granules_per_day: int = 8,
) -> list[dict]:
    """Fetch House floor remarks as flat list of document dicts.

    Each dict has keys: speaker, text, date, title — ready for explore
    document ingestion.
    """
    cache_key = f"house-floor-remarks-{days_back}d-v1"
    cached = api_cache_get(db, "govinfo", cache_key)
    if cached is not None:
        return cached

    packages = await fetch_crec_packages(client, db, days_back)

    all_remarks: list[dict] = []

    for pkg_id in packages:
        date_str = pkg_id.replace("CREC-", "")

        granules = await fetch_house_granules(client, db, pkg_id)
        if not granules:
            continue

        for granule in granules[:max_granules_per_day]:
            text = await fetch_house_granule_text(
                client, db, pkg_id, granule["granuleId"]
            )
            if not text:
                continue

            for turn in parse_house_speaking_turns(text):
                all_remarks.append({
                    "speaker": turn["speaker"],
                    "text": turn["text"],
                    "date": date_str,
                    "title": granule.get("title", ""),
                })

    logger.info("Fetched %d House floor remarks", len(all_remarks))
    api_cache_set(db, "govinfo", cache_key, all_remarks)
    return all_remarks
