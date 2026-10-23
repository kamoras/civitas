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

import logging

from app import data_refresh
from app.district_lines import table_for_lines

logger = logging.getLogger(__name__)

PLACES_FILE = "congressional_district_places.json"
# Each declares the Congress whose lines it holds: the sitting Congress's
# and the coming election's (scripts/build_county_district_crosswalk.py,
# both rebuilt by the data refresh when a Congress the site needs has a
# state neither covers).
CROSSWALK_FILES = ("county_district_crosswalk_sitting.json", "county_district_crosswalk.json")


def places_on_lines_of(congress: int) -> dict[str, list[str]] | None:
    """"ST-N" -> the places in that district on `congress`'s lines, largest
    first (at-large seats are "ST-0"); a place listed under several
    districts is split between them. None when no map on file describes
    that Congress's lines (district_lines.table_for_lines: never another
    Congress's map for a state that redrew). The copy in use
    (data_refresh.read)."""
    tables = data_refresh.read(PLACES_FILE).get("congresses")
    if not isinstance(tables, dict):
        logger.error("congressional_district_places.json unavailable")
        tables = {}
    return table_for_lines(congress, {int(c): t for c, t in tables.items()})


def counties_on_lines_of(congress: int) -> dict[str, list[str]] | None:
    """"ST-N" -> county names on the district lines of `congress`, or None
    when no crosswalk describes those lines (district_lines.table_for_lines:
    never another Congress's map for a state that redrew)."""
    tables: dict[int, dict[str, list[str]]] = {}
    for name in CROSSWALK_FILES:
        doc = data_refresh.read(name)
        if isinstance(doc.get("districts"), dict) and str(doc.get("congress", "")).isdigit():
            tables[int(doc["congress"])] = doc["districts"]
        else:
            logger.error("%s unavailable", name)
    return table_for_lines(congress, tables)
