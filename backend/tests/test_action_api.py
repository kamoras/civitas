"""Tests for the /action/issues fallback logic in app/api/action.py."""

import json
from unittest.mock import patch

import numpy as np
import pytest

from app.api.action import _latest_current_issues
from app.issue_ids import to_public_id
from app.models import ActionIssue, ActionIssueStatus, Senator


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

    def test_stale_fallback_never_returns_duplicate_ranks(self, db_session):
        """Regression, confirmed live 2026-08-21: rows retired across
        several DIFFERENT hourly runs can share one `date` value (date is
        only touched by a match/create, never by retirement) while each
        held rank 1 from ITS OWN run's independent renumbering pass — the
        stale-data fallback used to hand those raw stored ranks straight
        to the reader, so the Action Center showed four simultaneous
        "#1"s."""
        db_session.add(_make_issue("2026-08-20", 1, "Was #1 this morning", is_current=False))
        db_session.add(_make_issue("2026-08-20", 1, "Was #1 this evening", is_current=False))
        db_session.add(_make_issue("2026-08-20", 2, "Was #2 twice", is_current=False))
        db_session.add(_make_issue("2026-08-20", 2, "Also #2 twice", is_current=False))
        db_session.commit()

        issues = _latest_current_issues(db_session)

        assert sorted(i.rank for i in issues) == [1, 2, 3, 4]

    def test_requested_date_fallback_also_gets_clean_ranks(self, db_session):
        db_session.add(_make_issue("2026-07-14", 1, "First one", is_current=False))
        db_session.add(_make_issue("2026-07-14", 1, "Second one", is_current=False))
        db_session.commit()

        issues = _latest_current_issues(db_session, for_date="2026-07-14")

        assert sorted(i.rank for i in issues) == [1, 2]


    def test_a_developing_draft_from_before_midnight_stays_listed(self, db_session):
        """An election-night flip drafted at 11:50 PM ET must not drop off
        the list when the first confirmed story of the next day lands."""
        db_session.add(_make_issue("2026-11-04", 1, "Morning story", is_current=True))
        draft = _make_issue("2026-11-03", 999, "Leads in a seat", is_current=True)
        draft.status = ActionIssueStatus.DEVELOPING
        db_session.add(draft)
        db_session.commit()

        issues = _latest_current_issues(db_session)

        assert [i.title for i in issues] == ["Morning story", "Leads in a seat"]

    def test_a_developing_draft_dated_ahead_does_not_hide_the_confirmed_day(self, db_session):
        db_session.add(_make_issue("2026-11-03", 1, "Evening story", is_current=True))
        draft = _make_issue("2026-11-04", 999, "Leads in a seat", is_current=True)
        draft.status = ActionIssueStatus.DEVELOPING
        db_session.add(draft)
        db_session.commit()

        issues = _latest_current_issues(db_session)

        assert [i.title for i in issues] == ["Evening story", "Leads in a seat"]

    def test_only_developing_drafts_are_still_listed(self, db_session):
        draft = _make_issue("2026-11-04", 999, "Leads in a seat", is_current=True)
        draft.status = ActionIssueStatus.DEVELOPING
        db_session.add(draft)
        db_session.commit()

        assert [i.title for i in _latest_current_issues(db_session)] == ["Leads in a seat"]

