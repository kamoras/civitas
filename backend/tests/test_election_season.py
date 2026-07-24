"""Tests for is_election_season/days_until_next_election (api/action.py) —
extracted as public helpers so scheduler.py's election-season coverage
refresh doesn't need its own copy of this date arithmetic.

Boundary dates are derived from _next_election_day itself (anchored to a
January date, safely outside November) rather than hardcoded calendar
dates — _next_election_day has its own pre-existing behavior around
November of an election year (out of scope to change here), so deriving
from it keeps these tests honest about what the wrapped function actually
returns instead of asserting an assumed calendar date.
"""

from datetime import date, timedelta

from app.api.action import (
    ELECTION_SEASON_WINDOW_DAYS,
    _next_election_day,
    days_until_next_election,
    is_election_season,
)


class TestDaysUntilNextElection:
    def test_positive_before_election_day(self):
        assert days_until_next_election(date(2026, 1, 1)) > 0

    def test_counts_down_correctly(self):
        election_day = _next_election_day(date(2026, 1, 1))
        ten_days_before = election_day - timedelta(days=10)
        assert days_until_next_election(ten_days_before) == 10


class TestIsElectionSeason:
    def test_true_within_window(self):
        election_day = _next_election_day(date(2026, 1, 1))
        just_inside = election_day - timedelta(days=ELECTION_SEASON_WINDOW_DAYS)
        assert is_election_season(just_inside) is True

    def test_false_just_outside_window(self):
        election_day = _next_election_day(date(2026, 1, 1))
        just_outside = election_day - timedelta(days=ELECTION_SEASON_WINDOW_DAYS + 1)
        assert is_election_season(just_outside) is False

    def test_false_well_outside_window(self):
        assert is_election_season(date(2026, 1, 1)) is False
