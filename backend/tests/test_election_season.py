"""Tests for is_election_season/days_until_next_election (app/election_calendar.py) —
extracted as public helpers so scheduler.py's election-season coverage
refresh doesn't need its own copy of this date arithmetic.

Boundary dates are derived from next_election_day itself rather than
hardcoded calendar dates, so these tests stay honest about what the
wrapped function actually returns instead of asserting an assumed
calendar date.
"""

from datetime import date, timedelta

from app.election_calendar import (
    ELECTION_SEASON_WINDOW_DAYS,
    days_until_next_election,
    is_election_season,
    next_election_day,
)


class TestDaysUntilNextElection:
    def test_positive_before_election_day(self):
        assert days_until_next_election(date(2026, 1, 1)) > 0

    def test_counts_down_correctly(self):
        election_day = next_election_day(date(2026, 1, 1))
        ten_days_before = election_day - timedelta(days=10)
        assert days_until_next_election(ten_days_before) == 10


    def test_zero_on_election_day_itself(self):
        """next_election_day is strictly after its argument; this used to
        return ~730 on the day, so the teaser's ELECTION DAY never showed."""
        election_day = next_election_day(date(2026, 1, 1))
        assert days_until_next_election(election_day) == 0


class TestIsElectionSeason:
    def test_true_within_window(self):
        election_day = next_election_day(date(2026, 1, 1))
        just_inside = election_day - timedelta(days=ELECTION_SEASON_WINDOW_DAYS)
        assert is_election_season(just_inside) is True

    def test_false_just_outside_window(self):
        election_day = next_election_day(date(2026, 1, 1))
        just_outside = election_day - timedelta(days=ELECTION_SEASON_WINDOW_DAYS + 1)
        assert is_election_season(just_outside) is False

    def test_false_well_outside_window(self):
        assert is_election_season(date(2026, 1, 1)) is False

    def test_true_while_results_are_on_show(self, db_session):
        """The count after election day is when coverage moves fastest."""
        from unittest.mock import patch

        election_day = next_election_day(date(2026, 1, 1))
        with patch("app.database.SessionLocal", return_value=db_session):
            assert is_election_season(election_day + timedelta(days=3)) is True
