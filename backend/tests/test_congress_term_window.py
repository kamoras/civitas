"""Tests for the "current term" = "current congress" windowing helpers.

fetch_significant_bills/_recent_congresses_only/RECENT_RC_SESSIONS previously
windowed to 2-3 congresses (career-ish lookback); scores should reflect the
current congress only. See AGENTS.md "current term" and fetch/congress.py.
"""

from datetime import datetime, timezone
from unittest.mock import patch

from app.pipeline.fetch.congress import (
    _recent_congresses_only,
    congress_first_year,
    congress_for_year,
    expected_current_congress,
)


def test_congress_first_year_119th_is_2025():
    assert congress_first_year(119) == 2025


def test_congress_first_year_matches_known_anchors():
    # 1st Congress convened 1789; 116th (2019-2021) is a well-known anchor.
    assert congress_first_year(1) == 1789
    assert congress_first_year(116) == 2019


def test_recent_congresses_only_excludes_previous_congress():
    with patch("app.pipeline.fetch.congress.settings.CURRENT_CONGRESS", 119):
        bills = [
            {"congress": 119, "number": "1"},
            {"congress": 118, "number": "2"},
            {"congress": 117, "number": "3"},
        ]
        result = _recent_congresses_only(bills)
        assert [b["number"] for b in result] == ["1"]


def test_recent_congresses_only_keeps_current_congress_bills():
    with patch("app.pipeline.fetch.congress.settings.CURRENT_CONGRESS", 119):
        bills = [{"congress": 119, "number": "a"}, {"congress": 119, "number": "b"}]
        result = _recent_congresses_only(bills)
        assert len(result) == 2


def test_congress_for_year_is_inverse_of_first_year():
    for congress in (1, 116, 119, 120, 200):
        assert congress_for_year(congress_first_year(congress)) == congress
    # 2025-2026 is the 119th; 2027 begins the 120th.
    assert congress_for_year(2025) == 119
    assert congress_for_year(2026) == 119
    assert congress_for_year(2027) == 120


def test_expected_current_congress_tracks_the_clock():
    with patch(
        "app.time_utils.utcnow",
        return_value=datetime(2027, 6, 1, tzinfo=timezone.utc),
    ):
        assert expected_current_congress() == 120


class TestCongressStalenessGuard:
    def test_alerts_when_a_pin_is_behind_the_calendar(self):
        # An environment pin still says 119 while the clock is in the 120th.
        from app.config import Settings

        with patch("app.config.settings", Settings(CURRENT_CONGRESS=119)), patch(
            "app.pipeline.fetch.congress.expected_current_congress",
            return_value=120,
        ), patch("app.ops_alerts.send_ops_alert") as mock_alert:
            from app.ops_alerts import check_current_congress_staleness

            check_current_congress_staleness()
            assert mock_alert.called
            # The alert names the Congress in session and the pin.
            text = mock_alert.call_args.args[1]
            assert "120th" in text and "pinned in the environment" in text and "to 119," in text

    def test_unpinned_never_alerts_even_across_the_hand_over(self):
        """Unpinned, the check advances the value first; it can read behind
        only if noon passes between its two reads — not staleness, and no
        "pinned" alert."""
        from app.config import Settings

        s = Settings()
        assert not s.current_congress_pinned
        with patch("app.config.settings", s), patch(
            "app.pipeline.fetch.congress.expected_current_congress",
            return_value=s.CURRENT_CONGRESS + 1,
        ), patch("app.ops_alerts.send_ops_alert") as mock_alert, patch(
            "app.ops_alerts.resolve_ops_alert",
        ) as resolved:
            from app.ops_alerts import check_current_congress_staleness

            check_current_congress_staleness()
        mock_alert.assert_not_called()
        resolved.assert_called_once_with("stale-congress")

    def test_silent_when_config_matches(self):
        with patch("app.config.settings.CURRENT_CONGRESS", 120), patch(
            "app.pipeline.fetch.congress.expected_current_congress",
            return_value=120,
        ), patch("app.ops_alerts.send_ops_alert") as mock_alert:
            from app.ops_alerts import check_current_congress_staleness

            check_current_congress_staleness()
            assert not mock_alert.called

    def test_silent_when_config_is_ahead(self):
        # An operator who bumped early (or a mid-term convening edge) must
        # not trigger a spurious "stale" alert.
        with patch("app.config.settings.CURRENT_CONGRESS", 121), patch(
            "app.pipeline.fetch.congress.expected_current_congress",
            return_value=120,
        ), patch("app.ops_alerts.send_ops_alert") as mock_alert:
            from app.ops_alerts import check_current_congress_staleness

            check_current_congress_staleness()
            assert not mock_alert.called


def test_expected_current_congress_waits_for_noon_et_on_jan_3():
    """A calendar-year rule said 120 on Jan 1, 2027 and raised a false
    "stale" alert for the two days the 119th is still in office."""
    with patch("app.time_utils.utcnow", return_value=datetime(2027, 1, 2, 12)):
        assert expected_current_congress() == 119
    with patch("app.time_utils.utcnow", return_value=datetime(2027, 1, 3, 17, 0)):
        assert expected_current_congress() == 120


