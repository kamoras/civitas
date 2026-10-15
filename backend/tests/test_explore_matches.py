"""Explore's Members and Bills groups (GET /api/explore/matches): "Fetterman"
gave thirty floor speeches and no link to the senator, and "flag burning"
never offered the Flag Burners Act."""

from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.models import Representative, Senator, SponsoredBill
from app.services.bill_service import clear_bill_collection_cache
from app.services.explore_matches import bill_matches, explore_matches, member_matches


def _seed(db):
    db.add_all([
        Senator(id="avery-holt", name="Avery Holt", state="PA", party="D", is_current=True),
        Senator(id="jo-warren", name="Jo Warren", state="MA", party="D", is_current=True),
        Senator(id="old-timer", name="Old Timer", state="PA", party="R", is_current=False),
        Representative(id="sam-reyes", name="Sam Reyes", state="PA", district=7, party="R", is_current=True),
    ])
    for bill_id, title, sponsor in (
        ("S.2079", "Flag Burners Act", "avery-holt"),
        ("S.3", "Flag Code Modernization Act", "avery-holt"),
        ("S.4", "A bill to rename a post office", "jo-warren"),
        ("S.5", "Warren County Flood Relief Act", "avery-holt"),
    ):
        db.add(SponsoredBill(senator_id=sponsor, bill_id=bill_id, title=title, stage="INTRODUCED",
                             congress=settings.CURRENT_CONGRESS, bill_type="S", latest_action_date="2026-01-01"))
    db.commit()
    clear_bill_collection_cache()


def test_a_name_finds_the_member_and_a_misspelling_still_does(db_session):
    _seed(db_session)
    assert [m["id"] for m in member_matches(db_session, "holt")[1]] == ["avery-holt"]
    assert [m["id"] for m in member_matches(db_session, "Avery Holt")[1]] == ["avery-holt"]
    assert [m["id"] for m in member_matches(db_session, "hlot")[1]] == ["avery-holt"]  # transposed
    assert member_matches(db_session, "zorp") == (0, [])  # nothing close
    assert [m["id"] for m in member_matches(db_session, "warrne")[1]] == ["jo-warren"]  # difflib, 0.83
    # Sitting members only.
    assert member_matches(db_session, "timer") == (0, [])


def test_a_state_lists_its_delegation_and_names_rank_first(db_session):
    _seed(db_session)
    total, members = member_matches(db_session, "pennsylvania")
    assert total == 2 and {m["id"] for m in members} == {"avery-holt", "sam-reyes"}
    assert members[0]["chamber"] in ("senate", "house")
    assert next(m for m in members if m["id"] == "sam-reyes")["district"] == 7


def test_a_topic_finds_bills_not_members(db_session):
    _seed(db_session)
    groups = explore_matches(db_session, "flag burning")
    assert groups["members"]["total"] == 0
    assert [b["billId"] for b in groups["bills"]["items"]] == ["S.2079"]
    assert groups["bills"]["items"][0]["label"] == "S. 2079"


def test_bills_match_titles_not_sponsors(db_session):
    # "warren" is S. 5's title and S. 4's sponsor: only the title counts here
    # (the senator is the Members group's), unlike the bills list's search.
    _seed(db_session)
    total, bills = bill_matches(db_session, "warren")
    assert total == 1 and [b["billId"] for b in bills] == ["S.5"]


def test_a_bill_number_is_that_bill_tracked_or_not(db_session):
    _seed(db_session)
    assert [b["billId"] for b in bill_matches(db_session, "S. 2079")[1]] == ["S.2079"]
    # Not a sitting member's: still offered, its page is Congress.gov's record.
    total, bills = bill_matches(db_session, "H.R. 7008")
    assert total == 1 and bills[0]["billId"] == "HR.7008" and bills[0]["title"] is None


def test_the_route(db_session):
    _seed(db_session)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        res = TestClient(app).get("/api/explore/matches", params={"q": "holt"})
    finally:
        app.dependency_overrides.clear()
    assert res.status_code == 200
    body = res.json()
    assert body["members"]["items"][0]["id"] == "avery-holt"
    assert "max-age" in res.headers["cache-control"]
