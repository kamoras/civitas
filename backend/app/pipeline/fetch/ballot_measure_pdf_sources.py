"""The hand-verified state -> ballot-measure-PDF directory that powers
direct ballot-measure ingestion (see ballot_measures_pdf.py).

Same volume/bundled dual-path read as ballot_pdf_sources.py/
town_directory.py: app/data/ is COPY'd into the Docker image and is NOT
writable at runtime.
"""

import logging
import os
from typing import Any

from app.file_cache import load_json_once

logger = logging.getLogger(__name__)

_BUNDLED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "ballot_measure_pdf_sources.json")
_VOLUME_PATH = "/data/ballot_measure_pdf_sources.json"

_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _cache
    data, _cache = load_json_once(_cache, _VOLUME_PATH, _BUNDLED_PATH)
    return data


def invalidate_cache() -> None:
    global _cache
    _cache = None


def source_for_state(state: str) -> dict[str, str] | None:
    """{"url_pattern", "source_name", "strategy"} for `state`, or None if
    no hand-verified source is registered — case-insensitive state code."""
    data = _load()
    return (data.get("states") or {}).get(state.upper())


def configured_states() -> set[str]:
    """Every state with a registered direct source — the states whose
    measures are read (election_pipeline._sync_pdf_measures). Every other
    state is recorded not yet covered."""
    data = _load()
    return set((data.get("states") or {}).keys())


def unread_reason(state: str) -> str | None:
    """Why `state`'s measures are not read — the `reason` of its registry
    'unread' entry, written for a voter and shown on the state's page.
    Only `reason` leaves this function: the entry's `dev_note` records what
    a developer found from one particular network and is never served.
    None for a state with a registered source (the registry's 'states'
    wins)."""
    if source_for_state(state) is not None:
        return None
    entry = ((_load().get("unread") or {}).get(state.upper())) or {}
    return entry.get("reason") if isinstance(entry, dict) else None


def none_by_law(state: str) -> dict | None:
    """{"source_name", "basis"} when an unread state can have no statewide
    measure at all, by its own law — its 'unread' entry's `none_by_law`
    (Delaware: amendments take no popular vote, and there is no initiative
    or referendum). Such a state is recorded confirmed none, citing that
    law, instead of not yet covered. A fact about one election (a
    pamphlet that lists none) is never put here: that needs a reader that
    checks every election. Only the sync's unread states ask (a state
    can't be both registered and unread: test_ballot_measures)."""
    entry = ((_load().get("unread") or {}).get(state.upper())) or {}
    law = entry.get("none_by_law") if isinstance(entry, dict) else None
    return law if isinstance(law, dict) and law.get("source_name") and law.get("basis") else None
