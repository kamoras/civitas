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
    """The closest vote, then the lone breaks, of the latest week of business."""
    votes = nt._week_votes(db, nt._business_week(db, today))
    closest = nt._closest(votes, nt.median_margin(db, 119))
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
        assert nt._business_week(db_session, date(2026, 10, 2)) == (date(2026, 9, 28), date(2026, 10, 4))
        first = _entries(db_session, date(2026, 10, 2))[0]
        # Margins 22 and 1: the median is 11.5, and 1 is under it.
        assert first["kind"] == "closest" and first["close"]
        assert first["vote"]["number"] == close.number
        assert first["headline"] == "Decided by 1 vote in the Senate, 49–50: Rejected."
        assert first["figure"] == {"type": "split", "yeas": 49, "nays": 50, "yeaShare": 0.4949,
                                   "caption": "Decided by 1 vote", "marks": [
            {"id": "yea", "label": "Yea", "detail": None, "shown": "49 votes",
             "context": "49% of the 99 voting", "href": f"/congress/votes/senate/119/2/{close.number}#members"},
            {"id": "nay", "label": "Nay", "detail": None, "shown": "50 votes",
             "context": "51% of the 99 voting", "href": f"/congress/votes/senate/119/2/{close.number}#members"}]}

    def test_a_tie_and_the_newest_of_equal_gaps(self, db_session):
        _rc(db_session, "2026-09-28", 48, 48)
        newer = _rc(db_session, "2026-09-30", 47, 47)
        db_session.commit()
        first = _entries(db_session, date(2026, 10, 2))[0]
        assert first["vote"]["number"] == newer.number
        assert first["headline"] == "Tied 47–47 in the Senate: Passed."

    def test_close_means_under_this_congresss_median_margin(self, db_session):
        # Earlier weeks of the Congress: margins 2, 10, 30, 40.
        for day, (y, n) in zip(("2026-07-07", "2026-07-08", "2026-07-14", "2026-07-15"),
                               ((51, 49), (55, 45), (65, 35), (70, 30))):
            _rc(db_session, day, y, n)
        # A housekeeping question and a 3/5 question don't count toward it.
        _rc(db_session, "2026-07-16", 50, 50, question="On the Motion to Table")
        _rc(db_session, "2026-07-16", 50, 50, requirement="3/5")
        week = _rc(db_session, "2026-09-29", 60, 40)  # margin 20: the median of 2, 10, 20, 30, 40
        db_session.commit()
        assert nt.median_margin(db_session, 119) == 20
        first = _entries(db_session, date(2026, 10, 2))[0]
        assert first["vote"]["number"] == week.number and not first["close"]
        week.yeas, week.nays = 55, 45  # margin 10: median of 2, 10, 10, 30, 40 is 10, not under it
        db_session.commit()
        assert not _entries(db_session, date(2026, 10, 2))[0]["close"]
        week.yeas, week.nays = 52, 47  # margin 5: median of 2, 5, 10, 30, 40 is 10
        db_session.commit()
        assert _entries(db_session, date(2026, 10, 2))[0]["close"]

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
        marks = r["figure"].pop("marks")
        assert r["figure"] == {"type": "dots", "party": "R", "voting": 41,
                               "caption": "1 of 41 Republicans voted Yea"}
        # One readout per dot, in drawing order: the 40 Nays, then the breaker.
        assert len(marks) == 41 and len({m["id"] for m in marks}) == 41
        assert {m["shown"] for m in marks[:-1]} == {"Voted Nay"}
        assert marks[-1] == {"id": "pat-lone", "label": "Pat Lone", "detail": "R-AA", "shown": "Voted Yea",
                             "context": "The only Republican to vote Yea", "href": "/politicians/pat-lone"}
        # BB has one senator on file: nobody to compare with.
        assert breaks[0]["compareWithId"] is None

    def test_a_break_with_a_few_attendees_is_not_a_break_with_the_party(self, db_session):
        """The rest voting the other way must be most of the party's members
        on the roll: eight of the party's forty-nine is a handful, not the
        party."""
        _senators(db_session)
        rc = _rc(db_session, "2026-09-30", 10, 10)
        _party_votes(db_session, rc, "R", 0, 8, lone=("Lone", "AA", "Yea"))
        for i in range(40):
            db_session.add(RollCallPosition(roll_call_id=rc.id, member_id=f"Rx{i}", last_name=f"RX{i}",
                                            party="R", state="ZZ", position="Not Voting"))
        db_session.commit()
        assert _breaks(db_session, date(2026, 10, 2)) == []

    def test_a_small_party_present_in_full_can_break(self, db_session):
        """No count is typed: two of a three-member party against the third
        is the party against one of its own."""
        _senators(db_session)
        rc = _rc(db_session, "2026-09-30", 10, 10)
        _party_votes(db_session, rc, "R", 0, 2, lone=("Lone", "AA", "Yea"))
        db_session.commit()
        assert [b["memberId"] for b in _breaks(db_session, date(2026, 10, 2))] == ["pat-lone"]

    def test_the_majority_leaders_reconsider_switch_is_not_a_break(self, db_session):
        _senators(db_session)
        rc = _rc(db_session, "2026-09-30", 48, 51, rejected=True)
        # The party voted Yea on a question that failed; the leader moved to
        # the prevailing Nay side to be able to move to reconsider.
        _party_votes(db_session, rc, "R", 48, 0, lone=("Leader", "CC", "Nay"))
        db_session.commit()
        assert _breaks(db_session, date(2026, 10, 2)) == []


