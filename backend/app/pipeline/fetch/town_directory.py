"""The small, hand-curated town → representative-address directory that
powers town-level ballot lookups (see civic_info.py).

Same volume/bundled dual-path read as ballot_lookup.py/district_pvi.py:
app/data/ is COPY'd into the Docker image and is NOT writable at runtime, so
this prefers a writable volume copy and falls back to the image-baked one.
There is currently no writer for the volume copy (unlike ballot_lookup.py's
verifier) — expanding the list means editing town_directory.json and
redeploying, which is the right amount of ceremony for a hand-curated list
that should not grow without a human looking at each address.
"""

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_BUNDLED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "town_directory.json")
_VOLUME_PATH = "/data/town_directory.json"

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
            logger.exception("Failed to read town directory file %s", path)
    _cache = {}
    return _cache


def invalidate_cache() -> None:
    global _cache
    _cache = None


def towns_for_state(state: str) -> list[dict[str, str]]:
    """Every curated town for `state`, each as {"name", "address", "sourceName"}.

    Empty list for a state with no pilot towns — never an error. The
    frontend's town selector treats an empty list as "not offered here"
    exactly like MeasureCoverage.NOT_YET_COVERED for statewide measures.
    """
    data = _load()
    towns = (data.get("towns") or {}).get(state.upper()) or []
    return [
        {
            "name": t["name"],
            "address": t["address"],
            "sourceName": t.get("source_name") or "",
        }
        for t in towns
        if t.get("name") and t.get("address")
    ]


def address_for_town(state: str, town: str) -> str | None:
    """The representative address for one curated town, or None if it
    isn't in the directory — the caller's is_configured()-style gate
    against a lookup that was never going to resolve to anything."""
    for entry in towns_for_state(state):
        if entry["name"].casefold() == town.casefold():
            return entry["address"]
    return None
