"""Which kinds of contest a state puts on its November general ballot in a
given year — see data/state_ballot_scope.json. The state ballot page's
`omits` list reads this so it names only gaps that exist: a state that
appoints its judges, holds no retention votes, or elects its legislature
in odd years must not be told its page leaves those out."""

import json
import os
from functools import lru_cache

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "state_ballot_scope.json")

KINDS = ("legislature", "judicial_contests", "judicial_retention")


@lru_cache(maxsize=1)
def _states() -> dict:
    with open(_PATH) as f:
        return json.load(f)["states"]


def on_november_ballot(state: str, kind: str, year: int) -> bool:
    """Whether `kind` (one of KINDS) is on `state`'s November general
    ballot in `year`. A state the file doesn't list answers True: a gap
    nobody checked is still a gap the page has to name."""
    entry = _states().get(state)
    if entry is None:
        return True
    cycle = entry[kind]
    return cycle is not None and (year - cycle["from"]) % cycle["every"] == 0
