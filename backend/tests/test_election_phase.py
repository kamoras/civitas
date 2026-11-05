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

    def test_the_grace_period_counts_from_the_eastern_date(self, db_session):
        """Stored stamps are UTC: 9 PM ET on the 16th is the 17th in UTC,
        which held the page a day longer than the rule says."""
        db_session.add(Race(id="2026-SEN-CO", cycle_year=2026, office="S", state="CO"))
        db_session.add(RaceResult(
            race_id="2026-SEN-CO", election_date=ELECTION.isoformat(), source_name="x",
            last_change_at=datetime(2026, 11, 17, 2),
        ))
        db_session.flush()
        got = active_election(db_session, today=date(2026, 11, 20))
        assert got.results_until == date(2026, 11, 16) + timedelta(days=RESULTS_GRACE_DAYS)


class TestFreezeUtcnowPinsTheElectionDate:
    """conftest.freeze_utcnow pins the election date too: a test frozen at
    a September instant that reaches active_election() otherwise followed
    the real calendar into the results window, then the next cycle."""

    def test_the_frozen_instant_is_read_as_its_eastern_date(self, freeze_utcnow, db_session):
        from app.api import elections
        from app.election_phase import active_election

        # 01:30 UTC on Nov 4 is still election day (Nov 3) in the East.
        freeze_utcnow(datetime(2026, 11, 4, 1, 30))
        assert active_election(db_session).phase == ELECTION_DAY
        # Modules that imported the function by name read it too.
        assert elections.election_today() == date(2026, 11, 3)

    def test_a_date_the_test_pinned_itself_stands(self, freeze_utcnow, monkeypatch):
        from app import election_phase

        monkeypatch.setattr(election_phase, "election_today", lambda: date(2026, 10, 1))
        freeze_utcnow(datetime(2026, 11, 20, 12))
        assert election_phase.election_today() == date(2026, 10, 1)


class TestSettledCount:
    """Once every counted race is settled the results stay up
    SETTLED_GRACE_DAYS more, not RESULTS_GRACE_DAYS after the count's last
    move; the count itself is still read to each state's certification
    deadline (counting_election)."""

    def test_a_settled_count_ends_the_window_early(self):
        from app.election_phase import SETTLED_GRACE_DAYS

        settled = _changed_on(ELECTION + timedelta(days=1))
        still_moving = _changed_on(ELECTION + timedelta(days=20))
        until = ELECTION + timedelta(days=1 + SETTLED_GRACE_DAYS)
        got = resolve_active_election(until, still_moving, settled)
        assert got.phase == RESULTS and got.results_until == until
        after = resolve_active_election(until + timedelta(days=1), still_moving, settled)
        assert (after.election_day.year, after.phase) == (2028, CAMPAIGN)

    def test_an_open_race_keeps_the_usual_window(self):
        last = ELECTION + timedelta(days=10)
        day = last + timedelta(days=RESULTS_GRACE_DAYS)
        assert resolve_active_election(day, _changed_on(last), _never).phase == RESULTS

    def test_election_day_is_never_cut_short(self):
        assert resolve_active_election(ELECTION, _never, _changed_on(ELECTION)).phase == ELECTION_DAY

    def test_counting_runs_to_the_latest_certification_deadline(self):
        from app import certification_deadlines
        from app.election_phase import counting_window_end

        latest = max(certification_deadlines.deadline(s, ELECTION) for s in certification_deadlines.states())
        assert counting_window_end(ELECTION, ELECTION) == latest + timedelta(days=1)
        assert results_window_end(ELECTION, ELECTION) < latest

    def _race(self, db, rid, settled_at):
        db.add(Race(id=rid, cycle_year=2026, office="S", state=rid[-2:]))
        db.add(RaceResult(race_id=rid, election_date=ELECTION.isoformat(), source_name="x",
                          last_change_at=datetime(2026, 11, 4, 9), settled_at=settled_at))
        db.flush()

    def test_read_from_every_counted_race(self, db_session):
        from app.election_phase import SETTLED_GRACE_DAYS, all_settled_at, counting_election

        self._race(db_session, "2026-SEN-CO", datetime(2026, 11, 4, 6))
        self._race(db_session, "2026-SEN-IA", None)
        assert all_settled_at(db_session, ELECTION) is None
        db_session.get(RaceResult, "2026-SEN-IA").settled_at = datetime(2026, 11, 5, 3)
        db_session.flush()
        assert all_settled_at(db_session, ELECTION) == datetime(2026, 11, 5, 3)
        # 03:00 UTC is still the 4th in the East.
        day = date(2026, 11, 4) + timedelta(days=SETTLED_GRACE_DAYS + 1)
        shown = active_election(db_session, today=day)
        assert (shown.election_day.year, shown.phase) == (2028, CAMPAIGN)
        # The count is still read: the page moved on, the archive and the
        # deadline rule did not.
        counting = counting_election(db_session, today=day)
        assert counting is not None and counting.election_day == ELECTION
        assert counting.counting_until >= date(2026, 12, 7)

    def test_nothing_counted_is_never_settled(self, db_session):
        from app.election_phase import all_settled_at

        assert all_settled_at(db_session, ELECTION) is None

    def test_no_counting_after_the_new_congress(self, db_session):
        from app.election_phase import counting_election

        assert counting_election(db_session, today=date(2027, 1, 4)) is None
        assert counting_election(db_session, today=date(2026, 6, 1)) is None
