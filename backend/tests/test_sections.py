"""GET /api/sections: one live figure per section page, for the hub cards
and the homepage's "Sections" band."""

from datetime import datetime

from fastapi.testclient import TestClient

from app.api import sections
from app.database import get_db
from app.main import app
from app.models import Agency, PresidentialSupport, Representative, RollCall


def _get(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        return TestClient(app).get("/api/sections")
    finally:
        app.dependency_overrides.clear()


def _vote(day, number, chamber="house"):
    return RollCall(chamber=chamber, congress=119, session=2, number=number, date=day, question="On Passage",
                    title="A bill", result="Passed", yeas=60, nays=40, bill_id="HR.1")


def test_nothing_read_is_null_or_a_reason_never_a_made_up_zero(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 10, 15, 16))
    r = _get(db_session)
    assert r.status_code == 200
    assert "max-age=300" in r.headers["cache-control"]
    body = r.json()
    assert body["congress"]["reports"] is None and body["congress"]["week"] is None
    assert body["congress"]["committees"] == {"count": 0, "metThisWeek": None}
    assert body["congress"]["president"] == {"house": None, "senate": None}
    assert body["agencies"]["budget"] is None
    assert body["action"]["today"] == {"issues": 0, "date": None}
    # The map page's own reason: not built (no file) here.
    assert body["elections"]["map"]["available"] is False and body["elections"]["map"]["reason"]
    assert body["elections"]["ballots"]["phase"] == "campaign"
    assert body["elections"]["redistricting"]["states"] > 0


def test_counts_the_latest_week_and_month_congress_met(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 10, 15, 16))
    db_session.add_all([
        _vote("2026-10-14", 1), _vote("2026-10-14", 2), _vote("2026-10-12", 3),
        _vote("2026-10-02", 4), _vote("2026-09-30", 5, "senate"),
    ])
    db_session.commit()
    c = sections.congress(db_session)
    assert c["reports"] == {"date": "2026-10-14", "recordVotes": 2}
    assert c["week"] == {"start": "2026-10-12", "recordVotes": 3}
    assert c["month"] == {"month": "2026-10", "recordVotes": 4}


def test_votes_with_the_president_counts_serving_members_only(db_session):
    db_session.add_all([
        Representative(id="a", bioguide_id="A000001", name="A", state="OH", district=1, party="R", is_current=True),
        Representative(id="b", bioguide_id="B000001", name="B", state="OH", district=2, party="D", is_current=True),
        Representative(id="gone", bioguide_id="G000001", name="G", state="OH", district=3, party="D",
                       is_current=False),
        PresidentialSupport(chamber="house", member_id="a", congress=119, party="R", with_president=9, votes=10),
        PresidentialSupport(chamber="house", member_id="b", congress=119, party="D", with_president=1, votes=10),
        PresidentialSupport(chamber="house", member_id="gone", congress=119, party="D", with_president=0, votes=10),
    ])
    db_session.commit()
    assert sections._president(db_session, "house") == {"rate": 0.5, "rollCalls": 0}
    assert sections._president(db_session, "senate") is None


def test_agencies(db_session):
    db_session.add(Agency(slug="epa", fr_id=145, name="Environmental Protection Agency", synced_at=datetime(2026, 10, 1)))
    db_session.commit()
    a = sections.agencies(db_session, open_for_comment=7)
    assert a["directory"] == {"count": 1}
    assert a["comments"] == {"open": 7}
    assert a["budget"] is None
