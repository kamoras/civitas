"""The per-state member lists (GET /api/senators?state=, /api/representatives?state=)
name who serves the state now. A departed member's row stays through the
retirement grace period, and these lists once returned it beside the
seated successor: three senators for one state, the departed one unmarked."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.router import api_router
from app.database import get_db
from app.models import Representative, Senator
from app.pipeline.transform.normalize_members import normalize_members


def _client(db_session) -> TestClient:
    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def test_state_senators_leave_out_a_departed_senator(db_session):
    db_session.add_all([
        Senator(id="ann-roe", name="Ann Roe", state="OH", party="R"),
        Senator(id="bo-doe", name="Bo Doe", state="OH", party="R",
                is_current=False, left_office_date="2026-07-01"),
        Senator(id="cy-poe", name="Cy Poe", state="OH", party="R"),
    ])
    db_session.commit()
    body = _client(db_session).get("/api/senators?state=OH").json()
    assert sorted(s["id"] for s in body) == ["ann-roe", "cy-poe"]


def test_state_representatives_leave_out_a_departed_member(db_session):
    db_session.add_all([
        Representative(id="di-roe", name="Di Roe", state="OH", party="D", district=1),
        Representative(id="ed-doe", name="Ed Doe", state="OH", party="D", district=2,
                       is_current=False, left_office_date="2026-07-01"),
    ])
    db_session.commit()
    body = _client(db_session).get("/api/representatives?state=OH&per_page=60").json()
    assert [r["id"] for r in body["entries"]] == ["di-roe"]
    assert body["total"] == 1


def test_a_generational_suffix_follows_the_surname():
    [member] = normalize_members([
        {"bioguideId": "R000009", "name": "Roe, Jane Q., Jr.", "state": "Ohio", "chamber": "Senate"},
    ])
    assert member["name"] == "Jane Q. Roe Jr."
    assert member["initials"] == "JR"


def test_a_name_without_a_suffix_is_unchanged():
    [member] = normalize_members([
        {"bioguideId": "D000009", "name": "Doe Roe, Jane", "state": "Ohio", "chamber": "Senate"},
    ])
    assert member["name"] == "Jane Doe Roe"
    assert member["initials"] == "JR"
