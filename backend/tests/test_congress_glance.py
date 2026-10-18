"""/congress at a glance: the sitting calendar, the bill pipeline and a
picked state's members, all from stored rows."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import CongressDay, Representative, RepSponsoredBill, RollCall, RollCallPosition
from app.services import bill_service, congress_glance


@pytest.fixture
def db(db_session, monkeypatch):
    monkeypatch.setattr(congress_glance, "eastern_today", lambda: date(2025, 3, 5))
    for chamber, day, sat in (("senate", "2025-01-03", True), ("house", "2025-01-03", True),
                              ("senate", "2025-01-04", False), ("house", "2025-02-10", True)):
        db_session.add(CongressDay(chamber=chamber, date=day, in_session=sat, source="digest"))
    # A roll call on a day with no Record row still counts as a sitting.
    for n, (chamber, day) in enumerate((("house", "2025-01-03"), ("house", "2025-01-03"), ("senate", "2025-02-11")), 1):
        db_session.add(RollCall(chamber=chamber, congress=119, session=1, number=n, date=day, question="On Passage"))
    db_session.add(Representative(id="pat-quill", bioguide_id="Q000001", name="Pat Quill", state="PA",
                                  district=7, party="D", is_current=True))
    db_session.add(Representative(id="gone", bioguide_id="G000001", name="Gone", state="PA", party="R",
                                  is_current=False))
    db_session.flush()
    rc = db_session.query(RollCall).filter_by(chamber="house", number=1).one()
    db_session.add(RollCallPosition(roll_call_id=rc.id, member_id="Q000001", last_name="Quill", party="D",
                                    state="PA", position="Not Voting"))
    db_session.add(RepSponsoredBill(representative_id="pat-quill", bill_id="HR.12", title="A Long Act",
                                    introduced_date="2025-02-01", congress=119, stage="REFERRED"))
    db_session.add(RepSponsoredBill(representative_id="pat-quill", bill_id="HR.9", title="Old", congress=118))
    db_session.commit()
    bill_service.clear_bill_collection_cache()
    yield db_session
    bill_service.clear_bill_collection_cache()


def test_calendar_counts_sittings_and_votes(db):
    cal = congress_glance.sitting_calendar(db, 119)
    assert (cal["start"], cal["end"], cal["today"]) == ("2025-01-03", "2027-01-02", "2025-03-05")
    assert cal["days"] == {"2025-01-03": {"senate": 0, "house": 2}, "2025-02-10": {"house": 0},
                           "2025-02-11": {"senate": 1}}
    assert [m["month"] for m in cal["months"]] == ["2025-01", "2025-02", "2025-03"]
    assert cal["months"][1] == {"month": "2025-02", "senate": {"days": 1, "votes": 1},
                                "house": {"days": 1, "votes": 0}}
    assert cal["totals"] == {"senate": {"days": 2, "votes": 1}, "house": {"days": 2, "votes": 2}}
    assert cal["maxVotes"] == 2
    assert cal["recordFrom"] == "2025-01-03"


def test_state_members_record(db):
    [m] = congress_glance.state_members(db, "PA")
    assert (m["id"], m["chamber"], m["district"]) == ("pat-quill", "house", 7)
    assert (m["rollCalls"], m["missed"], m["voted"]) == (1, 1, 0)
    assert m["votes"][0]["vote"] == "Not Voting"
    assert m["votes"][0]["rollCall"]["number"] == 1
    assert m["billsSponsored"] == 1
    assert (m["bills"][0]["billId"], m["bills"][0]["billLabel"]) == ("HR.12", "H.R. 12")


def test_routes(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        assert client.get("/api/congress/members?state=pa").json()["state"] == "PA"
        assert client.get("/api/congress/members?state=ZZ").status_code == 404
        body = client.get("/api/congress/glance").json()
        assert set(body) == {"calendar", "pipeline"}
    finally:
        app.dependency_overrides.clear()
