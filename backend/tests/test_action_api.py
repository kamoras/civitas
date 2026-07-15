"""Tests for the /action/issues fallback logic in app/api/action.py."""

from app.api.action import _latest_current_issues
from app.models import ActionIssue


def _make_issue(date: str, rank: int, title: str, is_current: bool) -> ActionIssue:
    return ActionIssue(date=date, rank=rank, title=title, is_current=is_current)


class TestLatestCurrentIssues:
    def test_returns_todays_current_issues(self, db_session):
        db_session.add(_make_issue("2026-07-14", 1, "Today's story", is_current=True))
        db_session.add(_make_issue("2026-07-13", 1, "Yesterday's story", is_current=True))
        db_session.commit()

        issues = _latest_current_issues(db_session, for_date="2026-07-14")

        assert [i.title for i in issues] == ["Today's story"]

    def test_no_date_returns_most_recent_current_day(self, db_session):
        db_session.add(_make_issue("2026-07-13", 1, "Older", is_current=True))
        db_session.add(_make_issue("2026-07-14", 1, "Newer", is_current=True))
        db_session.commit()

        issues = _latest_current_issues(db_session)

        assert [i.title for i in issues] == ["Newer"]

    def test_falls_back_to_stale_data_when_nothing_is_current(self, db_session):
        """Regression for 2026-07-14: a wedged hourly refresh retired every
        row (is_current=False table-wide) without inserting replacements.
        The strict is_current query returns nothing even though the DB
        holds a perfectly good day of data — the fallback must still
        surface it rather than leaving the action center blank."""
        db_session.add(_make_issue("2026-07-14", 1, "Stale but real", is_current=False))
        db_session.add(_make_issue("2026-07-14", 2, "Also stale", is_current=False))
        db_session.commit()

        issues = _latest_current_issues(db_session)

        assert {i.title for i in issues} == {"Stale but real", "Also stale"}

    def test_requested_date_with_no_current_rows_falls_back_to_that_dates_data(self, db_session):
        db_session.add(_make_issue("2026-07-14", 1, "Stale today", is_current=False))
        db_session.add(_make_issue("2026-07-13", 1, "Current yesterday", is_current=True))
        db_session.commit()

        issues = _latest_current_issues(db_session, for_date="2026-07-14")

        assert [i.title for i in issues] == ["Stale today"]

    def test_empty_table_returns_empty_list(self, db_session):
        assert _latest_current_issues(db_session) == []

    def test_prefers_current_issues_over_stale_when_both_exist(self, db_session):
        db_session.add(_make_issue("2026-07-14", 1, "Current", is_current=True))
        db_session.add(_make_issue("2026-07-14", 2, "Stale", is_current=False))
        db_session.commit()

        issues = _latest_current_issues(db_session, for_date="2026-07-14")

        assert [i.title for i in issues] == ["Current"]
