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


def test_congress_first_year_matches_known_anchors():
    # 1st Congress convened 1789; 116th (2019-2021) is a well-known anchor.
    assert congress_first_year(1) == 1789
    assert congress_first_year(116) == 2019
    assert congress_first_year(119) == 2025


def test_recent_congresses_only_keeps_only_the_current_congress():
    with patch("app.pipeline.fetch.congress.settings.CURRENT_CONGRESS", 119):
        bills = [
            {"congress": 119, "number": "1"},
            {"congress": 118, "number": "2"},
            {"congress": 117, "number": "3"},
            {"congress": 119, "number": "4"},
        ]
        result = _recent_congresses_only(bills)
        assert [b["number"] for b in result] == ["1", "4"]


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


def test_an_environment_value_without_archive_mode_follows_the_clock():
    """A CURRENT_CONGRESS left in a production .env is not a pin: the
    value follows the clock and keeps advancing, so it can't go stale."""
    from app.config import Settings

    with patch("app.time_utils.utcnow", return_value=datetime(2027, 6, 1)):
        s = Settings(CURRENT_CONGRESS=119)
    assert s.CURRENT_CONGRESS == 120 and not s.current_congress_pinned


def test_archive_mode_pins_the_environment_value():
    from app.config import Settings

    with patch("app.time_utils.utcnow", return_value=datetime(2027, 6, 1)):
        s = Settings(CURRENT_CONGRESS=118, ARCHIVE_MODE=True)
    assert s.CURRENT_CONGRESS == 118 and s.current_congress_pinned


def test_expected_current_congress_waits_for_noon_et_on_jan_3():
    """A calendar-year rule said 120 on Jan 1, 2027 and raised a false
    "stale" alert for the two days the 119th is still in office."""
    with patch("app.time_utils.utcnow", return_value=datetime(2027, 1, 2, 12)):
        assert expected_current_congress() == 119
    with patch("app.time_utils.utcnow", return_value=datetime(2027, 1, 3, 17, 0)):
        assert expected_current_congress() == 120


def test_env_example_does_not_pin_current_congress():
    """docker compose passes .env to the backend; with ARCHIVE_MODE, a
    CURRENT_CONGRESS there pins the scored windows AND the district lines
    (Settings.current_congress_pinned). The template must leave both unset:
    commented-out examples only."""
    import pathlib
    import re

    example = pathlib.Path(__file__).resolve().parents[2] / ".env.example"
    text = example.read_text()
    # A live assignment, as a dotenv parser reads one (optional `export`);
    # comment lines don't count.
    assert not re.search(r"(?m)^\s*(?:export\s+)?CURRENT_CONGRESS\s*=", text)
    assert not re.search(r"(?m)^\s*(?:export\s+)?ARCHIVE_MODE\s*=", text)
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
