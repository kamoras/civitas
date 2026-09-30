"""Tests for the "current term" = "current congress" windowing helpers.

fetch_significant_bills/_recent_congresses_only/RECENT_RC_SESSIONS previously
windowed to 2-3 congresses (career-ish lookback); scores should reflect the
current congress only. See AGENTS.md "current term" and fetch/congress.py.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

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


@pytest.mark.parametrize("configured, alerts", [
    # Config still says 119 while the clock is in the 120th Congress.
    pytest.param(119, True, id="alerts_when_config_is_behind_the_calendar"),
    pytest.param(120, False, id="silent_when_config_matches"),
    # An operator who bumped early (or a mid-term convening edge) must
    # not trigger a spurious "stale" alert.
    pytest.param(121, False, id="silent_when_config_is_ahead"),
])
def test_congress_staleness_guard(configured, alerts):
    from app.ops_alerts import check_current_congress_staleness

    with patch("app.config.settings.CURRENT_CONGRESS", configured), patch(
        "app.pipeline.fetch.congress.expected_current_congress",
        return_value=120,
    ), patch("app.ops_alerts.send_ops_alert") as mock_alert:
        check_current_congress_staleness()
    assert mock_alert.called is alerts
    if alerts:
        # The alert names the Congress to bump to.
        assert "120" in mock_alert.call_args.args[1]
