"""The site's election after polls close: held on the one just voted in
while its count moves and for RESULTS_GRACE_DAYS after, never past the
new Congress's first day, and only then on to the next election."""

from datetime import date, datetime, timedelta

from app.election_calendar import new_congress_day, next_election_day, previous_election_day
from app.election_phase import (
    CAMPAIGN,
    ELECTION_DAY,
    RESULTS,
    RESULTS_GRACE_DAYS,
    active_election,
    resolve_active_election,
    results_window_end,
)
from app.models import Race, RaceResult

ELECTION = next_election_day(date(2026, 1, 1))  # 2026-11-03


def _never(_day):
    return None


def _changed_on(day: date):
    return lambda _held: datetime(day.year, day.month, day.day, 12)


class TestCalendarHelpers:
    def test_previous_election_day_on_the_day_is_the_day(self):
        assert previous_election_day(ELECTION) == ELECTION

    def test_previous_election_day_the_day_before_is_two_years_back(self):
        assert previous_election_day(ELECTION - timedelta(days=1)) == next_election_day(date(2024, 1, 1))

    def test_new_congress_day_is_january_third(self):
        assert new_congress_day(ELECTION) == date(2027, 1, 3)


class TestResolveActiveElection:
    def test_before_election_day_is_the_campaign(self):
        got = resolve_active_election(ELECTION - timedelta(days=1), _never)
        assert (got.election_day, got.phase) == (ELECTION, CAMPAIGN)
        assert got.results_until is None

    def test_election_day_itself(self):
        got = resolve_active_election(ELECTION, _never)
        assert (got.election_day, got.phase) == (ELECTION, ELECTION_DAY)

    def test_the_morning_after_is_still_this_election(self):
        """next_election_day already says 2028 here — the bug this module
        exists to fix."""
        day_after = ELECTION + timedelta(days=1)
        assert next_election_day(day_after).year == 2028
        got = resolve_active_election(day_after, _never)
        assert (got.election_day, got.phase) == (ELECTION, RESULTS)

    def test_holds_for_the_grace_period_after_the_last_change(self):
        last = ELECTION + timedelta(days=10)
        until = last + timedelta(days=RESULTS_GRACE_DAYS)
        assert resolve_active_election(until, _changed_on(last)).phase == RESULTS
        after = resolve_active_election(until + timedelta(days=1), _changed_on(last))
        assert (after.election_day.year, after.phase) == (2028, CAMPAIGN)

    def test_no_count_at_all_holds_for_the_grace_period_from_election_day(self):
        until = ELECTION + timedelta(days=RESULTS_GRACE_DAYS)
        assert resolve_active_election(until, _never).phase == RESULTS
        assert resolve_active_election(until + timedelta(days=1), _never).phase == CAMPAIGN

    def test_never_holds_past_the_new_congress(self):
        still_counting = _changed_on(date(2027, 1, 1))
        assert resolve_active_election(date(2027, 1, 3), still_counting).phase == RESULTS
        assert resolve_active_election(date(2027, 1, 4), still_counting).phase == CAMPAIGN

    def test_count_is_not_consulted_outside_a_results_window(self):
        def boom(_day):
            raise AssertionError("looked up a count it had no need of")

        assert resolve_active_election(date(2026, 6, 1), boom).phase == CAMPAIGN

    def test_window_end_is_capped(self):
        assert results_window_end(ELECTION, date(2026, 12, 30)) == date(2027, 1, 3)


class TestActiveElectionReadsTheCount:
    def test_last_change_comes_from_race_results(self, db_session):
        db_session.add(Race(id="2026-SEN-CO", cycle_year=2026, office="S", state="CO"))
        changed = datetime(2026, 11, 20, 9)
        db_session.add(RaceResult(
            race_id="2026-SEN-CO", election_date=ELECTION.isoformat(), source_name="x",
            last_change_at=changed,
        ))
        db_session.flush()
        got = active_election(db_session, today=date(2026, 12, 1))
        assert got.phase == RESULTS
        assert got.last_result_change == changed
        assert got.results_until == date(2026, 11, 20) + timedelta(days=RESULTS_GRACE_DAYS)
