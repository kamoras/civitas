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


class TestRelatedBillInternalLinks:
    """The issues API should point related bills at our own /bills/{id} page
    when we host the bill, keeping congress.gov as the fallback only."""

    def _make_issue_with_bill(self, db, bill_entry):
        import json

        issue = ActionIssue(
            date="2026-07-22", rank=1, title="Issue", summary="s",
            related_bill_ids=json.dumps([bill_entry]), is_current=True,
        )
        db.add(issue)
        db.commit()
        return issue

    def _host_senate_bill(self, db, bill_id="HR.22", congress=119):
        from app.models import Senator, SponsoredBill

        senator = Senator(id="s1", name="Sen. Alpha", state="CA", party="D", is_current=True)
        db.add(senator)
        db.flush()
        db.add(SponsoredBill(
            senator_id=senator.id, bill_id=bill_id, title="A bill",
            congress=congress,
        ))
        db.commit()

    def test_hosted_bill_gets_internal_url(self, db_session):
        from app.api.action import _build_issue_response

        self._host_senate_bill(db_session, "HR.22", congress=119)
        issue = self._make_issue_with_bill(db_session, {
            "name": "SAVE Act", "id": "HR.22",
            "url": "https://www.congress.gov/bill/119th-congress/house-bill/22",
            "congress": 119,
        })

        resp = _build_issue_response(issue, db_session)

        assert resp["relatedBills"][0]["internalUrl"] == "/bills/HR.22"
        # congress.gov URL stays available as the fact-check fallback
        assert "congress.gov" in resp["relatedBills"][0]["url"]

    def test_unhosted_bill_has_no_internal_url(self, db_session):
        from app.api.action import _build_issue_response

        issue = self._make_issue_with_bill(db_session, {
            "name": "Some bill", "id": "S.999",
            "url": "https://www.congress.gov/bill/119th-congress/senate-bill/999",
        })

        resp = _build_issue_response(issue, db_session)

        assert resp["relatedBills"][0]["internalUrl"] is None

    def test_congress_mismatch_blocks_internal_link(self, db_session):
        """A bill number alone is ambiguous across congresses — an issue
        entry that recorded a different congress than our hosted record
        must not link to our (different) bill."""
        from app.api.action import _build_issue_response

        self._host_senate_bill(db_session, "HR.3055", congress=119)
        issue = self._make_issue_with_bill(db_session, {
            "name": "Old appropriations act", "id": "HR.3055",
            "url": "https://www.congress.gov/bill/101st-congress/house-bill/3055",
            "congress": 101,
        })

        resp = _build_issue_response(issue, db_session)

        assert resp["relatedBills"][0]["internalUrl"] is None

    def test_legacy_entry_without_congress_still_links(self, db_session):
        """Rows stored before the congress field existed match by id alone."""
        from app.api.action import _build_issue_response

        self._host_senate_bill(db_session, "HR.22", congress=119)
        issue = self._make_issue_with_bill(db_session, {
            "name": "SAVE Act", "id": "HR.22",
            "url": "https://www.congress.gov/bill/119th-congress/house-bill/22",
        })

        resp = _build_issue_response(issue, db_session)

        assert resp["relatedBills"][0]["internalUrl"] == "/bills/HR.22"

    def test_non_current_sponsor_blocks_internal_link(self, db_session):
        """get_bill_detail only resolves bills sponsored by current members —
        the internal link must apply the same filter or it would 404."""
        from app.api.action import _build_issue_response
        from app.models import Senator, SponsoredBill

        senator = Senator(id="s2", name="Sen. Gone", state="TX", party="R", is_current=False)
        db_session.add(senator)
        db_session.flush()
        db_session.add(SponsoredBill(
            senator_id=senator.id, bill_id="S.55", title="A bill", congress=119,
        ))
        db_session.commit()
        issue = self._make_issue_with_bill(db_session, {
            "name": "A bill", "id": "S.55",
            "url": "https://www.congress.gov/bill/119th-congress/senate-bill/55",
            "congress": 119,
        })

        resp = _build_issue_response(issue, db_session)

        assert resp["relatedBills"][0]["internalUrl"] is None
