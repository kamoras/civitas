"""Tests for app.config's computed defaults."""

from datetime import date, datetime, timezone
from unittest.mock import patch

from app import config
from app.config import _default_current_congress, sitting_congress
from app.pipeline.fetch.congress import congress_for_year, congress_of_date
from app.time_utils import congress_in_session


class TestDefaultCurrentCongress:
    """CURRENT_CONGRESS used to be a hardcoded literal (119) that only a
    separate ops alert could catch going stale after a new Congress
    convened. Now computed from the wall clock so it never needs a manual
    bump — at process start (see sitting_congress for the live reading)."""

    def test_is_todays_congress(self):
        assert _default_current_congress() == congress_of_date(date.today().isoformat())

    def test_a_process_started_before_noon_on_jan_3_is_not_stuck_on_the_outgoing_congress(self):
        """The value is fixed for the life of the process and scopes the
        roll-call sessions, bill windows and ideal points. Under the
        noon-ET-Jan-3 rule, a backend started at 11:59 ET on Jan 3, 2027
        would score the dead 119th for the whole 120th Congress until a
        restart; the date rule gives 120, a few hours early at worst."""

        class _Jan3(datetime):
            @classmethod
            def today(cls):
                return cls(2027, 1, 3, 11, 59)

        with patch("app.config.datetime.date", _Jan3):
            assert _default_current_congress() == 120
        assert congress_in_session(datetime(2027, 1, 3, 16, 59)) == 119  # sitting_congress's rule differs

    def test_matches_pipeline_formula_across_years(self):
        for year in (2025, 2026, 2027, 2028, 2033):
            assert congress_for_year(year) == 1 + (year - 1789) // 2

    def test_returns_119_for_2026(self):
        assert congress_for_year(2026) == 119

    def test_returns_120_for_2027(self):
        assert congress_for_year(2027) == 120


    def test_the_new_congress_starts_when_it_convenes(self):
        # January 3 of an odd year (20th Amendment), not January 1: a day
        # early, every scored window would point at a Congress with no bills.
        assert _default_current_congress(date(2027, 1, 2)) == 119
        assert _default_current_congress(date(2027, 1, 3)) == 120
        assert _default_current_congress(date(2026, 1, 1)) == 119

    def test_the_staleness_check_never_calls_the_default_stale(self):
        """expected_current_congress follows the noon-ET hand-over; the
        default switches at the date. The default may run a few hours
        ahead on Jan 3 (not staleness — the alert fires only when it is
        BEHIND), and the two agree on every other day."""
        from app.pipeline.fetch.congress import expected_current_congress
        for day in (date(2027, 1, 2), date(2027, 1, 3), date(2026, 6, 1)):
            for hour in (0, 12, 16, 17, 23):
                now = datetime(day.year, day.month, day.day, hour)
                assert expected_current_congress(now) <= _default_current_congress(day)
        assert expected_current_congress(datetime(2027, 1, 3, 17)) == _default_current_congress(date(2027, 1, 3))
        assert expected_current_congress(datetime(2027, 1, 2, 23)) == _default_current_congress(date(2027, 1, 2))

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

    def test_defaults_to_the_clock(self):
        with patch("app.time_utils.utcnow", return_value=datetime(2029, 1, 3, 17, 0)):
            assert congress_in_session() == 121


class TestSittingCongress:
    def test_follows_the_clock_without_a_restart(self, monkeypatch):
        """settings.CURRENT_CONGRESS is fixed when the process starts;
        sitting_congress() re-reads the clock on every call."""
        monkeypatch.setattr(config, "settings", config.Settings())
        assert not config.settings.current_congress_pinned
        with patch("app.time_utils.utcnow", return_value=datetime(2027, 1, 3, 16, 59)):
            assert sitting_congress() == 119
        with patch("app.time_utils.utcnow", return_value=datetime(2027, 1, 3, 17, 0)):
            assert sitting_congress() == 120

    def test_assigning_the_setting_later_is_not_a_pin(self, monkeypatch):
        """Pydantic adds an assigned field to model_fields_set; a test (or
        anything) that sets CURRENT_CONGRESS after startup must not turn
        the clock off for the rest of the process."""
        s = config.Settings()
        s.CURRENT_CONGRESS = 119
        assert "CURRENT_CONGRESS" in s.model_fields_set and not s.current_congress_pinned
        monkeypatch.setattr(config, "settings", s)
        with patch("app.time_utils.utcnow", return_value=datetime(2027, 6, 1)):
            assert sitting_congress() == 120

    def test_an_environment_pin_wins(self, monkeypatch):
        monkeypatch.setenv("CURRENT_CONGRESS", "118")
        monkeypatch.setattr(config, "settings", config.Settings())
        assert config.settings.current_congress_pinned
        with patch("app.time_utils.utcnow", return_value=datetime(2027, 6, 1)):
            assert sitting_congress() == 118
