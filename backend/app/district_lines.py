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

from app.live_results.sync import _map_in, _maps

T = TypeVar("T")


def elected_in(congress: int) -> int:
    """The election year that seats `congress` (the 1st was elected in 1788)."""
    return 1786 + 2 * congress


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
