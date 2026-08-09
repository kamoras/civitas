"""The hand-verified state -> confirmed-general-election-candidate source
directory (see state_candidates.py). Same dual-path bundled/volume read as
ballot_measure_pdf_sources.py/town_directory.py: app/data/ is COPY'd into
the Docker image and is NOT writable at runtime.
"""

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_BUNDLED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "state_candidate_sources.json")
_VOLUME_PATH = "/data/state_candidate_sources.json"

_cache: dict[str, Any] | None = None


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


def invalidate_cache() -> None:
    global _cache
    _cache = None


def source_for_state(state: str) -> dict[str, str] | None:
    """{"source_name", "strategy", ...} for `state`, or None if no
    hand-verified source is registered — case-insensitive state code."""
    data = _load()
    return (data.get("states") or {}).get(state.upper())


def configured_states() -> set[str]:
    """Every state with a registered confirmed-candidate source — drives
    the sync loop in election_pipeline.py."""
    data = _load()
    return set((data.get("states") or {}).keys())
