"""The public retraction log: withdrawn issues answer 410 with the reason,
and their Bluesky posts are deleted once each."""

import pytest
from fastapi.testclient import TestClient

from app import retractions
from app.database import get_db
from app.main import app


def test_the_log_is_well_formed():
    for entry in retractions.entries():
        assert entry["reason"] and entry["date"] and entry["migration"]
        assert len(entry["issueIds"]) == len(entry["publicIds"])
        assert all(u.startswith("at://did:plc:") and "/app.bsky.feed.post/" in u for u in entry["bskyPosts"])


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


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setattr(retractions.settings, "BSKY_HANDLE", "civitas.test", raising=False)
    monkeypatch.setattr(retractions.settings, "BSKY_APP_PASSWORD", "x", raising=False)


def test_each_post_is_deleted_once_and_a_failure_is_retried(db_session, credentials, monkeypatch):
    posts = [u for e in retractions.entries() for u in e["bskyPosts"]]
    attempts = []
    fail = {posts[0]}
    monkeypatch.setattr(retractions, "_delete_post", lambda uri: attempts.append(uri) or uri not in fail)
    assert retractions.delete_retracted_posts(db_session) == len(posts) - 1
    fail.clear()
    assert retractions.delete_retracted_posts(db_session) == 1
    assert retractions.delete_retracted_posts(db_session) == 0
    assert attempts.count(posts[0]) == 2 and attempts.count(posts[1]) == 1


def test_a_post_already_gone_counts_as_deleted(monkeypatch, credentials):
    class Gone:
        def login(self, *a):
            pass

        def delete_post(self, uri):
            raise RuntimeError("Could not locate record")

    import atproto
    monkeypatch.setattr(atproto, "Client", Gone)
    assert retractions._delete_post("at://did:plc:x/app.bsky.feed.post/y") is True


def test_no_credentials_no_deletion(db_session, monkeypatch):
    monkeypatch.setattr(retractions.settings, "BSKY_HANDLE", "", raising=False)
    assert retractions.delete_retracted_posts(db_session) == 0
