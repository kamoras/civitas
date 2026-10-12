"""Searching a member's votes (?q= on /senators/{id}/votes and
/representatives/{id}/votes): "how did my senator vote on X" meant paging
through a dozen pages of fifteen."""

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import KeyVote, Representative, RepKeyVote, RollCall, Senator
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