class TestWindow:
    def test_the_latest_week_of_business_however_long_ago(self, db_session):
        _rc(db_session, "2026-09-30", 49, 50)
        db_session.commit()
        assert nt._business_week(db_session, date(2026, 12, 1)) == (date(2026, 9, 28), date(2026, 10, 4))
        assert len(_entries(db_session, date(2026, 12, 1))) == 1

    def test_no_business_on_record(self, db_session):
        assert nt._business_week(db_session, TODAY) is None and nt._week_votes(db_session, None) == []


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

    def test_a_close_vote_leads(self):
        page = nt.arrange(*self.everything())
        assert page["lead"]["kind"] == "closest"
        assert [e["kind"] for e in page["tier"]] == ["law", "comment"]

    def test_each_fallback_in_order(self):
        congress, agencies, news = self.everything()
        congress[0]["close"] = False  # not under the Congress's median margin
        assert nt.arrange(congress, agencies, news)["lead"]["kind"] == "law"
        assert nt.arrange([congress[0], *congress[2:]], agencies, news)["lead"]["kind"] == "confirmed"
        assert nt.arrange([congress[0], congress[3]], agencies, news)["lead"]["kind"] == "loneBreak"
        assert nt.arrange([congress[0]], agencies, news)["lead"]["headline"] == "issue 2026-10-20"
        assert nt.arrange([], agencies, [])["lead"]["kind"] == "comment"
        assert nt.arrange([], [], []) == {
            "lead": None, "tier": [], "columns": {"congress": [], "agencies": [], "news": []}}

    def test_while_congress_is_out_a_vote_does_not_lead(self):
        congress, agencies, news = self.everything()
        page = nt.arrange(congress, agencies, news, congress_out=True)
        assert page["lead"]["kind"] == "law"  # a law signed in a recess is news
        votes_only = [congress[0], congress[3]]
        page = nt.arrange(votes_only, agencies, news, congress_out=True)
        assert page["lead"]["kind"] == "issue"
        assert page["tier"][0] is congress[0]  # still on the page, dated

    def test_no_entry_appears_twice(self):
        page = nt.arrange(*self.everything())
        shown = [page["lead"], *page["tier"], *(e for col in page["columns"].values() for e in col)]
        assert len({id(e) for e in shown}) == len(shown) == 8

    def test_the_tier_skips_entries_without_a_figure_and_never_takes_one_column_twice(self):
        congress, _, news = self.everything()
        congress[1]["figure"] = None
        page = nt.arrange(congress, [], news)
        assert [e["kind"] for e in page["tier"]] == ["confirmed", "issue"]
        assert [e["kind"] for e in page["columns"]["congress"]] == ["loneBreak", "law"]

    def test_a_column_takes_one_of_each_kind_before_a_second(self):
        laws = [_e("law", "congress", f"2026-10-1{d}") for d in (5, 4, 3, 2, 1)]
        lone = _e("loneBreak", "congress", "2026-10-01")
        page = nt.arrange(laws + [lone], [], [])
        assert page["lead"] is laws[0] and page["tier"] == [laws[1]]
        assert [e["kind"] for e in page["columns"]["congress"]] == ["law", "law", "loneBreak"]
        assert laws[4] not in page["columns"]["congress"]


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
        got = nt._confirmations(db_session, date(2026, 10, 12), TODAY)
        assert nt._confirmations(db_session, date(2026, 10, 19), TODAY) == []
        assert got[0]["headline"].startswith("Confirmed by the Senate: Jane")
        assert got[0]["figure"]["type"] == "split" and got[0]["figure"]["caption"] == "Confirmed 51–47"


class TestFrontPage:
    def test_while_congress_is_out_the_news_leads_and_the_last_week_of_business_is_dated(
            self, db_session, freeze_utcnow):
        freeze_utcnow(datetime(2026, 10, 20, 16))
        _rc(db_session, "2026-08-20", 49, 50)  # the last business, weeks ago
        _rc(db_session, "2026-08-19", 70, 30)
        db_session.add(CongressDay(chamber="senate", date="2026-10-19", in_session=True, is_final=True,
                                   source="digest", adjournment_text="The Senate met in pro forma session.",
                                   next_meeting="3:30 p.m., Thursday, October 22",
                                   next_program="Program for Thursday: Senate will meet in a pro forma session."))
        _issue(db_session, "2026-10-20", 1, "The day's top story")
        _comment(db_session, "2026-10-21")
        db_session.commit()
        page = nt.front_page(db_session, TODAY)
        assert page["lead"]["headline"] == "The day's top story"
        # The close vote of the last week of business is still shown, dated.
        assert [e["kind"] for e in page["tier"]] == ["closest", "comment"]
        assert page["tier"][0]["close"] and page["tier"][0]["date"] == "2026-08-20"
        assert page["dateline"][:2] == ["Tuesday, October 20",
                                        "Congress is out; the Senate next sits Thursday, October 22, pro forma"]
        assert page["voteWeek"] == "2026-08-17"
        assert page["edition"]["weekLabel"] == "The week of August 17"

    def test_nothing_on_record_is_no_front_page(self, db_session):
        assert nt.front_page(db_session, TODAY)["lead"] is None


def test_the_endpoint_reads_the_eastern_clock(db_session, freeze_utcnow):
    _rc(db_session, "2026-10-07", 70, 30)
    _rc(db_session, "2026-10-14", 49, 50)
    db_session.commit()
    # 03:00 UTC on Oct 22 is still Oct 21 in the East.
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


def test_a_law_with_a_short_title_leads_with_its_name():
    named = {"name": "Example Fisheries Enforcement Act of 2026", "billLabel": "S. 9", "billId": "S.9"}
    assert nt._law_name(named) == "Example Fisheries Enforcement Act of 2026"
    # An official long title is not a name news uses: the number leads.
    long = {"name": "A bill to amend title 46 to improve example enforcement.", "billLabel": "H.R. 9", "billId": "HR.9"}
    assert nt._law_name(long) is None
