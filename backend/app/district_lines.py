"""Which Congress's table describes a Congress's district lines.

Every per-district table (computed lean, counties, places) is built for the
lines of particular Congresses. The next Congress's lines usually exist
before anyone has built its table: they are the same lines wherever a state
did not redraw. So a table for a Congress with none of its own is the
newest earlier one — but only for the states whose map is the same in both
elections, and only once redrawn_congressional_maps.json lists the election
that Congress is chosen in (a cycle nobody has reviewed carries nothing: a
court stay "for 2026 only" must not silently carry into 2028). A state that
redrew gets nothing until its own table is built, never another map's
districts under the same numbers.
"""

from collections.abc import Mapping
from typing import TypeVar

from app.census_lines import elected_in
from app.census_lines import map_in as _map_in
from app.census_lines import maps_cycles as _maps

T = TypeVar("T")


def table_for_lines(congress: int, tables: Mapping[int, Mapping[str, T]]) -> dict[str, T] | None:
    """{"ST-N": value} on `congress`'s lines from `tables` (Congress ->
    table): its own table, else the newest earlier one restricted to the
    states on the same map (see module docstring); None when neither
    applies."""
    if congress in tables:
        return dict(tables[congress])
    earlier = max((c for c in tables if c < congress), default=None)
    cycles = _maps()
    target = elected_in(congress)
    if earlier is None or str(target) not in cycles:
        return None
    base = elected_in(earlier)
    same = {}
    for key, value in tables[earlier].items():
        state = key.split("-")[0]
        if _map_in(cycles, target, state) == _map_in(cycles, base, state):
            same[key] = value
    return same


def lookup_lines(state: str, congress: int) -> str | None:
    """The folder of frontend/public/data/lookup/ holding `state`'s House
    district shapes on `congress`'s lines ("congress-119"), or None when
    that Congress's election isn't listed yet (as in table_for_lines,
    nothing is carried into an unreviewed cycle).

    The folders are named by map, not by the Congress asking: a state that
    didn't redraw keeps the earlier Congress's folder, which holds the same
    lines (build_district_lookup.py builds one folder per map in use). So
    when a new Congress sits, every state that kept its map is still found
    with nothing rebuilt; asking for congress-<N> by the sitting Congress's
    number found no file for 41 states from the day the 120th sat."""
    cycles = _maps()
    year = elected_in(congress)
    if str(year) not in cycles and year > max((int(c) for c in cycles), default=0):
        return None
    return f"congress-{_map_in(cycles, year, state).removeprefix('CD')}"
