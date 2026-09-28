"""The hand-verified town -> ballot-PDF-URL directory that powers direct
ballot-PDF ingestion (see ballot_pdf.py).

Same volume/bundled dual-path read as town_directory.py/ballot_lookup.py:
app/data/ is COPY'd into the Docker image and is NOT writable at runtime.
"""

import logging
import os
from typing import Any

from app.file_cache import Uncached, read_json_preferring

logger = logging.getLogger(__name__)

_BUNDLED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "ballot_pdf_sources.json")
_VOLUME_PATH = "/data/ballot_pdf_sources.json"

_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        data = read_json_preferring(_VOLUME_PATH, _BUNDLED_PATH, default={})
    except Uncached as unreadable:
        # A volume copy that exists but can't be read right now: its
        # fallback serves this once, and the next call reads again —
        # kept, it would stand for the life of the process.
        return unreadable.value if isinstance(unreadable.value, dict) else {}
    _cache = data if isinstance(data, dict) else {}
    return _cache


def invalidate_cache() -> None:
    global _cache
    _cache = None


def source_for_town(town: str) -> dict[str, str] | None:
    """{"state", "url", "source_name", "description"} for `town`, or None
    if it isn't in the hand-verified directory — case-insensitive, same
    convention as town_directory.address_for_town."""
    data = _load()
    for name, entry in (data.get("towns") or {}).items():
        if name.casefold() == town.casefold():
            return entry
    return None


def town_names_for_state(state: str) -> list[str]:
    """Every curated town name in this directory for `state` — used to
    fold PDF-sourced towns into the same selector as Google-Civic-sourced
    ones (see elections.py's state_towns), since a town needs neither nor
    both: it needs whichever ONE source actually covers it."""
    data = _load()
    return [
        name for name, entry in (data.get("towns") or {}).items()
        if (entry.get("state") or "").upper() == state.upper()
    ]
