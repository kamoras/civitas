"""Tests for reset_all_data()'s vector-store reset summary reporting.

reset_vector_db() itself (sqlite-vec) is tested in
test_vector_store_sqlitevec.py — these only cover reset_all_data()'s own
success/failure bookkeeping around that call, renamed from chromadb_* to
vector_db_* during the 2026-07 migration cleanup (the naming was stale;
the call itself already targeted sqlite-vec).
"""

from unittest.mock import patch

from app import models  # noqa: F401 — registers all Base subclasses before db_session's create_all()
from app.database import reset_all_data


class TestResetAllDataVectorStoreSummary:
    def test_records_vector_db_collections_on_success(self, db_session, monkeypatch):
        monkeypatch.setattr("app.database.SessionLocal", lambda: db_session)
        with patch("app.pipeline.vector_store.reset_vector_db"):
            summary = reset_all_data()
        assert summary["vector_db_collections"] == 2
        assert "vector_db_error" not in summary

    def test_records_vector_db_error_on_failure(self, db_session, monkeypatch):
        monkeypatch.setattr("app.database.SessionLocal", lambda: db_session)
        with patch(
            "app.pipeline.vector_store.reset_vector_db",
            side_effect=RuntimeError("boom"),
        ):
            summary = reset_all_data()
        assert summary["vector_db_error"] == "reset failed — see server logs"
        assert "vector_db_collections" not in summary
