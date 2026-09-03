"""The state -> confirmed-general-election-candidate source directory (see
state_candidates.py). Same dual-path bundled/volume read as
ballot_measure_pdf_sources.py/town_directory.py: app/data/ is COPY'd into
the Docker image and is NOT writable at runtime.

TWO directories, kept deliberately separate:

  * The HAND-VERIFIED file (state_candidate_sources.json) — every entry
    checked against that state's real live feed by a person, and the only
    place a state's nomination RULES (runoff threshold, top-two) are ever
    written, because those are law and can't be inferred.
  * The DISCOVERED file, written by state_source_crawler.py — locations it
    found and proved on its own, refreshed on every crawl.

A hand-verified entry always wins. The discovered file only covers states
nobody has written up yet, so an automatic find can add a state but can
never quietly override a checked one.
"""

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_BUNDLED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "state_candidate_sources.json")
_VOLUME_PATH = "/data/state_candidate_sources.json"

# Written at runtime, so it lives where the app can actually write: the
# Docker volume, or the same local data/ directory the dev database sits in.
_DISCOVERED_PATHS = (
    "/data/state_sources_discovered.json",
    os.path.join(os.getcwd(), "data", "state_sources_discovered.json"),
)

_cache: dict[str, Any] | None = None
_discovered_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    for path in (_VOLUME_PATH, _BUNDLED_PATH):
        try:
            with open(path, encoding="utf-8") as fh:
                _cache = json.load(fh)
                return _cache
        except FileNotFoundError:
            continue
        except Exception:
            logger.exception("Failed to read state candidate sources file %s", path)
    _cache = {}
    return _cache


def _load_discovered() -> dict[str, Any]:
    global _discovered_cache
    if _discovered_cache is not None:
        return _discovered_cache
    for path in _DISCOVERED_PATHS:
        try:
            with open(path, encoding="utf-8") as fh:
                _discovered_cache = json.load(fh) or {}
                return _discovered_cache
        except FileNotFoundError:
            continue
        except Exception:
            logger.exception("Failed to read discovered sources file %s", path)
    _discovered_cache = {}
    return _discovered_cache


def save_discovered(state: str, source: dict[str, Any] | None) -> None:
    """Record (or, with None, forget) what the crawler proved for `state`.
    Never touches the hand-verified file."""
    discovered = dict(_load_discovered())
    if source is None:
        discovered.pop(state.upper(), None)
    else:
        discovered[state.upper()] = source
    for path in _DISCOVERED_PATHS:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(discovered, fh, indent=2, sort_keys=True)
            break
        except OSError:
            continue
    else:
        logger.warning("Nowhere writable to record discovered source for %s", state)
    global _discovered_cache
    _discovered_cache = discovered


def discovered_states() -> set[str]:
    """States covered only by what the crawler found."""
    return set(_load_discovered()) - set((_load().get("states") or {}).keys())


def invalidate_cache() -> None:
    global _cache, _discovered_cache
    _cache = None
    _discovered_cache = None


def source_for_state(state: str) -> dict[str, str] | None:
    """{"source_name", "strategy", ...} for `state`, or None if neither a
    hand-verified nor a discovered source exists — case-insensitive state
    code. Hand-verified always wins."""
    data = _load()
    return ((data.get("states") or {}).get(state.upper())
            or _load_discovered().get(state.upper()))


def configured_states() -> set[str]:
    """Every state with a RESULTS source, hand-verified or discovered —
    drives the confirmed-nominee sync in election_pipeline.py."""
    entries = {**_load_discovered(), **(_load().get("states") or {})}
    return {state for state, entry in entries.items() if entry.get("strategy")}


def states_with_filings() -> set[str]:
    """Every state with a candidate FILING list registered — a state can
    have one without having a results source yet, which is the normal
    situation before its primary."""
    entries = {**_load_discovered(), **(_load().get("states") or {})}
    return {state for state, entry in entries.items() if entry.get("filings")}
