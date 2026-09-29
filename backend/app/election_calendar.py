"""Structural facts of the U.S. federal election calendar.

Extracted from api/action.py (2026-07, midterm-elections feature) so the
election pipeline can share them without a pipeline->api import: the
statutory election-day rule (2 U.S.C. §7 — first Tuesday after the first
Monday in November of even years) and the Senate's three-class rotation
(U.S. Const. art. I §3).

Which states sit in which class is data, read from the Senate's own member
list (pipeline/fetch/senate_classes.py, refreshed each election run into
/data/senate_classes.json, with app/data/senate_classes.json as the
pre-first-run fallback), not typed here: a new state, or any change to the
assignments, reaches every caller without a code change. The union of the
three classes is the set of states (senate_classes' docstring says why a
vacancy doesn't drop one).

The class sets are the authoritative cross-check for FEC-derived Senate
race data: a cycle's regular Senate races occur exactly in that cycle's
class states, so an FEC candidate filing for a Senate election in any
OTHER state that year is either a special election (seat vacated
mid-term) or bad data — election_pipeline._sync_roster uses this to
label specials instead of trusting any single upstream field.
"""

import json
import logging
import pathlib
from datetime import date

from app.time_utils import utcnow

logger = logging.getLogger(__name__)

_CLASS_FILES = (
    pathlib.Path("/data/senate_classes.json"),
    pathlib.Path(__file__).resolve().parent / "data" / "senate_classes.json",
)
_senate_classes_cache: dict[int, frozenset[str]] | None = None


def senate_classes() -> dict[int, frozenset[str]]:
    """{1: states, 2: states, 3: states}: which states hold a seat in each
    Senate class, from the refreshed file, else the bundled one."""
    global _senate_classes_cache
    if _senate_classes_cache is None:
        for path in _CLASS_FILES:
            try:
                raw = json.loads(path.read_text())["classes"]
                _senate_classes_cache = {int(k): frozenset(v) for k, v in raw.items()}
                break
            except Exception:
                continue
        else:
            logger.error("senate_classes.json unavailable in /data and the bundled fallback")
            _senate_classes_cache = {1: frozenset(), 2: frozenset(), 3: frozenset()}
    return _senate_classes_cache


def reset_senate_classes() -> None:
    """Drop the cached sets, after a refresh wrote new ones."""
    global _senate_classes_cache
    _senate_classes_cache = None


def federal_states() -> frozenset[str]:
    """Every state: the jurisdictions with Senate seats, which are exactly
    the ones with voting House seats and federal races. DC and the
    territories have neither."""
    return frozenset().union(*senate_classes().values())


def next_election_day(after: date) -> date:
    """Compute next federal election day (first Tue after first Mon in Nov, even years).

    Starts the search at `after`'s own year (bumped to the next even year
    only if odd) and lets the `election_day > after` check below decide
    whether to advance — a `year = after.year + 1` short-circuit for any
    November date used to skip straight past the *current* year's election
    day even when `after` fell a day or two before it (e.g. Nov 1-2 in an
    election year), reporting the cycle two years too far out.
    """
    year = after.year
    if year % 2 != 0:
        year += 1
    while True:
        nov1 = date(year, 11, 1)
        first_monday = nov1.day + (7 - nov1.weekday()) % 7
        if nov1.weekday() == 0:
            first_monday = 1
        election_day = date(year, 11, first_monday + 1)
        if election_day > after:
            return election_day
        year += 2


# Single source of truth for the rotation: each class's first modern
# election year, and the state set it elects. seats_up_for_year and
# next_senate_election_year both read this instead of each hand-typing
# their own copy of the three base years — two independently-maintained
# copies is exactly how one gets corrected (a typo fix, a rare mid-decade
# reassignment) without the other, and silently drifts them apart.
_CLASS_BASE_YEARS = {1: 2018, 2: 2020, 3: 2022}


def seats_up_for_year(year: int) -> frozenset[str]:
    """States with a REGULAR Senate seat up in `year` (by class rotation).

    Special elections are additional to this set and are not derivable
    from the calendar — they exist only when a seat was vacated.
    """
    for cls, base_year in _CLASS_BASE_YEARS.items():
        if (year - base_year) % 6 == 0:
            return senate_classes().get(cls, frozenset())
    return frozenset()


def next_senate_election_year(state: str, after_year: int) -> int | None:
    """The next year after `after_year` this state has a REGULAR Senate
    seat up, or None for a jurisdiction with no Senate seats at all (DC,
    territories) — every one of the 50 states is in exactly two of the
    three class sets, one seat per class, so this never returns None for
    an actual state.

    For explaining an empty Senate section on a state's ballot page: a
    reader who doesn't already know the three-class rotation (U.S.
    Const. art. I §3) can't otherwise tell "this state's other seat
    isn't up until later" from "the data is missing." Special elections
    aren't covered here — like seats_up_for_year, they only exist when a
    seat is vacated and aren't derivable from the calendar alone.
    """
    classes = [c for c, states in senate_classes().items() if state in states]
    if not classes:
        return None
    candidates = []
    for c in classes:
        year = _CLASS_BASE_YEARS[c]
        while year <= after_year:
            year += 6
        candidates.append(year)
    return min(candidates)


ELECTION_SEASON_WINDOW_DAYS = 60


def days_until_next_election(today: date | None = None) -> int:
    """Days remaining until the next federal Election Day (0 = today)."""
    today = today or utcnow().date()
    return (next_election_day(today) - today).days


def is_election_season(today: date | None = None) -> bool:
    """True within ELECTION_SEASON_WINDOW_DAYS of the next federal election
    — the window the midterm-elections pipeline (election_pipeline.py) uses
    to switch its coverage-ingestion phase from nightly to a tighter cadence
    (see scheduler.py). Moved here from api/action.py (2026-09), where it
    had outlived the Action Center elections tab it was written beside."""
    return days_until_next_election(today) <= ELECTION_SEASON_WINDOW_DAYS