class TestRenumberForDisplay:
    def test_preserves_relative_order_when_ranks_already_distinct(self, db_session):
        from app.api.action import _renumber_for_display

        first = _make_issue("2026-08-20", 1, "First", is_current=False)
        second = _make_issue("2026-08-20", 2, "Second", is_current=False)
        db_session.add_all([first, second])
        db_session.commit()

        result = _renumber_for_display([first, second])

        assert [i.title for i in result] == ["First", "Second"]
        assert [i.rank for i in result] == [1, 2]

    def test_breaks_a_rank_tie_by_most_recently_touched_first(self, db_session):
        from datetime import datetime

        from app.api.action import _renumber_for_display

        older = ActionIssue(
            date="2026-08-20", rank=1, title="Older #1", is_current=False,
            created_at=datetime(2026, 8, 20, 8, 0, 0),
        )
        newer = ActionIssue(
            date="2026-08-20", rank=1, title="Newer #1", is_current=False,
            created_at=datetime(2026, 8, 20, 20, 0, 0),
        )
        db_session.add_all([older, newer])
        db_session.commit()

        result = _renumber_for_display([older, newer])

        assert [i.title for i in result] == ["Newer #1", "Older #1"]
        assert [i.rank for i in result] == [1, 2]

    def test_closes_gaps_from_skipped_ranks(self, db_session):
        from app.api.action import _renumber_for_display

        a = _make_issue("2026-08-20", 3, "A", is_current=False)
        b = _make_issue("2026-08-20", 7, "B", is_current=False)
        db_session.add_all([a, b])
        db_session.commit()

        result = _renumber_for_display([a, b])

        assert [i.rank for i in result] == [1, 2]

    def test_does_not_persist_the_renumbering(self, db_session):
        """Purely a display fix — these rows are is_current=False, and the
        real stored rank belongs to whichever run last touched the row,
        not to a read request."""
        from app.api.action import _renumber_for_display

        a = _make_issue("2026-08-20", 1, "A", is_current=False)
        b = _make_issue("2026-08-20", 1, "B", is_current=False)
        db_session.add_all([a, b])
        db_session.commit()

        _renumber_for_display([a, b])
        db_session.rollback()

        db_session.refresh(a)
        db_session.refresh(b)
        assert a.rank == 1
        assert b.rank == 1


