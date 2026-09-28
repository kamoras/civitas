"""The public retraction log: withdrawn issues answer 410 with the reason."""

from fastapi.testclient import TestClient

from app import retractions
from app.database import get_db
from app.main import app


def test_the_log_is_well_formed():
    for entry in retractions.entries():
        assert entry["reason"] and entry["date"] and entry["migration"]
        assert len(entry["issueIds"]) == len(entry["publicIds"])


def test_a_withdrawn_issue_is_found_by_either_id():
    assert retractions.retraction_for_issue("i1679cbc7")["date"] == "2026-09-27"
    assert retractions.retraction_for_issue("759") is not None
    assert retractions.retraction_for_issue("i0000000") is None


def test_a_withdrawn_issue_answers_410_with_its_reason(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        r = TestClient(app).get("/api/action/issues/i1679cbc7")
        assert r.status_code == 410
        assert r.json()["detail"]["retracted"] is True
        assert "unrelated news stories" in r.json()["detail"]["reason"]
        assert TestClient(app).get("/api/action/issues/i0000001").status_code == 404
    finally:
        app.dependency_overrides.clear()
