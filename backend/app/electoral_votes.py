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

from app.pipeline.analyze.score_calculator import get_district_pvi_map
from app.state_names import STATE_NAMES

# The fewest electors any state has: two senators and one representative.
_LEAST_STATE_ELECTORS = 3


def is_presidential(cycle: int) -> bool:
    return cycle % 4 == 0


def electoral_votes() -> dict[str, int]:
    """{state: electoral votes}, DC included; {} when no apportionment is on
    file (a state with no seat would otherwise read as two votes).

    ponytail: the sitting House's apportionment. A census reapportionment
    reaches it when the new Congress's lines are pinned (the 2032 cycle's,
    after the 2030 census), which the House pages need first anyway."""
    seats = Counter(key.split("-")[0] for key in get_district_pvi_map())
    if not seats:
        return {}
    votes = {state: n + 2 for state, n in seats.items()}
    votes["DC"] = _LEAST_STATE_ELECTORS
    return votes


def summary() -> dict:
    """The table the pages show: each state's votes, the total and the
    majority that elects (art. II and amend. XII: a majority of all
    electors appointed)."""
    votes = electoral_votes()
    total = sum(votes.values())
    return {
        "states": [{"state": st, "stateName": STATE_NAMES.get(st, st), "votes": n}
                   for st, n in sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))],
        "total": total,
        "toWin": total // 2 + 1 if total else None,
    }
