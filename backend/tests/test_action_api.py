"""Tests for the /action/issues fallback logic in app/api/action.py."""

from app.api.action import _latest_current_issues
from app.issue_ids import to_public_id
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
        # stored congress.gov URL stays available verbatim as the fact-check fallback
        assert resp["relatedBills"][0]["url"] == (
            "https://www.congress.gov/bill/119th-congress/house-bill/22"
        )

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


class TestElectionsAndTimelineRoutesUseCanonicalClock:
    """get_open_comments/get_election_info/get_timeline all compute
    "today" via app.time_utils.utcnow — must not silently regress to a
    local-timezone-dependent date.today()/datetime.now() call, which
    could compute a different calendar day/year right at a UTC boundary
    depending on the container's local timezone (2026-07-23 timezone-
    consistency pass)."""

    async def test_get_election_info_runs_against_an_empty_db(self, db_session):
        from fastapi import Response

        from app.api.action import get_election_info

        result = await get_election_info(Response(), db=db_session)
        assert "nextElection" in result
        assert result["nextElection"]["daysUntil"] >= 0
        assert result["senateSeatsUp"] > 0

    def test_get_open_comments_runs_against_an_empty_db(self, db_session):
        from fastapi import Response

        from app.api.action import get_open_comments

        result = get_open_comments(Response(), db=db_session)
        assert result == []

    async def test_get_timeline_defaults_year_from_the_canonical_clock(self, db_session):
        from datetime import datetime
        from unittest.mock import patch

        from fastapi import Response

        from app.api.action import get_timeline

        with patch("app.api.action.utcnow", return_value=datetime(2026, 3, 15)):
            result = await get_timeline(Response(), year=None, db=db_session)
        assert result["year"] == 2026


class TestElectionInfoSpecialSenateRaces:
    """get_election_info merges data-derived special Senate races (Race
    rows with is_special, synced from FEC by the election pipeline) into
    the calendar-derived class rotation (2026-07 review F16) — so the
    Action Center teaser and /api/elections can't disagree about which
    states have a Senate race."""

    def _fl_entry(self, result):
        return next(s for s in result["states"] if s["state"] == "FL")

    async def test_special_race_adds_state_and_seat_count(self, db_session):
        from datetime import datetime
        from unittest.mock import patch

        from fastapi import Response

        from app.api.action import get_election_info
        from app.models import Race

        # FL's Class 3 seat is NOT in the 2026 (Class II) rotation — only
        # the pipeline-synced special race can put it on the map.
        db_session.add(Race(
            id="2026-SEN-FL-SPECIAL", cycle_year=2026, office="S",
            state="FL", is_special=True,
        ))
        db_session.commit()

        with patch("app.api.action.utcnow", return_value=datetime(2026, 7, 24)):
            result = await get_election_info(Response(), db=db_session)

        assert self._fl_entry(result)["hasSenateRace"] is True
        assert result["senateSeatsUp"] == 34  # 33 Class II + FL special

    async def test_without_special_race_fl_has_no_senate_race(self, db_session):
        from datetime import datetime
        from unittest.mock import patch

        from fastapi import Response

        from app.api.action import get_election_info

        with patch("app.api.action.utcnow", return_value=datetime(2026, 7, 24)):
            result = await get_election_info(Response(), db=db_session)

        assert self._fl_entry(result)["hasSenateRace"] is False
        assert result["senateSeatsUp"] == 33  # the Class II rotation alone


class TestSingleIssueEnrichment:
    """The single-issue endpoint backs the /issue/{id} full-story page, which
    is a cold entry point from social posts. It has to return the same
    enrichment the list endpoint does, not a stripped-down version."""

    def _make_issue_with_doc(self, db):
        import json
        from app.models import ExploreDocument

        doc = ExploreDocument(
            doc_type="proposed_rule",
            source="federal_register",
            title="Proposed Rule on Something",
            date="2026-07-20",
            url="https://www.federalregister.gov/d/2026-1",
            comment_url="https://www.regulations.gov/comment/1",
            comments_close_on="2026-12-31",
        )
        db.add(doc)
        db.flush()

        issue = ActionIssue(
            date="2026-07-22", rank=1, title="Issue", summary="s",
            related_explore_ids=json.dumps([doc.id]), is_current=True,
        )
        db.add(issue)
        db.commit()
        return issue, doc

    async def test_single_issue_returns_related_explore_docs(self, db_session):
        """Regression: the endpoint used to pass an empty prefetch map, which
        resolved every related explore id to a miss and silently returned no
        documents — so the full-story page could never show them."""
        from fastapi import Response

        from app.api.action import get_action_issue

        issue, doc = self._make_issue_with_doc(db_session)

        resp = await get_action_issue(to_public_id(issue.id), Response(), db=db_session)

        assert [d["id"] for d in resp["relatedExploreDocs"]] == [doc.id]
        assert resp["relatedExploreDocs"][0]["title"] == "Proposed Rule on Something"
        assert resp["relatedExploreDocs"][0]["commentUrl"] == (
            "https://www.regulations.gov/comment/1"
        )
        assert resp["relatedExploreDocs"][0]["commentsCloseOn"] == "2026-12-31"

    async def test_single_issue_matches_the_list_endpoint(self, db_session):
        """Whatever the list endpoint exposes for an issue, the detail endpoint
        must expose too — otherwise the full-story page silently degrades."""
        from fastapi import Response

        from app.api.action import get_action_issue, get_action_issues

        issue, _ = self._make_issue_with_doc(db_session)

        listed = await get_action_issues(Response(), date=issue.date, db=db_session, db_visits=db_session)
        single = await get_action_issue(to_public_id(issue.id), Response(), db=db_session)

        assert single == listed["issues"][0]


