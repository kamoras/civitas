"""The homepage's front page (api/notable.py): its candidates (the closest
vote and the lone party breaks of Congress's latest week of record votes,
laws, confirmations, comment periods, the news) and the fixed rule that
lays them out as a lead, a tier of two and three columns."""

import json
from datetime import date, datetime

from fastapi.testclient import TestClient

from app.api import notable as nt
from app.database import get_db
from app.main import app
from app.models import (
    ActionIssue, CongressDay, ExploreDocument, FeedArticle, NationalMonitor, Nomination, RollCall, RollCallPosition,
    Senator,
)

_NUMBER = iter(range(1, 10_000))
_DOC = iter(range(1, 10_000))
TODAY = date(2026, 10, 20)
PAPERWORK = "Agency Information Collection Activities; Proposals, Submissions, and Approvals"


def _rc(db, day, yeas, nays, *, chamber="senate", question="On Passage", requirement="1/2", rejected=False):
    rc = RollCall(chamber=chamber, congress=119, session=2, number=next(_NUMBER), date=day, question=question,
                  title="A bill", result="Rejected" if rejected else "Passed", rejected=rejected,
                  majority_requirement=requirement, yeas=yeas, nays=nays, bill_id="S.1")
    db.add(rc)
    db.flush()
    return rc


def _entries(db, today):
    """The closest vote, then the lone breaks, of the week shown on `today`."""
    _, votes = nt._week_votes(db, today)
    closest = nt._closest([v for v in votes if not nt.is_housekeeping(v.question, v.chamber)])
    return ([closest] if closest else []) + nt._lone_breaks(db, votes)


def _breaks(db, today):
    return [e for e in _entries(db, today) if e["kind"] == "loneBreak"]


def _party_votes(db, rc, party, yea, nay, *, prefix=None, lone=None):
    """`yea` and `nay` members of `party`; `lone` = (last name, state, side)
    for a named member added on that side."""
    prefix = prefix or party
    for i in range(yea):
        db.add(RollCallPosition(roll_call_id=rc.id, member_id=f"{prefix}y{i}", last_name=f"{prefix}Y{i}",
                                party=party, state="ZZ", position="Yea"))
    for i in range(nay):
        db.add(RollCallPosition(roll_call_id=rc.id, member_id=f"{prefix}n{i}", last_name=f"{prefix}N{i}",
                                party=party, state="ZZ", position="Nay"))
    if lone:
        last, state, side = lone
        db.add(RollCallPosition(roll_call_id=rc.id, member_id=f"L-{last}", last_name=last, first_name="Pat",
                                party=party, state=state, position=side))


def _senators(db):
    db.add_all([
        Senator(id="pat-lone", name="Pat Lone", state="AA", party="R", is_current=True),
        Senator(id="sam-other", name="Sam Other", state="AA", party="D", is_current=True),
        Senator(id="pat-dee", name="Pat Dee", state="BB", party="D", is_current=True),
        Senator(id="lee-leader", name="Lee Leader", state="CC", party="R", is_current=True,
                leadership_title="Senate Majority Leader"),
    ])


def _issue(db, day, rank, title, *, outlets=("Outlet A",), monitors=()):
    issue = ActionIssue(date=day, rank=rank, title=title, is_current=True, source_names=json.dumps(list(outlets)),
                        source_urls=json.dumps(["https://news.example/a"]),
                        related_monitor_slugs=json.dumps(list(monitors)))
    db.add(issue)
    db.flush()
    return issue


def _comment(db, closes, *, title="A proposed rule", doc_type="Proposed Rule", views=0, toc=None):
    n = next(_DOC)
    db.add(ExploreDocument(external_id=f"fr-reg-2026-{n:05d}", doc_type=doc_type, title=title,
                           agency_name="An Agency", date="2026-09-01", comments_close_on=closes,
                           comment_url=f"https://www.regulations.gov/commenton/X-{n}", fr_page_views=views,
                           fr_toc_subject=toc, source="federal_register", summary=""))


