"""Searching a member's votes (?q= on /senators/{id}/votes and
/representatives/{id}/votes): "how did my senator vote on X" meant paging
through a dozen pages of fifteen."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import KeyVote, Representative, RepKeyVote, RollCall, Senator
from app.services._scorecard_common import vote_period_start
from app.services.representative_service import get_rep_votes
from app.services.senator_service import get_senator_votes


@pytest.fixture
def senator(db_session):
    db_session.add(Senator(id="s1", name="Senator Test", state="SW", party="D", is_current=True))
    rows = [
        ("H.R. 7008", "A bill to restrict stock trading by Members", "senate-119-2-300", "Nay", True),
        ("S. 1", "A bill to fund the highways", "senate-119-2-301", "Yea", True),
        ("S.Res. 15", "A resolution honoring teachers", None, "Yea", False),
        ("PN1129", "A nominee, to be Secretary of Labor", "senate-119-2-302", "Nay", True),
    ]
    for n, (bill_id, name, ref, vote, with_party) in enumerate(rows):
        db_session.add(KeyVote(senator_id="s1", bill_id=bill_id, bill_name=name, date=f"2026-09-{10 + n:02d}",
                               vote=vote, vote_category="recent", roll_call=ref, voted_with_party=with_party))
    db_session.add(RollCall(chamber="senate", congress=119, session=2, number=302, date="2026-09-13",
                            question="On the Cloture Motion PN1129", result="Agreed to", yeas=51, nays=47))
    db_session.commit()
    return db_session


@pytest.mark.parametrize("q,found", [
    ("HR 7008", ["H.R. 7008"]),
    ("h.r.7008", ["H.R. 7008"]),
    ("hr70", ["H.R. 7008"]),  # while typing
    ("7008", ["H.R. 7008"]),  # the number alone
    ("S 1", ["S. 1"]),  # not S. Res. 15, whose bare form "SRES15" contains "S1"
    ("highways", ["S. 1"]),
    ("highway funding", ["S. 1"]),  # each word, stemmed, in any order
    ("trading stocks", ["H.R. 7008"]),
    ("teacher", ["S.Res. 15"]),
    ("cloture", ["PN1129"]),  # the roll call's question
    ("100%", []),  # a LIKE wildcard is a character here
])
def test_finds_by_number_title_or_question(senator, q, found):
    page = get_senator_votes(senator, "s1", category="all", q=q)
    assert [v.bill_id for v in page.votes] == found
    assert page.total == page.counts.all == len(found)


def test_the_counts_follow_the_search(senator):
    page = get_senator_votes(senator, "s1", category="all", q="a bill", vote_filter="nay")
    assert (page.counts.all, page.counts.yea, page.counts.nay) == (2, 1, 1)
    assert [v.bill_id for v in page.votes] == ["H.R. 7008"]


def test_a_blank_search_is_no_search(senator):
    assert get_senator_votes(senator, "s1", category="all", q="  ").total == 4


def test_house_votes_search_too(db_session):
    db_session.add(Representative(id="r1", name="Rep Test", state="SW", district=1, party="R", is_current=True))
    for bill_id, name in (("H.R. 22", "A bill on water"), ("H.R. 23", "A bill on roads")):
        db_session.add(RepKeyVote(representative_id="r1", bill_id=bill_id, bill_name=name, date="2026-09-10",
                                  vote="Yea", vote_category="recent"))
    db_session.commit()
    assert [v["billId"] for v in get_rep_votes(db_session, "r1", category="all", q="roads")["votes"]] == ["H.R. 23"]


def test_the_route_takes_q(senator):
    app.dependency_overrides[get_db] = lambda: senator
    try:
        body = TestClient(app).get("/api/senators/s1/votes", params={"category": "all", "q": "7008"}).json()
    finally:
        app.dependency_overrides.clear()
    assert [v["billId"] for v in body["votes"]] == ["H.R. 7008"]


# A date range (?period=), so "what did they vote on lately" is one tap.
@pytest.fixture
def dated(db_session):
    db_session.add(Senator(id="s2", name="Senator Dated", state="SW", party="D", is_current=True))
    for n, day in enumerate(("2024-12-20", "2025-01-03", "2026-09-12", "2026-09-30", "2026-10-10")):
        db_session.add(KeyVote(senator_id="s2", bill_id=f"S. {n + 1}", bill_name="A bill", date=day,
                               vote="Yea" if n % 2 else "Nay", vote_category="key"))
    db_session.commit()
    return db_session


@pytest.mark.parametrize("period,found", [
    ("month", ["S. 5", "S. 4"]),  # the last 30 days: from 2026-09-13
    ("congress", ["S. 5", "S. 4", "S. 3", "S. 2"]),  # from the 119th's Jan 3, 2025
    ("all", ["S. 5", "S. 4", "S. 3", "S. 2", "S. 1"]),
])
def test_period_narrows_the_list_and_its_counts(dated, freeze_utcnow, period, found):
    freeze_utcnow(datetime(2026, 10, 13, 15, 0))
    page = get_senator_votes(dated, "s2", category="all", period=period)
    assert [v.bill_id for v in page.votes] == found
    assert page.counts.all == len(found)


def test_month_counts_days_on_the_capitols_calendar(freeze_utcnow):
    # 02:00 UTC on Oct 14 is still Oct 13 in Washington: 30 days back is Sep 13.
    freeze_utcnow(datetime(2026, 10, 14, 2, 0))
    assert vote_period_start("month") == "2026-09-13"
    assert vote_period_start("all") is None


def test_house_votes_take_a_period_too(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 10, 13, 15, 0))
    db_session.add(Representative(id="r2", name="Rep Dated", state="SW", district=1, party="R", is_current=True))
    for bill_id, day in (("H.R. 1", "2026-08-01"), ("H.R. 2", "2026-10-01")):
        db_session.add(RepKeyVote(representative_id="r2", bill_id=bill_id, bill_name="A bill", date=day,
                                  vote="Yea", vote_category="recent"))
    db_session.commit()
    page = get_rep_votes(db_session, "r2", category="all", period="month")
    assert [v["billId"] for v in page["votes"]] == ["H.R. 2"] and page["counts"]["all"] == 1


def test_the_route_refuses_an_unknown_period(senator):
    app.dependency_overrides[get_db] = lambda: senator
    try:
        client = TestClient(app)
        assert client.get("/api/senators/s1/votes", params={"period": "week"}).status_code == 422
        assert client.get("/api/senators/s1/votes", params={"period": "congress"}).status_code == 200
    finally:
        app.dependency_overrides.clear()
