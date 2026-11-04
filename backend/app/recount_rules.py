"""Whether a certified count can still change: each state's recount law
(data/recount_rules.json, cited per state) applied to a race's certified
count.

A race is a certified winner only when all of these hold:
  - the state has certified the count (a certified source was read);
  - no runoff is owed (election_rules.json's generalRunoffs, where the
    winner needs a majority);
  - the margin is outside every automatic recount the state's law orders;
  - every recount a candidate may still ask for at that margin has a
    deadline in the law, and that deadline has passed.

Where the law orders a recount, or sets no deadline for asking for one,
the race is left at "recount possible" until the new Congress is sworn in:
a recount's own result is certified again, and nothing here can tell a
recount that has finished from one still going. The margin is measured
against every vote cast for the office, the widest base any of these laws
uses, so it is never read as wider than the state reads it, and every
deadline the file anchors to a county canvass is counted from the state's
certification, which comes later. Both choices can only make the wait
longer than the law's, never shorter.
"""

import json
import logging
import pathlib
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

from app.election_rules import election_rules

logger = logging.getLogger(__name__)

_PATH = pathlib.Path(__file__).resolve().parent / "data" / "recount_rules.json"

RUNOFF = "runoff"
RECOUNT = "recount"
RECOUNT_POSSIBLE = "recount_possible"
CERTIFIED = "certified"


@dataclass(frozen=True)
class Outcome:
    status: str  # RUNOFF, RECOUNT, RECOUNT_POSSIBLE or CERTIFIED
    # RECOUNT_POSSIBLE: the last day a recount may be asked for, where the
    # law sets one; None where it doesn't (then the race waits for the new
    # Congress). None for every other status.
    until: date | None = None
    statute: str | None = None

    def as_json(self) -> dict:
        return {"status": self.status, "recountUntil": self.until.isoformat() if self.until else None,
                "statute": self.statute}


@lru_cache(maxsize=1)
def _rules() -> dict:
    try:
        return json.loads(_PATH.read_text()).get("states") or {}
    except Exception:
        logger.exception("recount_rules.json unavailable")
        return {}


def rule_for(state: str) -> dict | None:
    return _rules().get(state)


def _thanksgiving(year: int) -> date:
    first = date(year, 11, 1)
    return first + timedelta(days=(3 - first.weekday()) % 7 + 21)


def _days_off(year: int) -> set[date]:
    """Weekdays a state office may be closed between a general and the new
    Congress: Veterans Day, Thanksgiving and the day after, Christmas and New
    Year's Day. Not every state closes on all of them; skipping one a state
    keeps open only makes the wait a day longer."""
    thanks = _thanksgiving(year)
    return {date(year, 11, 11), thanks, thanks + timedelta(days=1), date(year, 12, 25), date(year + 1, 1, 1)}


def _add(start: date, days: int, business: bool) -> date:
    if not business:
        return start + timedelta(days=days)
    off = _days_off(start.year) | _days_off(start.year - 1)
    day = start
    left = days
    while left > 0:
        day += timedelta(days=1)
        if day.weekday() < 5 and day not in off:
            left -= 1
    return day


def deadline(spec: dict, election_day: date, certified_on: date) -> date:
    start = election_day if spec["after"] == "election" else certified_on
    return max(_add(start, int(spec["days"]), bool(spec.get("business"))), certified_on)


def _within(spec: dict, margin_pct: float, margin_votes: int) -> bool:
    conditions = spec.get("within") or []
    if not conditions:
        return True
    hits = [margin_pct <= c["pct"] if "pct" in c else margin_votes <= c["votes"] for c in conditions]
    return all(hits) if spec.get("combine") == "all" else any(hits)


def _runoff_owed(state: str, cycle: int, office: str, district: int | None, share: float) -> bool:
    for entry in election_rules(state, cycle, "generalRunoffs"):
        offices = entry.get("offices")
        if offices != "*" and office not in (offices or []):
            continue
        if entry.get("districts") and district not in entry["districts"]:
            continue
        if share <= 50.0:
            return True
    return False


def outcome(state: str, office: str, district: int | None, votes: list[int], total: int,
            election_day: date, certified_on: date, today: date) -> Outcome:
    """What a certified count allows the page to say. `votes`: each
    candidate's certified votes, most first; `total`: every vote cast for the
    office (write-ins included where the state counts them)."""
    rule = rule_for(state)
    statute = (rule or {}).get("statute")
    votes = sorted(votes, reverse=True)
    if not votes or total <= 0 or votes[0] <= 0:
        return Outcome(RECOUNT_POSSIBLE, statute=statute)
    if _runoff_owed(state, election_day.year, office, district, 100.0 * votes[0] / total):
        return Outcome(RUNOFF, statute=statute)
    second = votes[1] if len(votes) > 1 else 0
    margin_votes = votes[0] - second
    if margin_votes <= 0:
        return Outcome(RECOUNT, statute=statute)  # a tie: every state's law breaks it some other way
    if rule is None:
        return Outcome(RECOUNT_POSSIBLE, statute=None)
    margin_pct = 100.0 * margin_votes / total
    if rule.get("automatic") and _within(rule["automatic"], margin_pct, margin_votes):
        return Outcome(RECOUNT, statute=statute)
    last: date | None = None
    for spec in rule.get("requested") or []:
        if not _within(spec, margin_pct, margin_votes):
            continue
        if spec.get("deadline") is None:
            return Outcome(RECOUNT_POSSIBLE, statute=statute)
        day = deadline(spec["deadline"], election_day, certified_on)
        last = day if last is None or day > last else last
    if last is not None and today <= last:
        return Outcome(RECOUNT_POSSIBLE, until=last, statute=statute)
    return Outcome(CERTIFIED, statute=statute)


def close_enough_to_contest(state: str, office: str, district: int | None, votes: list[int], total: int,
                            cycle: int) -> bool:
    """Whether a count's margin is one the state's law treats as close:
    a runoff owed, an automatic recount, or a recount a candidate may ask
    for only at a margin this narrow. A recount any candidate may ask for
    at any margin (no `within`) says nothing about this count. A state with
    no rule on file counts as close, so nothing is projected there.
    Read by the projection rule (live_results/sync.py); never a call."""
    votes = sorted(votes, reverse=True)
    if not votes or total <= 0 or votes[0] <= 0:
        return True
    if _runoff_owed(state, cycle, office, district, 100.0 * votes[0] / total):
        return True
    rule = rule_for(state)
    if rule is None:
        return True
    margin_votes = votes[0] - (votes[1] if len(votes) > 1 else 0)
    if margin_votes <= 0:
        return True
    margin_pct = 100.0 * margin_votes / total
    if rule.get("automatic") and _within(rule["automatic"], margin_pct, margin_votes):
        return True
    return any(spec.get("within") and _within(spec, margin_pct, margin_votes)
               for spec in rule.get("requested") or [])
