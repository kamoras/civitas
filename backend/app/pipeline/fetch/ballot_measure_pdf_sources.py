"""The hand-verified state -> ballot-measure-PDF directory that powers
direct ballot-measure ingestion (see ballot_measures_pdf.py).

Same volume/bundled dual-path read as ballot_pdf_sources.py/
town_directory.py: app/data/ is COPY'd into the Docker image and is NOT
writable at runtime.
"""

import logging
import os
from typing import Any

from app.file_cache import Uncached, read_json_preferring

logger = logging.getLogger(__name__)

_BUNDLED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "ballot_measure_pdf_sources.json")
_VOLUME_PATH = "/data/ballot_measure_pdf_sources.json"

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


def source_for_state(state: str) -> dict[str, str] | None:
    """{"url_pattern", "source_name", "strategy"} for `state`, or None if
    no hand-verified source is registered — case-insensitive state code."""
    data = _load()
    return (data.get("states") or {}).get(state.upper())


def configured_states() -> set[str]:
    """Every state with a registered PDF source — used to skip these
    states in the Vote Smart loop (election_pipeline._sync_ballot_measures)
    and to drive the direct-PDF sync loop."""
    data = _load()
    return set((data.get("states") or {}).keys())
