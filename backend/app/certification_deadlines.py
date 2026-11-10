"""The last day each state's law gives it to certify its federal results
(data/certification_deadlines.json, quoted and cited per state from NASS's
canvassing summary), for the states whose certification Civitas can't read
(live_results/certified.py: past the deadline, a live count that stopped
changing by then is taken as the certified one).
"""

import json
import logging
import pathlib
from datetime import date, timedelta
from functools import lru_cache

logger = logging.getLogger(__name__)

_PATH = pathlib.Path(__file__).resolve().parent / "data" / "certification_deadlines.json"
_WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


@lru_cache(maxsize=1)
def _rules() -> dict:
    try:
        return json.loads(_PATH.read_text()).get("states") or {}
    except Exception:
        logger.exception("certification_deadlines.json unavailable")
        return {}


def states() -> frozenset[str]:
    return frozenset(_rules())


def rule_for(state: str) -> dict | None:
    return _rules().get(state)


def _nth_weekday_after(start: date, weekday: int, nth: int) -> date:
    """The nth `weekday` strictly after `start`."""
    first = start + timedelta(days=(weekday - start.weekday()) % 7 or 7)
    return first + timedelta(weeks=nth - 1)


def deadline(state: str, election_day: date) -> date | None:
    """The day by which `state` must certify `election_day`'s federal results."""
    entry = rule_for(state)
    if entry is None:
        return None
    spec = entry["deadline"]
    if "days" in spec:
        return election_day + timedelta(days=int(spec["days"]))
    weekday = _WEEKDAYS.index(spec["weekday"])
    if spec.get("last"):
        # The last such weekday of the month the election is held in.
        month_end = (election_day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return month_end - timedelta(days=(month_end.weekday() - weekday) % 7)
    if "month" in spec:
        year = election_day.year + (1 if spec["month"] < election_day.month else 0)
        return _nth_weekday_after(date(year, spec["month"], 1) - timedelta(days=1), weekday, int(spec["nth"]))
    return _nth_weekday_after(election_day, weekday, int(spec["nth"]))