class TestClosest:
    def test_smallest_gap_among_simple_majority_questions(self, db_session):
        _rc(db_session, "2026-09-28", 60, 38)
        close = _rc(db_session, "2026-09-29", 49, 50, rejected=True)
        # A 3/5 question is left out: its distance from passing is the threshold's.
        _rc(db_session, "2026-09-30", 50, 50, requirement="3/5")
        db_session.commit()
        assert nt._week_votes(db_session, date(2026, 10, 2))[0] == date(2026, 9, 28)
        first = _entries(db_session, date(2026, 10, 2))[0]
        assert first["kind"] == "closest" and first["close"]
        assert first["vote"]["number"] == close.number
        assert first["headline"] == "Decided by 1 vote in the Senate, 49–50: Rejected."
        assert first["figure"] == {"type": "split", "yeas": 49, "nays": 50, "yeaShare": 0.4949,
                                   "caption": "Decided by 1 vote"}

    def test_a_tie_and_the_newest_of_equal_gaps(self, db_session):
        _rc(db_session, "2026-09-28", 48, 48)
        newer = _rc(db_session, "2026-09-30", 47, 47)
        db_session.commit()
        first = _entries(db_session, date(2026, 10, 2))[0]
        assert first["vote"]["number"] == newer.number
        assert first["headline"] == "Tied 47–47 in the Senate: Passed."

    def test_a_wide_margin_is_not_close(self, db_session):
        _rc(db_session, "2026-09-28", 60, 38)
        db_session.commit()
        assert not _entries(db_session, date(2026, 10, 2))[0]["close"]

    def test_housekeeping_questions_are_not_votes_on_anything(self, db_session):
        _rc(db_session, "2026-09-29", 50, 49, question="On the Motion to Table")
        db_session.commit()
        assert _entries(db_session, date(2026, 10, 2)) == []


class TestLoneBreak:
    def test_one_per_party_newest_first_with_a_same_state_comparison(self, db_session):
        _senators(db_session)
        older = _rc(db_session, "2026-09-28", 52, 47)
        _party_votes(db_session, older, "R", 0, 51, lone=("Lone", "AA", "Yea"))
        newer = _rc(db_session, "2026-09-30", 52, 47)
        _party_votes(db_session, newer, "R", 0, 40, lone=("Lone", "AA", "Yea"))
        _party_votes(db_session, newer, "D", 30, 0, lone=("Dee", "BB", "Nay"))
        db_session.commit()
        breaks = _breaks(db_session, date(2026, 10, 2))
        assert [(e["party"], e["vote"]["number"]) for e in breaks] == [("D", newer.number), ("R", newer.number)]
        r = breaks[1]
        assert r["headline"] == "The only Republican to vote Yea: Pat Lone (R-AA), against 40 Republicans voting Nay."
        assert r["memberId"] == "pat-lone"
        assert (r["compareWithId"], r["compareWithName"]) == ("sam-other", "Sam Other")
        assert r["figure"] == {"type": "dots", "party": "R", "voting": 41,
                               "caption": "1 of 41 Republicans voted Yea"}
        # BB has one senator on file: nobody to compare with.
        assert breaks[0]["compareWithId"] is None

    def test_too_few_voting_is_not_a_lone_break(self, db_session):
        _senators(db_session)
        rc = _rc(db_session, "2026-09-30", 10, 10)
        _party_votes(db_session, rc, "R", 0, nt.LONE_BREAK_MIN_VOTING - 2, lone=("Lone", "AA", "Yea"))
        db_session.commit()
        assert _breaks(db_session, date(2026, 10, 2)) == []

    def test_the_majority_leaders_reconsider_switch_is_not_a_break(self, db_session):
        _senators(db_session)
        rc = _rc(db_session, "2026-09-30", 48, 51, rejected=True)
        # The party voted Yea on a question that failed; the leader moved to
        # the prevailing Nay side to be able to move to reconsider.
        _party_votes(db_session, rc, "R", 48, 0, lone=("Leader", "CC", "Nay"))
        db_session.commit()
        assert _breaks(db_session, date(2026, 10, 2)) == []


class TestWindow:
    def test_the_last_week_in_session_shows_through_a_short_recess(self, db_session):
        _rc(db_session, "2026-09-30", 49, 50)
        db_session.commit()
        week, _ = nt._week_votes(db_session, date(2026, 10, 27))
        assert week == date(2026, 9, 28) and len(_entries(db_session, date(2026, 10, 27))) == 1

    def test_a_long_recess_shows_none_of_it(self, db_session):
        _rc(db_session, "2026-09-30", 49, 50)
        db_session.commit()
        assert nt._week_votes(db_session, date(2026, 10, 29)) == (None, [])


