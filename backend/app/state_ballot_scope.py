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
    """Whether `kind` (one of KINDS, or the optional "statewide_executive")
    is on `state`'s November general ballot in `year`. A state or kind the
    file doesn't list answers True: a gap nobody checked is still a gap the
    page has to name."""
    entry = _states().get(state)
    if entry is None or kind not in entry:
        return True
    cycle = entry[kind]
    return cycle is not None and (year - cycle["from"]) % cycle["every"] == 0


# Each kind as a reader would say it, for the odd-year list.
_KIND_WORDS = {
    "statewide_executive": "its statewide executive offices (the governor's among them)",
    "legislature": "its legislature",
    "judicial_contests": "its judges",
    "judicial_retention": "whether to keep its judges",
}


def odd_year_elections(year: int) -> list[dict]:
    """The states whose regular state elections fall in `year`, an odd year
    (none in an even one: every state also votes in those), each with the
    kinds of contest the file says are on its ballot then, by the cycle it
    cites. Only kinds the file lists for the state: an unlisted kind means
    "every cycle" for the gap list, which says nothing about an odd year."""
    if year % 2 == 0:
        return []
    out = []
    for state, entry in sorted(_states().items()):
        kinds = [k for k, words in _KIND_WORDS.items()
                 if isinstance(entry.get(k), dict) and (year - entry[k]["from"]) % entry[k]["every"] == 0]
        if kinds:
            out.append({"state": state, "kinds": kinds, "words": [_KIND_WORDS[k] for k in kinds],
                        "why": entry.get("_why")})
    return out
