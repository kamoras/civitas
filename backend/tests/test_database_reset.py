"""Tests for reset_all_data() and the admin endpoint that runs it.

reset_vector_db() itself (sqlite-vec) is tested in
test_vector_store_sqlitevec.py; here only reset_all_data()'s bookkeeping
around that call is (its vector_db_* summary keys were chromadb_* before
the 2026-07 migration cleanup).
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


class TestResetAllDataTables:
    def test_clears_annual_report_holdings(self, db_session, monkeypatch):
        db_session.add(models.Senator(id="S1", name="A Senator", state="TX", party="R"))
        disclosure = models.FinancialDisclosure(
            senator_id="S1", filing_id="f", report_label="2025 annual report", source_url="u", parsed=True,
        )
        disclosure.holdings.append(models.FinancialHolding(asset_name="Apple", category="STOCKS", value_text="x"))
        db_session.add(disclosure)
        db_session.commit()
        monkeypatch.setattr("app.database.SessionLocal", lambda: db_session)
        with patch("app.pipeline.vector_store.reset_vector_db"):
            reset_all_data()
        assert db_session.query(models.FinancialDisclosure).count() == 0
        assert db_session.query(models.FinancialHolding).count() == 0

    def test_empties_every_table_but_the_kept_ones(self, db_session, monkeypatch):
        from sqlalchemy import func, select

        from app.database import RESET_KEEPS, Base

        db_session.add(models.Senator(id="S1", name="A Senator", state="TX", party="R"))
        db_session.add(models.PipelineRun(status="completed"))
        db_session.add(models.ActionIssue(date="2026-09-01", rank=1, title="An issue", related_explore_ids="[12, 40]"))
        db_session.commit()
        monkeypatch.setattr("app.database.SessionLocal", lambda: db_session)
        with patch("app.pipeline.vector_store.reset_vector_db"):
            summary = reset_all_data()
        tables = {t.name for t in Base.metadata.sorted_tables}
        assert RESET_KEEPS <= tables  # a renamed table must not drop out of the keep list unnoticed
        assert set(summary) >= tables - RESET_KEEPS
        assert db_session.query(models.PipelineRun).count() == 1  # run history is kept
        # A kept issue no longer links to Explore rowids the rebuild reuses.
        db_session.expire_all()
        assert db_session.query(models.ActionIssue).one().related_explore_ids == "[]"
        for table in Base.metadata.sorted_tables:
            if table.name not in RESET_KEEPS:
                assert db_session.execute(select(func.count()).select_from(table)).scalar_one() == 0, table.name


class TestResetGuard:
    async def test_refuses_while_any_pipeline_writes(self, db_session):
        import pytest
        from fastapi import HTTPException

        from app.api.admin import admin_reset_data

        from app.pipeline.house_pipeline import _tracker

        _tracker.start()
        try:
            with patch("app.database.reset_all_data") as reset:
                with pytest.raises(HTTPException) as refused:
                    await admin_reset_data(db=db_session)
        finally:
            _tracker.stop()
        assert refused.value.status_code == 409 and "House" in refused.value.detail
        reset.assert_not_called()

    async def test_refuses_while_the_action_center_refresh_holds_its_lease(self, db_session):
        import pytest
        from fastapi import HTTPException

        from app.api.admin import admin_reset_data
        from app.pipeline.analyze.action_center import _acquire_refresh_lock

        assert _acquire_refresh_lock(db_session) is not None
        with patch("app.database.reset_all_data") as reset:
            with pytest.raises(HTTPException) as refused:
                await admin_reset_data(db=db_session)
        assert "Action Center refresh" in refused.value.detail
        reset.assert_not_called()

    async def test_runs_when_nothing_writes(self, db_session):
        from app.api.admin import admin_reset_data

        with patch("app.database.reset_all_data", return_value={"senators": 2}) as reset:
            result = await admin_reset_data(db=db_session)
        reset.assert_called_once()
        assert result["rowsDeleted"] == 2