class TestRelatedBillInternalLinks:
    """The issues API points related bills at the site's own bill page
    whenever that page can show them: any current-Congress bill, and an
    earlier one we hold from that Congress. Congress.gov is the fallback."""

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

        assert resp["relatedBills"][0]["internalUrl"] == "/congress/bills/HR.22?congress=119"
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

    def test_an_earlier_congress_links_to_that_congress_bill(self, db_session):
        """A bill number alone is ambiguous across congresses — an issue
        entry that recorded a different congress than our hosted record
        links to that Congress's bill, never to ours."""
        from app.api.action import _build_issue_response

        self._host_senate_bill(db_session, "HR.3055", congress=119)
        issue = self._make_issue_with_bill(db_session, {
            "name": "Old appropriations act", "id": "HR.3055",
            "url": "https://www.congress.gov/bill/101st-congress/house-bill/3055",
            "congress": 101,
        })

        resp = _build_issue_response(issue, db_session)

        assert resp["relatedBills"][0]["internalUrl"] == "/congress/bills/HR.3055?congress=101"

    def test_a_congress_that_has_not_convened_is_not_linked(self, db_session):
        from app.api.action import _build_issue_response
        from app.pipeline.fetch.congress import expected_current_congress

        issue = self._make_issue_with_bill(db_session, {
            "name": "A bill", "id": "S.1", "url": "https://www.congress.gov/", "congress": expected_current_congress() + 1,
        })
        assert _build_issue_response(issue, db_session)["relatedBills"][0]["internalUrl"] is None

    def test_legacy_entry_without_congress_still_links(self, db_session):
        """Rows stored before the congress field existed match by id alone."""
        from app.api.action import _build_issue_response

        self._host_senate_bill(db_session, "HR.22", congress=119)
        issue = self._make_issue_with_bill(db_session, {
            "name": "SAVE Act", "id": "HR.22",
            "url": "https://www.congress.gov/bill/119th-congress/house-bill/22",
        })

        resp = _build_issue_response(issue, db_session)

        assert resp["relatedBills"][0]["internalUrl"] == "/congress/bills/HR.22?congress=119"

    def test_any_current_congress_bill_links_to_the_sites_bill_page(self, db_session):
        """The bill page shows any bill of the current Congress (its record
        comes from Congress.gov on demand), sponsored by a current member or
        not."""
        from app.api.action import _build_issue_response
        from app.pipeline.fetch.congress import expected_current_congress

        current = expected_current_congress()
        issue = self._make_issue_with_bill(db_session, {
            "name": "A bill", "id": "S.55",
            "url": f"https://www.congress.gov/bill/{current}th-congress/senate-bill/55",
            "congress": current,
        })

        resp = _build_issue_response(issue, db_session)

        assert resp["relatedBills"][0]["internalUrl"] == f"/congress/bills/S.55?congress={current}"


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
        election = result["nextElection"]
        # Counting down in a campaign; zero or less while the election
        # just held has its results on show (election_phase).
        if election["phase"] == "campaign":
            assert election["daysUntil"] >= 0
        else:
            assert election["daysUntil"] <= 0
        assert result["senateSeatsUp"] > 0

    async def test_get_election_info_in_election_week(self, db_session):
        """Inside the results window the phase lookup must use the
        request's session — its own reached a database with no tables."""
        from datetime import date
        from unittest.mock import patch

        from fastapi import Response

        from app.api.action import get_election_info

        with patch("app.api.action.election_today", return_value=date(2026, 11, 5)), \
                patch("app.election_phase.election_today", return_value=date(2026, 11, 5)), \
                patch("app.database.SessionLocal", side_effect=AssertionError("opened its own session")):
            result = await get_election_info(Response(), db=db_session)
        assert result["nextElection"]["phase"] == "results"
        assert result["nextElection"]["daysUntil"] == -2
        assert result["nextElection"]["isElectionSeason"] is True

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

    async def test_election_night_keeps_election_day_on_the_calendar(self, db_session):
        """9 PM ET on election day is already the 4th in UTC."""
        from datetime import date, datetime
        from unittest.mock import patch

        from fastapi import Response

        from app.api.action import get_timeline

        with patch("app.api.action.utcnow", return_value=datetime(2026, 11, 4, 2)), \
                patch("app.api.action.election_today", return_value=date(2026, 11, 3)):
            result = await get_timeline(Response(), year=2026, db=db_session)
        assert any(e["date"] == "2026-11-03" for e in result["upcomingEvents"])


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

        # get_election_info reads the Eastern election date (election_today),
        # not utcnow: pin that, or the test runs on the real clock.
        from datetime import date

        with patch("app.api.action.utcnow", return_value=datetime(2026, 7, 24)), \
                patch("app.api.action.election_today", return_value=date(2026, 7, 24)), \
                patch("app.election_phase.election_today", return_value=date(2026, 7, 24)):
            result = await get_election_info(Response(), db=db_session)

        assert self._fl_entry(result)["hasSenateRace"] is True
        assert result["senateSeatsUp"] == 34  # 33 Class II + FL special

    async def test_without_special_race_fl_has_no_senate_race(self, db_session):
        from datetime import datetime
        from unittest.mock import patch

        from fastapi import Response

        from app.api.action import get_election_info

        # get_election_info reads the Eastern election date (election_today),
        # not utcnow: pin that, or the test runs on the real clock.
        from datetime import date

        with patch("app.api.action.utcnow", return_value=datetime(2026, 7, 24)), \
                patch("app.api.action.election_today", return_value=date(2026, 7, 24)), \
                patch("app.election_phase.election_today", return_value=date(2026, 7, 24)):
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

    async def test_visits_db_failure_degrades_to_no_trending_instead_of_500ing(self, db_session):
        """/action/issues is one of the most-hit routes on the site;
        get_visits_db has no error handling of its own (its docstring says
        it's built for low-stakes admin endpoints) — a locked or briefly
        unavailable visits DB must not take down issue listing over a
        badge (2026-07 incident: exactly this kind of lock contention has
        happened to this same database before, under track_visit's own
        write load)."""
        from unittest.mock import MagicMock

        from fastapi import Response
        from sqlalchemy.exc import OperationalError

        from app.api.action import get_action_issues

        issue = ActionIssue(date="2026-08-19", rank=1, title="Issue", summary="s")
        db_session.add(issue)
        db_session.commit()

        broken_visits_db = MagicMock()
        broken_visits_db.query.side_effect = OperationalError("stmt", {}, Exception("database is locked"))

        resp = await get_action_issues(
            Response(), date=issue.date, db=db_session, db_visits=broken_visits_db,
        )

        assert resp["issues"][0]["isTrending"] is False


