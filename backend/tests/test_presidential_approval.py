"""Tests for presidential_approval.py's recent-window filtering."""

from datetime import datetime

from app.pipeline.fetch.presidential_approval import ApprovalPoll, recent_polls


class TestRecentPolls:
    def test_filters_to_window(self):
        polls = [
            ApprovalPoll("01/01/2026", "01/02/2026", 40, 50, 10, None),
            ApprovalPoll("06/01/2026", "06/02/2026", 35, 55, 10, None),
        ]
        recent = recent_polls(polls, days=90, as_of=datetime(2026, 7, 21))
        assert len(recent) == 1
        assert recent[0].start_date == "06/01/2026"

    def test_unparseable_date_excluded(self):
        polls = [ApprovalPoll("not-a-date", "01/02/2026", 40, 50, 10, None)]
        recent = recent_polls(polls, days=90, as_of=datetime(2026, 7, 21))
        assert recent == []
