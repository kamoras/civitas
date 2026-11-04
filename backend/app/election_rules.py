"""A state's cited election-law facts per cycle, from data/election_rules.json
(the API's state ballot and the certified-result rule, app/recount_rules.py,
both read them)."""

import json
import logging
import pathlib
from datetime import date, timedelta

from app.election_calendar import next_election_day

logger = logging.getLogger(__name__)

_ELECTION_RULES_PATH = pathlib.Path(__file__).resolve().parent / "data" / "election_rules.json"
_election_rules_cache: dict | None = None


def election_rules(state: str, cycle: int, key: str) -> list[dict]:
    """`key` for `state` in `cycle` from data/election_rules.json: a cited
    legal fact per state. "generalRunoffs" -- where November is not
    decided by a plurality (Georgia's majority rule; Louisiana's House
    contests held as an all-party open primary), with the runoff's date.
    "otherPrimaries" -- contests whose primary was not the state's one
    primary date (Alabama's postponed House districts). "topPrimary" --
    an all-party primary that sent its top two (four) to November whatever
    their party (California, Washington; Alaska).

    The file's `standing` rules are the law in force every cycle (a runoff
    date as the statute counts it, `runoffDaysAfter` the general, computed
    here from the cycle's own election day); `cycles` holds one cycle's
    one-off facts. A standing rule therefore holds in a cycle nobody has
    written up, and a one-off never leaks into the next. [] when the file
    is missing: the page then says nothing rather than guessing."""
    global _election_rules_cache
    if _election_rules_cache is None:
        try:
            data = json.loads(_ELECTION_RULES_PATH.read_text())
            _election_rules_cache = {"standing": data.get("standing") or {}, "cycles": data.get("cycles") or {}}
        except Exception:
            logger.exception("election_rules.json unavailable")
            _election_rules_cache = {"standing": {}, "cycles": {}}
    entries = [
        *((_election_rules_cache["standing"].get(state) or {}).get(key) or []),
        *(((_election_rules_cache["cycles"].get(str(cycle)) or {}).get(state) or {}).get(key) or []),
    ]
    out = []
    for entry in entries:
        entry = dict(entry)
        if "runoffDaysAfter" in entry:
            general = next_election_day(date(cycle, 1, 1))
            entry["runoffDate"] = (general + timedelta(days=entry.pop("runoffDaysAfter"))).isoformat()
        out.append(entry)
    return out