class TestNewFactsField:
    """new_facts (app/fact_diff.py) through the real endpoint — the model/
    pipeline-level snapshot behavior is covered in test_action_center.py's
    TestApplyMatchedIssueUpdate; this pins the read side."""

    async def test_never_updated_issue_has_no_new_facts(self, db_session):
        from fastapi import Response

        from app.api.action import get_action_issue

        issue = ActionIssue(
            date="2026-08-19", rank=1, title="Issue", summary="s",
            facts=json.dumps(["Only fact."]),
        )
        db_session.add(issue)
        db_session.commit()

        resp = await get_action_issue(to_public_id(issue.id), Response(), db=db_session)

        assert resp["newFacts"] == []

    @patch("app.pipeline.analyze.action_center._embed_texts_sim")
    async def test_updated_issue_reports_only_the_added_fact(self, mock_embed, db_session):
        from fastapi import Response

        from app.api.action import get_action_issue

        # "Brand new fact." (remaining) and "Old fact." (previous_facts)
        # are both non-empty, so app.fact_diff.new_facts_since falls
        # through to a real, unmocked embedding call unless mocked here
        # -- this test's intent (the API reports the right fact as new)
        # has nothing to do with real embedding similarity, and letting
        # real model output decide the result is fragile (confirmed
        # flaky under coverage instrumentation, live 2026-09-06).
        # Mocked orthogonal so only the response-shape logic is tested.
        mock_embed.return_value = np.array([[1.0, 0.0], [0.0, 1.0]])

        issue = ActionIssue(
            date="2026-08-19", rank=1, title="Issue", summary="s",
            facts=json.dumps(["Old fact.", "Brand new fact."]),
            previous_facts=json.dumps(["Old fact."]),
        )
        db_session.add(issue)
        db_session.commit()

        resp = await get_action_issue(to_public_id(issue.id), Response(), db=db_session)

        assert resp["newFacts"] == ["Brand new fact."]
        assert resp["facts"] == ["Old fact.", "Brand new fact."]


class TestCacheHeaderMatchesNginx:
    """The browser's own HTTP cache honors Cache-Control independently of
    nginx's proxy_cache — a mismatch between the two just relocates the
    staleness window rather than closing it (2026-08 incident: a browser
    holding a response cached before a deploy added a field crashed the
    whole Action Center). This doesn't read nginx/civitas.conf — it just
    pins that this endpoint's own number is the deliberately-short one, so
    a future change to one side without the other is at least a failing
    test, not a silent drift back to five minutes."""

    async def test_issues_list_cache_header_is_short(self, db_session):
        from fastapi import Response

        from app.api.action import get_action_issues

        resp = Response()
        await get_action_issues(resp, date=None, db=db_session, db_visits=db_session)

        assert resp.headers["Cache-Control"] == "public, max-age=30"

    async def test_single_issue_cache_header_is_short(self, db_session):
        from fastapi import Response

        from app.api.action import get_action_issue

        issue = ActionIssue(date="2026-08-21", rank=1, title="Issue", summary="s")
        db_session.add(issue)
        db_session.commit()

        resp = Response()
        await get_action_issue(to_public_id(issue.id), resp, db=db_session)

        assert resp.headers["Cache-Control"] == "public, max-age=30"


