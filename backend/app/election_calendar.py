"""Structural facts of the U.S. federal election calendar.

Extracted from api/action.py (2026-07, midterm-elections feature) so the
election pipeline can share them without a pipeline->api import: the
statutory election-day rule (2 U.S.C. §7 — first Tuesday after the first
Monday in November of even years) and the Senate's three-class rotation
(U.S. Const. art. I §3). These are constitutional/statutory structure,
not calibrated constants — there is no data file they could be generated
from that wouldn't itself be a transcription of the same clauses.

The class sets are the authoritative cross-check for FEC-derived Senate
race data: a cycle's regular Senate races occur exactly in that cycle's
class states, so an FEC candidate filing for a Senate election in any
OTHER state that year is either a special election (seat vacated
mid-term) or bad data — election_pipeline._sync_roster uses this to
label specials instead of trusting any single upstream field.
"""

from datetime import date

CLASS_I_STATES = frozenset({
    "AZ", "CA", "CT", "DE", "FL", "HI", "IN", "ME", "MD", "MA",
    "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NJ", "NM", "NY",
    "ND", "OH", "PA", "RI", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY",
})
CLASS_II_STATES = frozenset({
    "AK", "AL", "AR", "CO", "DE", "GA", "ID", "IL", "IA", "KS",
    "KY", "LA", "ME", "MA", "MI", "MN", "MS", "MT", "NE", "NH",
    "NJ", "NM", "NC", "OK", "OR", "RI", "SC", "SD", "TN", "TX",
    "VA", "WV", "WY",
})
CLASS_III_STATES = frozenset({
    "AK", "AL", "AR", "AZ", "CA", "CO", "CT", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "MD", "MO", "NV",
    "NH", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "SC", "SD",
    "UT", "VT", "WA", "WI",
})


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


def seats_up_for_year(year: int) -> frozenset[str]:
    """States with a REGULAR Senate seat up in `year` (by class rotation).

    Special elections are additional to this set and are not derivable
    from the calendar — they exist only when a seat was vacated.
    """
    if (year - 2020) % 6 == 0:
        return CLASS_II_STATES
    if (year - 2022) % 6 == 0:
        return CLASS_III_STATES
    if (year - 2018) % 6 == 0:
        return CLASS_I_STATES
    return frozenset()


# Each class's own rotation base — the same three years seats_up_for_year
# checks against, just keyed the other direction (class -> base year
# instead of year -> class) so next_senate_election_year can walk a
# single class forward instead of re-deriving it from three checks.
_CLASS_BASE_YEARS = {1: 2018, 2: 2020, 3: 2022}


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
    classes = [c for c, states in (
        (1, CLASS_I_STATES), (2, CLASS_II_STATES), (3, CLASS_III_STATES),
    ) if state in states]
    if not classes:
        return None
    candidates = []
    for c in classes:
        year = _CLASS_BASE_YEARS[c]
        while year <= after_year:
            year += 6
        candidates.append(year)
    return min(candidates)