def _e(kind, column, day, *, close=False, figure=True):
    return {"kind": kind, "column": column, "date": day, "close": close,
            "figure": {"type": "stat"} if figure else None, "headline": f"{kind} {day}"}


class TestRule:
    """The lead's priority order, the tier from distinct columns, no repeats."""

    def everything(self):
        congress = [_e("closest", "congress", "2026-10-16", close=True), _e("law", "congress", "2026-10-15"),
                    _e("confirmed", "congress", "2026-10-14"), _e("loneBreak", "congress", "2026-10-16")]
        agencies = [_e("comment", "agencies", "2026-10-21"), _e("comment", "agencies", "2026-11-01")]
        news = [_e("issue", "news", "2026-10-20"), _e("issue", "news", "2026-10-19")]
        return congress, agencies, news

    def test_a_close_recent_vote_leads(self):
        page = nt.arrange(*self.everything(), TODAY)
        assert page["lead"]["kind"] == "closest"
        assert [e["kind"] for e in page["tier"]] == ["law", "comment"]

    def test_each_fallback_in_order(self):
        congress, agencies, news = self.everything()
        congress[0]["close"] = False  # a vote decided by a wide margin doesn't lead
        assert nt.arrange(congress, agencies, news, TODAY)["lead"]["kind"] == "law"
        congress[1]["date"] = "2026-10-01"  # a law older than the lead window doesn't
        assert nt.arrange(congress, agencies, news, TODAY)["lead"]["kind"] == "confirmed"
        congress[2]["date"] = "2026-10-01"
        assert nt.arrange(congress, agencies, news, TODAY)["lead"]["kind"] == "loneBreak"
        congress[3]["date"] = "2026-10-01"
        assert nt.arrange(congress, agencies, news, TODAY)["lead"]["headline"] == "issue 2026-10-20"
        assert nt.arrange(congress, agencies, [], TODAY)["lead"]["kind"] == "comment"
        assert nt.arrange([], [], [], TODAY) == {
            "lead": None, "tier": [], "columns": {"congress": [], "agencies": [], "news": []}}

    def test_no_entry_appears_twice(self):
        page = nt.arrange(*self.everything(), TODAY)
        shown = [page["lead"], *page["tier"], *(e for col in page["columns"].values() for e in col)]
        assert len({id(e) for e in shown}) == len(shown) == 8

    def test_the_tier_skips_entries_without_a_figure_and_never_takes_one_column_twice(self):
        congress, _, news = self.everything()
        congress[1]["figure"] = None
        page = nt.arrange(congress, [], news, TODAY)
        assert [e["kind"] for e in page["tier"]] == ["confirmed", "issue"]
        assert [e["kind"] for e in page["columns"]["congress"]] == ["loneBreak", "law"]

    def test_a_column_takes_one_of_each_kind_before_a_second(self):
        laws = [_e("law", "congress", f"2026-10-1{d}") for d in (5, 4, 3, 2, 1)]
        lone = _e("loneBreak", "congress", "2026-10-01")
        page = nt.arrange(laws + [lone], [], [], TODAY)
        assert page["lead"] is laws[0] and page["tier"] == [laws[1]]
        assert [e["kind"] for e in page["columns"]["congress"]] == ["law", "law", "loneBreak"]
        assert laws[4] not in page["columns"]["congress"]

    def test_a_close_vote_weeks_old_is_dated_in_the_column_not_the_lead(self):
        congress, agencies, news = self.everything()
        old = dict(congress[0], date="2026-09-30")
        page = nt.arrange([old], agencies, news, TODAY)
        assert page["lead"]["kind"] == "issue"
        assert page["tier"][0] is old