class TestRecentActionIssues:
    """The homepage's "record index" used to call the same is_current-
    filtered query the Action Center page uses, so an issue vanished from
    the homepage's own "recent record" the instant it retired (2026-08-22
    report: "we're saying we're collecting a record but records seem to
    disappear"). This endpoint deliberately ignores is_current.

    Which rows are near-identical duplicates is decided by the hourly
    refresh (action_center.mark_recent_duplicates, covered in
    test_action_center.py); this read path only filters on it, and must
    never load a model.
    """

    @pytest.fixture(autouse=True)
    def _no_model_on_the_read_path(self):
        def refuse(*a, **k):
            raise AssertionError("the recent-issues GET loaded an embedding model")

        with patch("app.pipeline.vector_store.get_similarity_model", side_effect=refuse), \
                patch("app.pipeline.vector_store.get_embedding_model", side_effect=refuse):
            yield

    async def test_leaves_out_rows_marked_duplicates(self, db_session):
        from fastapi import Response

        from app.api.action import get_recent_action_issues

        kept = _make_issue("2026-08-21", 1, "Beef import tariffs", is_current=True)
        db_session.add(kept)
        db_session.flush()
        copy = _make_issue("2026-08-21", 2, "Beef import tariff", is_current=False)
        copy.duplicate_of_id = kept.id
        db_session.add(copy)
        db_session.add(_make_issue("2026-08-20", 1, "Something else", is_current=False))
        db_session.commit()

        result = await get_recent_action_issues(Response(), limit=10, db=db_session)

        assert [i["title"] for i in result["issues"]] == ["Beef import tariffs", "Something else"]

    async def test_includes_retired_issues(self, db_session):
        from fastapi import Response

        from app.api.action import get_recent_action_issues

        db_session.add(_make_issue("2026-08-20", 1, "Retired yesterday", is_current=False))
        db_session.add(_make_issue("2026-08-21", 1, "Live today", is_current=True))
        db_session.commit()

        resp = Response()
        result = await get_recent_action_issues(resp, limit=10, db=db_session)

        assert {i["title"] for i in result["issues"]} == {"Retired yesterday", "Live today"}

    async def test_ordered_by_date_then_rank_newest_first(self, db_session):
        from fastapi import Response

        from app.api.action import get_recent_action_issues

        db_session.add(_make_issue("2026-08-20", 1, "Older day", is_current=False))
        db_session.add(_make_issue("2026-08-21", 2, "Newer day, rank 2", is_current=True))
        db_session.add(_make_issue("2026-08-21", 1, "Newer day, rank 1", is_current=True))
        db_session.commit()

        resp = Response()
        result = await get_recent_action_issues(resp, limit=10, db=db_session)

        assert [i["title"] for i in result["issues"]] == [
            "Newer day, rank 1", "Newer day, rank 2", "Older day",
        ]

    async def test_respects_the_limit(self, db_session):
        from fastapi import Response

        from app.api.action import get_recent_action_issues

        for i in range(5):
            db_session.add(_make_issue("2026-08-21", i + 1, f"Issue {i}", is_current=True))
        db_session.commit()

        resp = Response()
        result = await get_recent_action_issues(resp, limit=2, db=db_session)

        assert len(result["issues"]) == 2

    async def test_cache_header_matches_the_rest_of_the_endpoint_family(self, db_session):
        from fastapi import Response

        from app.api.action import get_recent_action_issues

        resp = Response()
        await get_recent_action_issues(resp, limit=10, db=db_session)

        assert resp.headers["Cache-Control"] == "public, max-age=30"

    def test_recent_route_is_declared_before_the_catch_all_issue_id_route(self):
        # FastAPI matches path routes in declaration order — /issues/{issue_id}
        # would swallow "recent" as a path parameter if it were declared first.
        from app.api.action import router

        paths = [r.path for r in router.routes if getattr(r, "path", None) in (
            "/action/issues/recent", "/action/issues/{issue_id}",
        )]
        assert paths == ["/action/issues/recent", "/action/issues/{issue_id}"]


def test_a_developing_issue_names_what_it_was_drafted_from(db_session):
    """The page's disclosure words the source ("a Federal Register rule",
    "the state's own election-night count") instead of calling every
    developing issue a vote record."""
    from app.api.action import _build_issue_response
    from app.models import ActionIssue

    issue = ActionIssue(date="2026-11-04", rank=999, title="t", status="developing", source_type="election_results")
    db_session.add(issue)
    db_session.flush()
    resp = _build_issue_response(issue, db_session)
    assert resp["status"] == "developing" and resp["sourceType"] == "election_results"


