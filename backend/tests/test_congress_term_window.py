"""Tests for the "current term" = "current congress" windowing helpers.

fetch_significant_bills/_recent_congresses_only/RECENT_RC_SESSIONS previously
windowed to 2-3 congresses (career-ish lookback); scores should reflect the
current congress only. See AGENTS.md "current term" and fetch/congress.py.
"""

from unittest.mock import patch

from app.pipeline.fetch.congress import _recent_congresses_only, congress_first_year


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
