"""POST /explore/{doc_id}/comments — transport and deadline.

Goes through a real ASGI app (not a direct call) because both things under
test live in FastAPI's request handling: where the comment is read from
(body vs query string) and how the deadline is compared.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.time_utils as time_utils
from app.api import explore as explore_module
from app.database import get_db
from app.models import ExploreDocument

COMMENT = "This proposed rule would affect my small business directly."


@pytest.fixture
def client(db_session):
    app = FastAPI()
    app.include_router(explore_module.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _doc(db_session, close_on: str) -> int:
    doc = ExploreDocument(
        doc_type="Proposed Rule",
        source="Federal Register",
        title="A proposed rule",
        body="Body.",
        date="2026-08-01",
        chamber="Regulatory",
        comment_url="https://www.regulations.gov/commenton/EPA-HQ-OAR-2026-0001-0001",
        comments_close_on=close_on,
    )
    db_session.add(doc)
    db_session.commit()
    return doc.id


def _freeze(monkeypatch, utc: datetime) -> None:
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc.astimezone(tz) if tz else utc.replace(tzinfo=None)

    monkeypatch.setattr(time_utils, "datetime", _Clock)


class TestCommentTransport:
    def test_body_submission_reaches_regulations_gov(self, client, db_session, monkeypatch):
        _freeze(monkeypatch, datetime(2026, 8, 10, 15, tzinfo=timezone.utc))
        doc_id = _doc(db_session, "2026-08-18")
        submit = AsyncMock(return_value={"success": True, "commentId": "c-1", "message": "ok"})
        with patch("app.pipeline.fetch.regulations_gov.submit_comment", submit):
            resp = client.post(
                f"/api/explore/{doc_id}/comments",
                json={"comment": COMMENT, "name": "Pat", "organization": ""},
            )
        assert resp.status_code == 201
        assert submit.await_args.kwargs["comment_text"] == COMMENT
        assert submit.await_args.kwargs["submitter_name"] == "Pat"

    def test_legacy_query_submission_still_accepted(self, client, db_session, monkeypatch):
        """For one release, until every frontend task has rolled."""
        _freeze(monkeypatch, datetime(2026, 8, 10, 15, tzinfo=timezone.utc))
        doc_id = _doc(db_session, "2026-08-18")
        resp = client.post(
            f"/api/explore/{doc_id}/comments",
            params={"comment": COMMENT, "dry_run": "true"},
        )
        assert resp.status_code == 200
        assert resp.json()["dryRun"] is True

    def test_body_validation_applies(self, client, db_session, monkeypatch):
        _freeze(monkeypatch, datetime(2026, 8, 10, 15, tzinfo=timezone.utc))
        doc_id = _doc(db_session, "2026-08-18")
        resp = client.post(f"/api/explore/{doc_id}/comments", json={"comment": "short"})
        assert resp.status_code == 422

    def test_no_comment_at_all_is_rejected(self, client, db_session, monkeypatch):
        _freeze(monkeypatch, datetime(2026, 8, 10, 15, tzinfo=timezone.utc))
        doc_id = _doc(db_session, "2026-08-18")
        resp = client.post(f"/api/explore/{doc_id}/comments")
        assert resp.status_code == 422


class TestCommentDeadline:
    def test_open_on_final_eastern_evening(self, client, db_session, monkeypatch):
        """21:30 EDT on the closing day is already the next day in UTC —
        regulations.gov still accepts until 11:59 PM ET, so must we."""
        _freeze(monkeypatch, datetime(2026, 8, 19, 1, 30, tzinfo=timezone.utc))
        doc_id = _doc(db_session, "2026-08-18")
        resp = client.post(
            f"/api/explore/{doc_id}/comments", json={"comment": COMMENT, "dry_run": True},
        )
        assert resp.status_code == 200, resp.json()

    def test_closed_after_eastern_midnight(self, client, db_session, monkeypatch):
        _freeze(monkeypatch, datetime(2026, 8, 19, 4, 30, tzinfo=timezone.utc))
        doc_id = _doc(db_session, "2026-08-18")
        resp = client.post(
            f"/api/explore/{doc_id}/comments", json={"comment": COMMENT, "dry_run": True},
        )
        assert resp.status_code == 400
        assert "closed" in resp.json()["detail"]


def test_comment_period_today_is_eastern(monkeypatch):
    _freeze(monkeypatch, datetime(2026, 8, 19, 1, 30, tzinfo=timezone.utc))
    assert time_utils.comment_period_today() == "2026-08-18"
