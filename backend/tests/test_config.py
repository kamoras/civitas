"""Tests for app.config's computed defaults."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app import config
from app.config import _default_current_congress, advance_current_congress, scoring_congress
from app.pipeline.fetch.congress import congress_for_year, congress_of_date
from app.time_utils import congress_in_session


class TestDefaultCurrentCongress:
    """CURRENT_CONGRESS used to be a hardcoded literal (119) that only a
    separate ops alert could catch going stale after a new Congress
    convened. Now computed from the clock at process start, and advanced at
    the start of each pipeline job (scoring_congress)."""

    def test_is_the_congress_in_office(self):
        with patch("app.time_utils.utcnow", return_value=datetime(2027, 2, 1)):
            assert _default_current_congress() == congress_in_session() == 120
            assert config.Settings().CURRENT_CONGRESS == 120

    def test_follows_the_noon_et_hand_over_like_the_district_lines(self):
        """A process started between midnight and noon ET on Jan 3 used to
        get the new Congress's windows (a date rule) beside the outgoing
        Congress's district lines (noon). One rule for both now."""
        assert _default_current_congress(datetime(2027, 1, 3, 5)) == 119  # 00:00 ET
        assert _default_current_congress(datetime(2027, 1, 3, 16, 59)) == 119  # 11:59 ET
        assert _default_current_congress(datetime(2027, 1, 3, 17)) == 120  # noon ET
        assert _default_current_congress(datetime(2027, 1, 2, 12)) == 119
        assert _default_current_congress(datetime(2026, 1, 1)) == 119

    @pytest.mark.parametrize("year, congress", [
        (2025, 119), (2026, 119), (2027, 120), (2028, 120), (2033, 123),
    ])
    def test_pipeline_formula_across_years(self, year, congress):
        assert congress_for_year(year) == congress

    def test_the_staleness_check_agrees_with_the_default(self):
        from app.pipeline.fetch.congress import expected_current_congress
        for now in (datetime(2027, 1, 2, 23), datetime(2027, 1, 3, 16), datetime(2027, 1, 3, 17), datetime(2026, 6, 1)):
            assert expected_current_congress(now) == _default_current_congress(now)
        assert congress_of_date("2027-01-04") == 120

class TestCongressInSession:
    """20th Amendment: the new Congress's members take office at noon
    (Eastern, the Capitol's) on Jan 3 of an odd year — not at midnight,
    and not on Jan 1 as a calendar-year rule would have it."""

    def test_the_outgoing_congress_holds_office_until_noon_et_on_jan_3(self):
        assert congress_in_session(datetime(2027, 1, 1, 12)) == 119
        assert congress_in_session(datetime(2027, 1, 3, 16, 59, 59)) == 119  # 11:59:59 ET
        assert congress_in_session(datetime(2027, 1, 3, 17, 0)) == 120  # noon ET
        assert congress_in_session(datetime(2027, 2, 1)) == 120

    def test_even_years_and_election_day_do_not_move_it(self):
        assert congress_in_session(datetime(2026, 1, 1)) == 119
        assert congress_in_session(datetime(2026, 11, 10, 15)) == 119
        assert congress_in_session(datetime(2026, 12, 31, 23, 59)) == 119

    def test_accepts_aware_datetimes(self):
        assert congress_in_session(datetime(2027, 1, 3, 17, 0, tzinfo=timezone.utc)) == 120

    def test_tests_run_on_the_pinned_congress_until_they_set_a_clock(self):
        """conftest._sitting_congress_pinned: on the real clock every test
        sees TEST_CONGRESS (process value, a job's advance, a new
        Settings()); a clock the test sets is read as production reads it."""
        from tests.conftest import TEST_CONGRESS

        assert config.settings.CURRENT_CONGRESS == congress_in_session() == TEST_CONGRESS
        assert advance_current_congress() == config.Settings().CURRENT_CONGRESS == TEST_CONGRESS
        with patch("app.time_utils.utcnow", return_value=datetime(2031, 6, 1)):
            assert congress_in_session() == 122
            assert advance_current_congress() == 122

    def test_defaults_to_the_clock(self):
        with patch("app.time_utils.utcnow", return_value=datetime(2029, 1, 3, 17, 0)):
            assert congress_in_session() == 121


