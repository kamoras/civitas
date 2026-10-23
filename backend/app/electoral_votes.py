"""Electoral votes by state, from the House apportionment.

A state's electors equal its senators and representatives (U.S. Const.
art. II, §1, cl. 2), so its electoral votes are its House seats plus two.
The seats are the apportionment the district table is gated on (every seat
of the House Clerk's member list exactly once, fetch/district_pvi.py),
read through the same keys the roster sync validates districts against
(score_calculator.get_district_pvi_map). The District of Columbia appoints
as many electors as the least populous state, never more (amend. XXIII,
§1): three. A presidential general is held every fourth year from 1788
(3 U.S.C. §1 and the four-year term, art. II, §1, cl. 1).
"""

from collections import Counter

from app.census_lines import apportionment, census_year, seated_by
from app.pipeline.analyze.score_calculator import get_district_pvi_map
from app.state_names import STATE_NAMES

# The fewest electors any state has: two senators and one representative.
_LEAST_STATE_ELECTORS = 3


def is_presidential(cycle: int) -> bool:
    return cycle % 4 == 0


def electoral_votes(cycle: int | None = None) -> dict[str, int]:
    """{state: electoral votes} for `cycle`'s presidential election, DC
    included: the apportionment of the census that election is held under
    (2032's is the 2030 census's: census_lines.census_year of the Congress
    it seats, from the Census Bureau's own table, app/data/apportionment.json),
    else, before that table is on file or with no cycle, the sitting
    House's seats; {} when neither is (a state with no seat would otherwise
    read as two votes)."""
    seats = Counter(apportionment(census_year(seated_by(cycle))) if cycle else {}) or Counter(
        key.split("-")[0] for key in get_district_pvi_map())
    if not seats:
        return {}
    votes = {state: n + 2 for state, n in seats.items()}
    votes["DC"] = _LEAST_STATE_ELECTORS
    return votes


def summary(cycle: int | None = None) -> dict:
    """The table the pages show: each state's votes, the total and the
    majority that elects (art. II and amend. XII: a majority of all
    electors appointed)."""
    votes = electoral_votes(cycle)
    total = sum(votes.values())
    return {
        "states": [{"state": st, "stateName": STATE_NAMES.get(st, st), "votes": n}
                   for st, n in sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))],
        "total": total,
        "toWin": total // 2 + 1 if total else None,
    }