class TestElectionNightPager:
    """A seat flip is restamped with the Eastern date on every five-minute
    count sync, so past midnight it carries a day no confirmed issue has
    reached yet. The pager must not offer that day as a view of its own."""

    def _seed(self, db_session):
        confirmed = ActionIssue(date="2026-11-03", rank=1, title="Polls close across the East", is_current=True,
                                source_type="rss")
        flip = ActionIssue(date="2026-11-04", rank=999, title="Republican leads Georgia's 2nd", is_current=True,
                           source_type="election_results", status=ActionIssueStatus.DEVELOPING)
        db_session.add_all([confirmed, flip])
        db_session.commit()

    async def test_the_landing_day_is_the_newest_day_the_pager_offers(self, db_session):
        from fastapi import Response

        from app.api.action import get_action_issues

        self._seed(db_session)
        resp = await get_action_issues(Response(), date=None, db=db_session, db_visits=db_session)
        assert resp["date"] == "2026-11-03"
        assert resp["availableDates"][0] == "2026-11-03"
        assert {i["title"] for i in resp["issues"]} == {"Polls close across the East", "Republican leads Georgia's 2nd"}

    async def test_asking_for_the_newest_day_by_date_shows_the_same_view(self, db_session):
        from fastapi import Response

        from app.api.action import get_action_issues

        self._seed(db_session)
        by_date = await get_action_issues(Response(), date="2026-11-03", db=db_session, db_visits=db_session)
        assert {i["title"] for i in by_date["issues"]} == {"Polls close across the East", "Republican leads Georgia's 2nd"}


class TestPagerAroundAnOlderDay:
    async def test_an_older_deep_link_pages_to_its_neighbours(self, db_session):
        """The timeline's year-in-review links open a day older than the 14
        newest: the pager still lists it and the days either side."""
        from datetime import date, timedelta

        from fastapi import Response

        from app.api.action import get_action_issues

        days = [(date(2026, 10, 1) + timedelta(days=i)).isoformat() for i in range(20)]
        for i, d in enumerate(days):
            db_session.add(ActionIssue(date=d, rank=1, title=f"Issue {i}", is_current=(d == days[-1])))
        db_session.commit()

        resp = await get_action_issues(Response(), date="2026-10-02", db=db_session, db_visits=db_session)
        dates = resp["availableDates"]
        assert resp["date"] == "2026-10-02"
        i = dates.index("2026-10-02")
        assert dates[i + 1] == "2026-10-01" and dates[i - 1] == "2026-10-03"
        assert dates[0] == days[-1]


class TestEmptyDayPager:
    async def test_a_day_with_no_issues_left_still_pages_to_its_neighbours(self, db_session):
        """A re-matched issue is restamped to the day that matched it, so a
        day can end up with no rows; the timeline still links to it."""
        from fastapi import Response

        from app.api.action import get_action_issues

        db_session.add(ActionIssue(date="2026-10-01", rank=1, title="Old", is_current=False))
        db_session.add(ActionIssue(date="2026-10-03", rank=1, title="Moved on", is_current=True))
        db_session.commit()
        resp = await get_action_issues(Response(), date="2026-10-02", db=db_session, db_visits=db_session)
        assert resp["issues"] == []
        assert resp["availableDates"] == ["2026-10-03", "2026-10-02", "2026-10-01"]

    async def test_a_malformed_date_is_not_offered_as_a_day(self, db_session):
        from fastapi import Response

        from app.api.action import get_action_issues

        resp = await get_action_issues(Response(), date="not-a-day", db=db_session, db_visits=db_session)
        assert resp == {"date": "not-a-day", "issues": [], "availableDates": []}


class TestMyRepsIssueDay:
    async def test_evening_eastern_still_finds_todays_issues(self, db_session):
        """My Reps used utcnow()'s date: from 8 PM Eastern (the next UTC
        day) until the next refresh it found no issues at all. It reads the
        Action Center's landing set, which the page intersects it with."""
        from unittest.mock import patch
        from datetime import datetime

        from fastapi import Response

        from app.api.action import get_my_reps

        db_session.add(Senator(id="s1", name="Sen. Alpha", state="CA", party="D", is_current=True))
        db_session.add(ActionIssue(date="2026-10-14", rank=1, title="Water bill", is_current=True,
                                   related_senators='[{"id": "s1"}]'))
        db_session.commit()
        with patch("app.api.action.utcnow", return_value=datetime(2026, 10, 15, 1, 30)):
            resp = await get_my_reps(Response(), state="CA", db=db_session)
        assert resp["issueDate"] == "2026-10-14"
        assert [i["title"] for i in resp["senators"][0]["connectedIssues"]] == ["Water bill"]

    async def test_after_midnight_on_election_night_it_is_the_landing_day(self, db_session):
        from fastapi import Response

        from app.api.action import get_my_reps

        db_session.add(Senator(id="s1", name="Sen. Alpha", state="CA", party="D", is_current=True))
        db_session.add(ActionIssue(date="2026-11-03", rank=1, title="Polls close", is_current=True,
                                   related_senators='[{"id": "s1"}]'))
        db_session.add(ActionIssue(date="2026-11-04", rank=999, title="Republican leads", is_current=True,
                                   source_type="election_results", status=ActionIssueStatus.DEVELOPING))
        db_session.commit()
        resp = await get_my_reps(Response(), state="CA", db=db_session)
        assert resp["issueDate"] == "2026-11-03"


