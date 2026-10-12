"""The "this week" strip's API: Election Day, Congress this week, and the
next comment deadlines worth a reader's time."""

import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.api import this_week as tw
from app.database import get_db
from app.issue_ids import to_public_id
from app.main import app
from app.models import ActionIssue, CongressDay, ExploreDocument, RollCall
from app.services.congress_service import meeting_date


class TestMeetingDate:
    def test_the_digest_wording(self):
        assert meeting_date("3:30 p.m., Tuesday, October 13", date(2026, 10, 9)) == date(2026, 10, 13)

    def test_the_floor_log_wording_with_its_year(self):
        text = "The Senate is scheduled to reconvene at 3:30 p.m. Tuesday, October 13, 2026."
        assert meeting_date(text, date(2026, 10, 10)) == date(2026, 10, 13)

    def test_a_december_notice_naming_january(self):
        assert meeting_date("12 noon, Monday, January 4", date(2026, 12, 18)) == date(2027, 1, 4)

    def test_no_date(self):
        assert meeting_date("Subject to the call of the Chair", date(2026, 10, 9)) is None
        assert meeting_date(None, date(2026, 10, 9)) is None


def _day(chamber, day, in_session=True, next_meeting=None):
    return CongressDay(chamber=chamber, date=day, in_session=in_session, is_final=True, next_meeting=next_meeting,
                       source="digest")


def _vote(chamber, day, number):
    return RollCall(chamber=chamber, congress=119, session=2, number=number, date=day, question="On Passage",
                    title="A bill", result="Passed", yeas=60, nays=40, bill_id="HR.1")


class TestCongressWeek:
    def test_a_recess_reads_out_until(self, db_session):
        # Friday the 9th: both met; the week of the 12th starts empty.
        db_session.add_all([_day("senate", "2026-10-09", next_meeting="3:30 p.m., Tuesday, October 20"),
                            _day("house", "2026-10-09", next_meeting="1:30 p.m., Tuesday, October 20")])
        db_session.commit()
        r = tw.congress_week(db_session, date(2026, 10, 13))
        assert r["sentence"] == "Congress is out until Tuesday, October 20."
        assert r["chambers"]["senate"]["nextMeeting"] == "2026-10-20"
        assert r["weekStart"] == "2026-10-12"

    def test_a_session_week(self, db_session):
        db_session.add_all([
            _day("senate", "2026-10-13", next_meeting="10 a.m., Wednesday, October 14"),
            _day("house", "2026-10-12", next_meeting="1:30 p.m., Friday, October 16"),
            _vote("senate", "2026-10-13", 301), _vote("senate", "2026-10-14", 302), _vote("house", "2026-10-12", 500),
        ])
        db_session.commit()
        r = tw.congress_week(db_session, date(2026, 10, 14))
        senate, house = r["chambers"]["senate"], r["chambers"]["house"]
        assert senate["inSessionToday"] and senate["recordVotesThisWeek"] == 2
        assert not house["inSessionToday"] and house["daysInSessionThisWeek"] == 1
        assert r["sentence"] == "The Senate is in session today. The House next meets Friday, October 16."
        assert [v["number"] for v in r["recentVotes"]] == [302, 301, 500]
        assert r["recentVotes"][0]["billLabel"] == "H.R. 1"

    def test_both_chambers_back_the_same_day(self, db_session):
        db_session.add_all([_day("senate", "2026-10-09", next_meeting="3:30 p.m., Tuesday, October 13"),
                            _day("house", "2026-10-09", next_meeting="1:30 p.m., Tuesday, October 13")])
        db_session.commit()
        r = tw.congress_week(db_session, date(2026, 10, 11))
        assert r["sentence"] == "Congress next meets Tuesday, October 13."

    def test_a_passed_notice_is_not_a_next_meeting(self, db_session):
        db_session.add(_day("house", "2026-10-01", next_meeting="Friday, October 2"))
        db_session.commit()
        r = tw.congress_week(db_session, date(2026, 10, 14))
        assert r["chambers"]["house"]["nextMeeting"] is None
        assert "The House's next meeting is not on record yet." in r["sentence"]

    def test_a_meeting_later_today(self, db_session):
        db_session.add(_day("senate", "2026-10-09", next_meeting="3 p.m., Tuesday, October 13"))
        db_session.commit()
        assert tw.congress_week(db_session, date(2026, 10, 13))["sentence"].startswith("The Senate meets today.")