class TestScoringCongress:
    """settings.CURRENT_CONGRESS is the one Congress a pipeline job scores —
    windows and district lines alike: advanced to the one in office when a
    job starts (no restart), held for the job, frozen by an operator pin."""

    def _started_on(self, monkeypatch, when):
        """A process started at ``when``, with the clock left there: a job
        the test starts reads it (advance_current_congress), never the real
        one, until the test moves it."""
        monkeypatch.setattr("app.time_utils.utcnow", lambda: when)
        s = config.Settings()
        monkeypatch.setattr(config, "settings", s)
        return s

    def test_a_job_after_noon_advances_a_process_started_before(self, monkeypatch):
        s = self._started_on(monkeypatch, datetime(2026, 12, 20))
        assert s.CURRENT_CONGRESS == 119 and not s.current_congress_pinned
        with patch("app.time_utils.utcnow", return_value=datetime(2027, 1, 3, 16, 59)):
            with scoring_congress() as held:
                assert held == s.CURRENT_CONGRESS == 119
        with patch("app.time_utils.utcnow", return_value=datetime(2027, 1, 4, 3)):
            with scoring_congress() as held:
                assert held == s.CURRENT_CONGRESS == 120
        assert s.CURRENT_CONGRESS == 120

    def test_a_job_holds_its_congress_while_another_advances(self, monkeypatch):
        import threading

        s = self._started_on(monkeypatch, datetime(2026, 12, 20))
        seen = []
        with scoring_congress():
            s.CURRENT_CONGRESS = 120  # another job, elsewhere in the process, advanced it
            seen.append(s.CURRENT_CONGRESS)
            t = threading.Thread(target=lambda: seen.append(s.CURRENT_CONGRESS))
            t.start()
            t.join()
        assert seen == [119, 120]
        assert s.CURRENT_CONGRESS == 120

    def test_a_nested_hold_keeps_the_outer(self, monkeypatch):
        s = self._started_on(monkeypatch, datetime(2026, 12, 20))
        with scoring_congress():
            with patch("app.time_utils.utcnow", return_value=datetime(2027, 2, 1)):
                with scoring_congress() as inner:
                    assert inner == s.CURRENT_CONGRESS == 119

    def test_advancing_inside_a_hold_reads_the_process_value(self, monkeypatch):
        """Another job advancing while this one holds an older Congress:
        compared against the process-wide value, never this hold, so it
        can't move the process back."""
        s = self._started_on(monkeypatch, datetime(2026, 12, 20))
        with scoring_congress():
            s.CURRENT_CONGRESS = 121  # process-wide, set by another job
            with patch("app.time_utils.utcnow", return_value=datetime(2027, 1, 4)):
                assert advance_current_congress() == 119  # this context's hold
        assert s.CURRENT_CONGRESS == 121

    def test_never_moves_back(self, monkeypatch):
        s = self._started_on(monkeypatch, datetime(2027, 2, 1))
        with patch("app.time_utils.utcnow", return_value=datetime(2026, 6, 1)):
            assert advance_current_congress() == s.CURRENT_CONGRESS == 120

    def test_an_environment_pin_wins(self, monkeypatch):
        monkeypatch.setenv("CURRENT_CONGRESS", "118")
        s = config.Settings()
        monkeypatch.setattr(config, "settings", s)
        assert s.current_congress_pinned
        with patch("app.time_utils.utcnow", return_value=datetime(2027, 6, 1)):
            with scoring_congress() as held:
                assert held == 118

    def test_assigning_the_setting_later_is_not_a_pin(self, monkeypatch):
        """Pydantic adds an assigned field to model_fields_set; anything
        that sets CURRENT_CONGRESS after startup must not stop it
        advancing for the rest of the process."""
        s = self._started_on(monkeypatch, datetime(2026, 6, 1))
        s.CURRENT_CONGRESS = 119
        assert "CURRENT_CONGRESS" in s.model_fields_set and not s.current_congress_pinned
        with patch("app.time_utils.utcnow", return_value=datetime(2027, 6, 1)):
            assert advance_current_congress() == 120