class TestIssueLookupByPublicId:
    """A public id (app/issue_ids.py) replaced the raw autoincrement id as
    the identifier shown to readers and published in share links (#issue
    relabeling). Old links pointing at the bare id still have to resolve."""

    async def test_looks_up_by_public_id(self, db_session):
        from fastapi import Response

        from app.api.action import get_action_issue

        issue = ActionIssue(date="2026-08-19", rank=1, title="Issue", summary="s")
        db_session.add(issue)
        db_session.commit()

        resp = await get_action_issue(to_public_id(issue.id), Response(), db=db_session)

        assert resp["id"] == issue.id
        assert resp["publicId"] == to_public_id(issue.id)

    async def test_falls_back_to_legacy_numeric_id(self, db_session):
        """A Bluesky post from before public_id existed links to the bare
        int id — it has to keep resolving, not 404 a link that's already
        out in the world."""
        from fastapi import Response

        from app.api.action import get_action_issue

        issue = ActionIssue(date="2026-08-19", rank=1, title="Issue", summary="s")
        db_session.add(issue)
        db_session.commit()

        resp = await get_action_issue(str(issue.id), Response(), db=db_session)

        assert resp["id"] == issue.id

    async def test_unknown_identifier_404s(self, db_session):
        from fastapi import HTTPException, Response

        from app.api.action import get_action_issue

        try:
            await get_action_issue("iNoSuchIssue", Response(), db=db_session)
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 404

    async def test_oversized_numeric_id_404s_instead_of_500ing(self, db_session):
        """A digit string past SQLite's 8-byte INTEGER range (a bot, a
        mistyped URL) used to reach the DB driver and raise OverflowError
        instead of just missing."""
        from fastapi import HTTPException, Response

        from app.api.action import get_action_issue

        try:
            await get_action_issue("9" * 40, Response(), db=db_session)
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 404


class TestFirstSurfacedDate:
    """`date` is bumped to today on every pipeline run that re-matches a
    story, whether or not anything changed (action_center.py's
    _apply_matched_issue_update) — so a week-old story that's still trending
    displays today's date as if that's when it happened. `first_surfaced`
    (issue.created_at) is set once at insert and never touched again."""

    async def test_first_surfaced_stays_fixed_while_date_advances(self, db_session):
        from datetime import datetime

        from fastapi import Response

        from app.api.action import get_action_issue

        issue = ActionIssue(
            date="2026-08-15", rank=1, title="Issue", summary="s",
            created_at=datetime(2026, 8, 15, 9, 0, 0),
        )
        db_session.add(issue)
        db_session.commit()

        # Simulate _apply_matched_issue_update re-matching this story to
        # fresh coverage five days later without touching created_at.
        issue.date = "2026-08-20"
        db_session.commit()

        resp = await get_action_issue(to_public_id(issue.id), Response(), db=db_session)

        assert resp["firstSurfaced"] == "2026-08-15"
        assert resp["date"] == "2026-08-20"


class TestTrendingFlag:
    """is_trending (app/trending.py) needs the whole day's view-count
    spread, so it's only computed by the list endpoint — see
    ActionIssueSchema.is_trending's docstring."""

    async def test_issue_with_no_recorded_views_is_not_trending(self, db_session):
        from fastapi import Response

        from app.api.action import get_action_issues

        issue = ActionIssue(date="2026-08-19", rank=1, title="Issue", summary="s")
        db_session.add(issue)
        db_session.commit()

        resp = await get_action_issues(
            Response(), date=issue.date, db=db_session, db_visits=db_session,
        )

        assert resp["issues"][0]["isTrending"] is False

    async def test_issue_clearing_the_traction_bar_is_flagged_trending(self, db_session):
        from fastapi import Response

        from app.api.action import get_action_issues
        from app.models import IssueView
        from app.time_utils import utcnow

        today = utcnow().date().isoformat()
        hot = ActionIssue(date=today, rank=1, title="Hot issue", summary="s")
        quiet = ActionIssue(date=today, rank=2, title="Quiet issue", summary="s")
        db_session.add_all([hot, quiet])
        db_session.commit()

        db_session.add(IssueView(date=today, issue_public_id=to_public_id(hot.id), count=100))
        db_session.add(IssueView(date=today, issue_public_id=to_public_id(quiet.id), count=1))
        db_session.commit()

        resp = await get_action_issues(
            Response(), date=today, db=db_session, db_visits=db_session,
        )

        by_title = {i["title"]: i["isTrending"] for i in resp["issues"]}
        assert by_title == {"Hot issue": True, "Quiet issue": False}

    async def test_single_issue_endpoint_never_flags_trending(self, db_session):
        """get_action_issue has no peer issues to judge against — always
        False rather than a misleading answer computed from nothing."""
        from fastapi import Response

        from app.api.action import get_action_issue
        from app.models import IssueView
        from app.time_utils import utcnow

        today = utcnow().date().isoformat()
        issue = ActionIssue(date=today, rank=1, title="Issue", summary="s")
        db_session.add(issue)
        db_session.commit()
        db_session.add(IssueView(date=today, issue_public_id=to_public_id(issue.id), count=1000))
        db_session.commit()

        resp = await get_action_issue(to_public_id(issue.id), Response(), db=db_session)

        assert resp["isTrending"] is False