class TestCandidates:
    def test_the_rule_closing_soonest_then_the_most_salient_and_no_paperwork(self, db_session):
        _comment(db_session, "2026-10-30", title="Later rule", views=900)
        _comment(db_session, "2026-10-21", title="Soonest rule")
        _comment(db_session, "2026-10-22", title="A notice", doc_type="Notice", views=50)
        _comment(db_session, "2026-10-20", title="A collection", doc_type="Notice", toc=PAPERWORK)
        _comment(db_session, "2026-10-19", title="Closed yesterday")
        db_session.commit()
        got = nt._comments(db_session, TODAY)
        assert [e["headline"] for e in got] == ["Soonest rule", "Later rule", "A notice"]
        assert got[0]["figure"]["daysLeft"] == 1 and got[0]["key"].startswith("fr-")

    def test_a_story_folds_its_other_days_under_the_newest_with_the_outlets_own_headline(self, db_session):
        db_session.add(NationalMonitor(slug="a-story", title="A story", description="", status="active"))
        _issue(db_session, "2026-10-20", 1, "Day two of a story", outlets=("Outlet A", "Outlet B"),
               monitors=("a-story",))
        _issue(db_session, "2026-10-19", 1, "Day one of a story", monitors=("a-story",))
        _issue(db_session, "2026-10-20", 2, "Another story")
        db_session.add(FeedArticle(url="https://news.example/a", source_name="Outlet B", text="",
                                   title="Day two of a story", embedding=b"", model="m"))
        db_session.commit()
        got = nt._news(db_session)
        assert [e["headline"] for e in got] == ["Day two of a story", "Another story"]
        first = got[0]
        assert first["outlet"] == "Outlet B" and first["alsoDates"] == ["2026-10-19"]
        assert first["monitor"] == {"slug": "a-story", "title": "A story"}
        assert first["figure"]["value"] == 2 and got[1]["monitor"] is None

    def test_a_confirmation_with_its_vote(self, db_session):
        db_session.add(Nomination(congress=119, citation="PN9-1", number=9, status="confirmed",
                                  status_date="2026-10-16", received_date="2026-06-01",
                                  position_title="Judge", organization="The Judiciary",
                                  nominees=json.dumps([{"names": [{"firstName": "Jane", "lastName": "Doe",
                                                                   "state": "ZZ"}]}])))
        _rc(db_session, "2026-10-16", 51, 47, question="On the Nomination PN9-1")
        db_session.commit()
        got = nt._confirmations(db_session, TODAY)
        assert got[0]["headline"].startswith("Confirmed by the Senate: Jane")
        assert got[0]["figure"]["type"] == "split" and got[0]["figure"]["caption"] == "Confirmed 51–47"


class TestFrontPage:
    def test_a_recess_with_no_votes_falls_back_and_says_congress_is_out(self, db_session, freeze_utcnow):
        freeze_utcnow(datetime(2026, 10, 20, 16))
        _rc(db_session, "2026-08-20", 49, 50)  # past the stale bound
        db_session.add(CongressDay(chamber="senate", date="2026-10-19", in_session=True, is_final=True,
                                   source="digest", adjournment_text="The Senate met in pro forma session.",
                                   next_meeting="3:30 p.m., Thursday, October 22",
                                   next_program="Program for Thursday: Senate will meet in a pro forma session."))
        _issue(db_session, "2026-10-20", 1, "The day's top story")
        _comment(db_session, "2026-10-21")
        db_session.commit()
        page = nt.front_page(db_session, TODAY)
        assert page["lead"]["headline"] == "The day's top story"
        assert [e["kind"] for e in page["tier"]] == ["comment"]
        assert page["dateline"][:2] == ["Tuesday, October 20",
                                        "Congress is out; the Senate next sits Thursday, October 22, pro forma"]
        assert page["voteWeek"] is None and page["columns"]["congress"] == []

    def test_nothing_on_record_is_no_front_page(self, db_session):
        assert nt.front_page(db_session, TODAY)["lead"] is None


def test_the_endpoint_reads_the_eastern_clock(db_session, freeze_utcnow):
    _rc(db_session, "2026-10-14", 49, 50)
    db_session.commit()
    # 03:00 UTC on Oct 22 is still Oct 21 in the East: within the lead window.
    freeze_utcnow(datetime(2026, 10, 22, 3))
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        r = TestClient(app).get("/api/front-page")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.headers["cache-control"].startswith("public, max-age=300")
    body = r.json()
    assert body["asOf"] == "2026-10-21" and body["lead"]["kind"] == "closest"