def _rule(id_, title, closes, doc_type="Proposed Rule", rins=(), cited=0):
    return ExploreDocument(
        id=id_, doc_type=doc_type, source="federal_register", title=title, date="2026-10-01",
        comment_url=f"https://www.regulations.gov/commenton/{id_}", comments_close_on=closes,
        identifiers=json.dumps([f"rin:{r}" for r in rins]), cited_by_count=cited,
    )


class TestCommentDeadlines:
    @pytest.fixture(autouse=True)
    def today(self, monkeypatch):
        monkeypatch.setattr(tw, "comment_period_today", lambda: "2026-10-11")

    def test_order_and_what_is_left_out(self, db_session):
        series = [_rule(10 + i, f"Templated series {i}", "2026-10-12", rins=["2120-AA64"], cited=300)
                  for i in range(3)]
        db_session.add_all([
            *series,
            _rule(1, "Uncited rule", "2026-10-13"),
            _rule(2, "Cited rule", "2026-11-30", cited=2, rins=["0000-AA01"]),
            _rule(3, "Rule in the news", "2026-12-01", doc_type="Final Rule"),
            _rule(4, "A notice", "2026-10-12", doc_type="Notice"),
            _rule(5, "Closed rule", "2026-10-10", cited=5),
            _rule(6, "Proposed rule and its correction", "2026-12-15", rins=["0000-AA02"]),
            _rule(7, "Correction", "2026-12-16", rins=["0000-AA02"]),
        ])
        db_session.add(ActionIssue(id=42, date="2026-10-08", rank=1, title="An issue", related_explore_ids="[3]"))
        db_session.commit()
        r = tw.comment_deadlines(db_session)
        assert [d["title"] for d in r] == ["Rule in the news", "Cited rule", "Uncited rule"]
        assert r[0]["issueId"] == to_public_id(42) and r[0]["daysLeft"] == 51
        assert r[1]["citedBy"] == 2 and r[1]["issueId"] is None
        assert r[2]["citedBy"] == 0

    def test_a_series_in_the_news_is_kept(self, db_session):
        db_session.add_all([_rule(10 + i, f"Series {i}", "2026-10-20", rins=["2120-AA64"]) for i in range(3)])
        db_session.add(ActionIssue(id=43, date="2026-10-10", rank=1, title="An issue", related_explore_ids="[11]"))
        db_session.commit()
        assert [d["id"] for d in tw.comment_deadlines(db_session)] == [11]

    def test_an_old_issue_is_not_news(self, db_session):
        db_session.add(_rule(3, "Rule", "2026-12-01"))
        db_session.add(ActionIssue(id=44, date="2026-09-01", rank=1, title="Old", related_explore_ids="[3]"))
        db_session.commit()
        assert tw.comment_deadlines(db_session)[0]["issueId"] is None


def test_the_endpoint(db_session, freeze_utcnow, monkeypatch):
    freeze_utcnow(datetime(2026, 10, 11, 16))
    monkeypatch.setattr(tw, "comment_period_today", lambda: "2026-10-11")
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        r = TestClient(app).get("/api/this-week")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    body = r.json()
    assert body["election"]["date"] == "2026-11-03"
    assert body["election"]["daysUntil"] == 23
    assert body["election"]["phase"] == "campaign"
    assert "DC" in body["election"]["states"] and len(body["election"]["states"]) == 51
    assert body["commentDeadlines"] == []
    assert body["congress"]["sentence"] == (
        "The Senate's next meeting is not on record yet. The House's next meeting is not on record yet."
    )
    assert "max-age=300" in r.headers["cache-control"]
