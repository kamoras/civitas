"""Fetch House floor proceedings from the Congressional Record via GovInfo API.

Mirrors the Senate Congressional Record fetcher but filters to HOUSE
granule classes. Proceedings are parsed into speaker-attributed segments
for ingestion into the explore document store.
"""

import logging

import httpx
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.congressional_record import (
    _strip_html,
    _fetch_htm,
    SPEAKER_RE,
    fetch_crec_packages,
    list_package_granules,
    speaker_of,
)

logger = logging.getLogger(__name__)

GOVINFO_API_BASE = "https://api.govinfo.gov"


_SKIP_SPEAKERS = frozenset({
    "SPEAKER", "SPEAKER pro tempore",
    "CHAIR", "CHAIRMAN", "CHAIRWOMAN",
    "CLERK", "PRESIDENT",
})


async def fetch_house_granules(
    client: httpx.AsyncClient,
    db: Session,
    package_id: str,
) -> list[dict] | None:
    """List House-section granules within a daily CREC package, or None
    when the listing could not be fetched (not cached, retried next run)."""
    cache_key = f"crec-house-gran-v2-{package_id}"  # v2: every page, not the first 100
    cached = api_cache_get(db, "govinfo", cache_key)
    if cached is not None:
        return cached

    listed = await list_package_granules(client, package_id)
    if listed is None:
        return None

    granules: list[dict] = []
    for g in listed:
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
    markers = list(SPEAKER_RE.finditer(text))
    if not markers:
        return []

    turns: list[dict] = []
    for i, m in enumerate(markers):
        name, speaker = speaker_of(m)
        if name in _SKIP_SPEAKERS:
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
    cache_key = f"house-floor-remarks-{days_back}d-v3"  # v3: every House granule is listed
    cached = api_cache_get(db, "govinfo", cache_key)
    if cached is not None:
        return cached

    packages = await fetch_crec_packages(client, db, days_back)

    all_remarks: list[dict] = []
    complete = True

    for pkg_id in packages:
        date_str = pkg_id.replace("CREC-", "")

        granules = await fetch_house_granules(client, db, pkg_id)
        if granules is None:
            complete = False  # this run's result is partial: not cached
            continue
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
    if complete:
        api_cache_set(db, "govinfo", cache_key, all_remarks)
    return all_remarks
