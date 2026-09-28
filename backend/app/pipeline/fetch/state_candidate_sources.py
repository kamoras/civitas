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

from app.atomic_write import LockTimeout, NotSaved, runtime_data_path, update_json_file
from app.file_cache import Stamp, files_stamp

logger = logging.getLogger(__name__)

_BUNDLED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "state_candidate_sources.json")
_VOLUME_PATH = "/data/state_candidate_sources.json"

# Written at runtime, so it lives where the app can actually write: the
# Docker volume, or the same local data/ directory the dev database sits in
# (runtime_data_path). Set by tests.
_DISCOVERED_FILE = "state_sources_discovered.json"
_DISCOVERED_PATH: str | None = None

# What a discovered entry's RESULTS source consists of — everything but its
# filing list, which is found and proved separately and outlives it.
_FILINGS_KEYS = ("filings",)

_cache: dict[str, Any] | None = None
_discovered_cache: dict[str, Any] | None = None
_discovered_stamp: Stamp = None


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


def _discovered_path() -> str:
    return _DISCOVERED_PATH or runtime_data_path(_DISCOVERED_FILE)


def _load_discovered() -> dict[str, Any]:
    global _discovered_cache, _discovered_stamp
    path = _discovered_path()
    # The election pipeline (the pipeline process) writes the file; the API
    # processes read it here and reload when its mtime moves
    # (file_cache.files_stamp) — invalidate_cache() reaches only its caller.
    stamp = files_stamp([path])
    if _discovered_cache is not None and stamp == _discovered_stamp:
        return _discovered_cache
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {}
    except ValueError:
        logger.exception("Discovered sources file %s is not valid JSON", path)
        data = {}
    except OSError:
        # Not cached: unreadable is not empty, and the next read retries.
        # (Writes re-read the file under their lock, so this can never be
        # written back as the whole file.)
        logger.exception("Failed to read discovered sources file %s", path)
        return {}
    _discovered_cache, _discovered_stamp = (data if isinstance(data, dict) else {}), stamp
    return _discovered_cache


def _update_discovered(change) -> None:
    """Apply `change` to the discovered file as it is on disk now, under its
    lock, so neither the crawl nor a sync in another process loses the
    other's write. Raises NotSaved."""
    global _discovered_cache
    path = _discovered_path()
    try:
        update_json_file(path, change, indent=2, sort_keys=True)
        # Re-read on next use rather than stamp what was written: a stat
        # taken after the write could already describe a later writer's
        # file, and would pin this older copy until the next change.
        _discovered_cache = None
    except (OSError, LockTimeout) as error:
        raise NotSaved(f"discovered sources not saved to {path}: {error}") from error


def save_discovered(state: str, source: dict[str, Any] | None) -> None:
    """Record (or, with None, forget) what the crawler proved for `state`.
    Never touches the hand-verified file. Raises NotSaved."""
    st = state.upper()

    def change(discovered: dict) -> dict:
        if source is None:
            discovered.pop(st, None)
        else:
            discovered[st] = source
        return discovered

    _update_discovered(change)


def forget_results_source(state: str) -> None:
    """Drop a discovered RESULTS source that stopped working, keeping the
    state's filing list, which was proved on its own and fails on its own.
    Raises NotSaved."""
    st = state.upper()

    def change(discovered: dict) -> dict:
        entry = discovered.get(st) or {}
        kept = {k: entry[k] for k in _FILINGS_KEYS if k in entry}
        if kept:
            discovered[st] = kept
        else:
            discovered.pop(st, None)
        return discovered

    _update_discovered(change)


def filings_for_state(state: str) -> dict[str, Any] | None:
    """`state`'s candidate filing list: the hand-verified entry's, else the
    one the crawler proved. Separate from source_for_state, where a
    hand-verified entry wins whole — a filing list found for a
    hand-verified state (which has none of its own) was otherwise never
    read."""
    st = state.upper()
    hand = (_load().get("states") or {}).get(st) or {}
    return hand.get("filings") or (_load_discovered().get(st) or {}).get("filings")


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
    states = set(_load_discovered()) | set(_load().get("states") or {})
    return {state for state in states if filings_for_state(state)}