class TestCongressStalenessMessage:
    """Only an environment pin can go stale (it needs editing); an unpinned
    value advances at the start of each pipeline job."""

    def _alert_text(self, settings_obj):
        expected = settings_obj.CURRENT_CONGRESS + 1
        with patch("app.config.settings", settings_obj), patch(
            "app.pipeline.fetch.congress.expected_current_congress", return_value=expected,
        ), patch("app.ops_alerts.send_ops_alert") as mock_alert:
            from app.ops_alerts import check_current_congress_staleness

            check_current_congress_staleness()
        return mock_alert.call_args.args[1]

    def test_unpinned_advances_instead_of_alerting(self):
        """Unpinned, a process still running when a new Congress convenes
        advances (app.config.advance_current_congress) — no restart, and
        nothing to alert about."""
        from app import config
        from app.config import Settings

        s = Settings()
        s.CURRENT_CONGRESS -= 1  # computed before the next Congress convened
        expected = s.CURRENT_CONGRESS + 1
        with patch.object(config, "settings", s), patch("app.ops_alerts.settings", s), patch(
            "app.time_utils.utcnow", return_value=datetime(2027, 1, 4, 3),
        ), patch(
            "app.pipeline.fetch.congress.expected_current_congress", return_value=expected,
        ), patch("app.ops_alerts.send_ops_alert") as mock_alert:
            from app.ops_alerts import check_current_congress_staleness

            check_current_congress_staleness()
        mock_alert.assert_not_called()

    def test_environment_pin_says_edit_the_pin(self):
        from app import config
        from app.config import Settings

        s = Settings(CURRENT_CONGRESS=119)
        with patch.object(config, "settings", s):
            text = self._alert_text(s)
        assert "pinned in the environment" in text and "Remove the pin" in text
        # The pin holds the windows and the district lines alike.
        assert "district lines" in text and "scored windows" in text
        assert "on the 119th Congress" in text and "120th Congress's members" in text


def test_env_example_does_not_pin_current_congress():
    """docker compose passes .env to the backend, and any CURRENT_CONGRESS
    there is an operator pin (Settings.current_congress_pinned): it freezes
    the scored windows AND the district lines (one value) past the
    next Jan 3. The template must leave it unset — a commented-out example
    only."""
    import pathlib
    import re

    example = pathlib.Path(__file__).resolve().parents[2] / ".env.example"
    text = example.read_text()
    # A live assignment, as a dotenv parser reads one (optional `export`);
    # comment lines don't count.
    assert not re.search(r"(?m)^\s*(?:export\s+)?CURRENT_CONGRESS\s*=", text)
    assert re.search(r"(?m)^#\s*CURRENT_CONGRESS=", text), "keep the documented, commented-out example"


class TestHouseRollCallsFollowTheHeldCongress:
    """The House's recent roll calls come from the Clerk's per-year files.
    The year used to be the clock's, and nothing filtered by Congress: a job
    holding the 119th whose House step ran on Jan 3 2027 after the 120th's
    first votes read them in."""

    def test_the_year_is_clamped_to_the_held_congress(self):
        from app.pipeline.house_pipeline import roll_call_year

        assert roll_call_year(119, datetime(2027, 1, 3, 18)) == (2026, False)
        assert roll_call_year(120, datetime(2027, 1, 3, 18)) == (2027, True)
        assert roll_call_year(119, datetime(2026, 3, 1)) == (2026, True)
        assert roll_call_year(119, datetime(2026, 9, 1)) == (2026, False)
        assert roll_call_year(119, datetime(2025, 2, 1)) == (2025, True)

    def test_roll_calls_of_another_congress_are_dropped(self):
        from app.pipeline.house_pipeline import in_congress

        rcs = [{"rollNumber": 1, "congress": 119}, {"rollNumber": 2, "congress": 120}, {"rollNumber": 3, "congress": 0}]
        assert [rc["rollNumber"] for rc in in_congress(rcs, 120)] == [2, 3]
        assert [rc["rollNumber"] for rc in in_congress(rcs, 119)] == [1, 3]


def test_the_les_reference_is_labelled_with_the_congress_it_measured(monkeypatch):
    """The startup rescore measures STORED bills: after a restart between
    noon ET on Jan 3 and the next run, those are still the 119th's while the
    job holds the 120th. The reference names the 119th — the data's."""
    from app import config
    from app.pipeline import live_references

    captured = {}
    monkeypatch.setattr(
        "app.pipeline.analyze.score_calculator.compute_les_reference",
        lambda members, congress, majority, rates: captured.setdefault("congress", congress),
    )
    members = [([{"congress": 119}, {"congress": 119}], "R"), ([], "D")]
    with patch.object(config.settings, "CURRENT_CONGRESS", 120):
        live_references.measure_les_reference("house", members, "R")
        assert captured["congress"] == 119
        captured.clear()
        live_references.measure_les_reference("house", [([], "R")], "R")
        assert captured["congress"] == 120