class TestRecentFeedAndSeatFlips:
    """The refresh's duplicate pass reads every seat flip in a state as one
    story (their titles differ only by the district, their source is the
    state's results page); the homepage keeps each race's own."""

    async def test_a_flip_is_never_hidden_behind_another_races_flip(self, db_session):
        from fastapi import Response

        from app.api.action import get_recent_action_issues

        ga2 = ActionIssue(date="2026-11-04", rank=999, title="Republican leads Georgia's 2nd", is_current=True,
                          source_type="election_results")
        db_session.add(ga2)
        db_session.flush()
        ga6 = ActionIssue(date="2026-11-04", rank=999, title="Republican leads Georgia's 6th", is_current=True,
                          source_type="election_results", duplicate_of_id=ga2.id)
        news = ActionIssue(date="2026-11-04", rank=1, title="Republican flips Georgia's 2nd", is_current=True,
                           source_type="rss")
        db_session.add_all([ga6, news])
        db_session.flush()
        copy = ActionIssue(date="2026-11-04", rank=2, title="Copy of the news story", is_current=True,
                           source_type="rss", duplicate_of_id=news.id)
        db_session.add(copy)
        db_session.commit()

        titles = {i["title"] for i in (await get_recent_action_issues(Response(), limit=10, db=db_session))["issues"]}
        assert "Republican leads Georgia's 6th" in titles
        assert "Copy of the news story" not in titles

    async def test_a_flip_still_gives_way_to_a_news_story_of_the_same_flip(self, db_session):
        from fastapi import Response

        from app.api.action import get_recent_action_issues

        news = ActionIssue(date="2026-11-04", rank=1, title="Republican flips Georgia's 2nd", is_current=True,
                           source_type="rss")
        db_session.add(news)
        db_session.flush()
        db_session.add(ActionIssue(date="2026-11-04", rank=999, title="Republican leads Georgia's 2nd",
                                   is_current=True, source_type="election_results", duplicate_of_id=news.id))
        db_session.commit()

        titles = {i["title"] for i in (await get_recent_action_issues(Response(), limit=10, db=db_session))["issues"]}
        assert titles == {"Republican flips Georgia's 2nd"}


class TestCountIssuePayload:
    def test_a_count_issue_says_when_its_figures_were_read_and_whether_official(self, db_session):
        from datetime import datetime

        from app.api.action import _build_issue_response

        issue = ActionIssue(date="2026-11-04", rank=999, title="Republican leads", is_current=True,
                            source_type="election_results", status=ActionIssueStatus.DEVELOPING,
                            count_as_of=datetime(2026, 11, 4, 2, 44), count_official=True)
        news = ActionIssue(date="2026-11-04", rank=1, title="News", is_current=True)
        db_session.add_all([issue, news])
        db_session.commit()

        data = _build_issue_response(issue, db_session)
        assert data["countAsOf"] == "2026-11-04T02:44:00Z"
        assert data["countOfficial"] is True
        other = _build_issue_response(news, db_session)
        assert other["countAsOf"] is None and other["countOfficial"] is None


def test_timeline_refuses_a_year_it_cannot_build_dates_for(db_session):
    # year=0 reached date(0, 10, 1) and answered 500; out-of-range input is a 422.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import action
    from app.database import get_db

    app = FastAPI()
    app.include_router(action.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    assert client.get("/api/action/timeline?year=0").status_code == 422
    assert client.get("/api/action/timeline?year=2026").status_code == 200
