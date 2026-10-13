"""The cities, towns and communities in each U.S. House district.

A reader who doesn't know their district number finds it by a place they
know (AGENTS.md §8). Counties alone failed the most natural query: about
one county in eight is split between districts, and most people name
their city first. This reads app/data/congressional_district_places.json
(scripts/build_district_place_crosswalk.py, Census block files), which
holds two maps keyed by the Congress whose lines they are: the coming
election's (the state ballot pages) and the sitting Congress's (finding
today's member).
"""

import json
import logging
import pathlib

logger = logging.getLogger(__name__)

_PATH = pathlib.Path(__file__).resolve().parent / "data" / "congressional_district_places.json"
_cache: dict[str, dict[str, list[str]]] | None = None


def places_on_lines_of(congress: int) -> dict[str, list[str]] | None:
    """"ST-N" -> the places in that district on `congress`'s lines, largest
    first (at-large seats are "ST-0"); a place listed under several
    districts is split between them. None when the file has no map for
    that Congress — never another Congress's map in its place, since in a
    redrawn state the same number is a different district."""
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_PATH.read_text())["congresses"]
        except Exception:
            logger.exception("congressional_district_places.json unavailable")
            _cache = {}
    return _cache.get(str(congress))
