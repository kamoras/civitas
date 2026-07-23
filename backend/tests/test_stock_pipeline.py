"""Tests for run_stock_trades_pipeline's admin-visible run tracking.

StockTradesPipelineRun mirrors HousePipelineRun (id, started_at,
completed_at, status, elapsed_seconds, error_message) so the admin
dashboard can show stock-trades runs the same way it already shows
Senate/House runs — previously this pipeline had no persisted run record
and no in-memory "is it actually running" flag at all, making it
impossible to tell a slow run from a stuck one (surfaced live 2026-07-15,
when a run took ~90 minutes with only CPU usage as a diagnostic signal).
"""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.models import HousePipelineRun, PipelineRun, PipelineStatus, StockTradesPipelineRun
from app.pipeline import stock_pipeline
from app.time_utils import utcnow


@pytest.fixture(autouse=True)
def _reset_running_flag():
    stock_pipeline._stock_pipeline_running = False
    stock_pipeline._stock_pipeline_started_at = None
    yield
    stock_pipeline._stock_pipeline_running = False
    stock_pipeline._stock_pipeline_started_at = None


def _run(db_session, house_result=None, senate_result=None):
    with patch("app.pipeline.stock_pipeline.SessionLocal", return_value=db_session), \
         patch("app.pipeline.stock_pipeline._other_pipeline_running", return_value=False), \
         patch("app.pipeline.stock_pipeline._ingest_house", new_callable=AsyncMock) as mock_house, \
         patch("app.pipeline.stock_pipeline._ingest_senate", new_callable=AsyncMock) as mock_senate:
        if isinstance(house_result, Exception):
            mock_house.side_effect = house_result
        else:
            mock_house.return_value = house_result if house_result is not None else 0
        if isinstance(senate_result, Exception):
            mock_senate.side_effect = senate_result
        else:
            mock_senate.return_value = senate_result if senate_result is not None else 0

        import asyncio
        return asyncio.run(stock_pipeline.run_stock_trades_pipeline())


class TestStockTradesPipelineRunTracking:
    def test_creates_a_run_row_and_marks_it_completed(self, db_session):
        result = _run(db_session, house_result=5, senate_result=3)

        assert result["status"] == "completed"
        assert result["house_trades"] == 5
        assert result["senate_trades"] == 3

        run = db_session.query(StockTradesPipelineRun).one()
        assert run.status == "completed"
        assert run.house_trades_ingested == 5
        assert run.senate_trades_ingested == 3
        assert run.completed_at is not None
        assert run.elapsed_seconds is not None

    def test_in_memory_flag_is_set_during_and_cleared_after(self, db_session):
        assert stock_pipeline.is_stock_pipeline_running() is False
        _run(db_session, house_result=0, senate_result=0)
        # Cleared by the finally block once the (synchronous, in this
        # test) run completes.
        assert stock_pipeline.is_stock_pipeline_running() is False

    def test_one_chamber_failing_does_not_block_the_other(self, db_session):
        """Best-effort per chamber, per the module's own docstring."""
        result = _run(db_session, house_result=RuntimeError("House PTR site down"), senate_result=7)

        assert result["status"] == "completed"
        assert result["senate_trades"] == 7

        run = db_session.query(StockTradesPipelineRun).one()
        assert run.status == "completed"
        assert run.senate_trades_ingested == 7
        assert "House" in (run.error_message or "")

    def test_both_chambers_failing_marks_run_failed(self, db_session):
        result = _run(
            db_session,
            house_result=RuntimeError("House PTR site down"),
            senate_result=RuntimeError("Senate session expired"),
        )

        assert result["status"] == "failed"

        run = db_session.query(StockTradesPipelineRun).one()
        assert run.status == "failed"
        assert "House" in run.error_message
        assert "Senate" in run.error_message

    def test_skips_when_stocks_own_prior_run_is_still_genuinely_active(self, db_session):
        db_session.add(StockTradesPipelineRun(started_at=utcnow() - timedelta(minutes=5), status=PipelineStatus.RUNNING))
        db_session.commit()

        with patch("app.pipeline.stock_pipeline.SessionLocal", return_value=db_session), \
             patch("app.pipeline.stock_pipeline._other_pipeline_running", return_value=False):
            import asyncio
            result = asyncio.run(stock_pipeline.run_stock_trades_pipeline())

        assert result == {"status": "skipped", "reason": "already_running"}
        assert db_session.query(StockTradesPipelineRun).count() == 1

    def test_skips_and_creates_no_row_when_a_member_pipeline_is_running(self, db_session):
        with patch("app.pipeline.stock_pipeline.SessionLocal", return_value=db_session), \
             patch("app.pipeline.stock_pipeline._other_pipeline_running", return_value=True):
            import asyncio
            result = asyncio.run(stock_pipeline.run_stock_trades_pipeline())

        assert result["status"] == "skipped"
        assert db_session.query(StockTradesPipelineRun).count() == 0
        assert stock_pipeline.is_stock_pipeline_running() is False


class TestOtherPipelineRunningStaleness:
    """_other_pipeline_running gained staleness awareness 2026-07-23:
    previously a row orphaned by a killed process (a deploy restarting
    the container mid-run) stayed status=running forever, permanently
    blocking Stock via this check with no auto-clear anywhere in it —
    confirmed live as the likely cause of stock-trades data going stale
    for 4+ days after a since-fixed deploy-race incident.
    """

    def test_recent_running_senate_row_blocks(self, db_session):
        db_session.add(PipelineRun(started_at=utcnow() - timedelta(minutes=5), status=PipelineStatus.RUNNING))
        db_session.commit()
        assert stock_pipeline._other_pipeline_running(db_session) is True

    def test_recent_running_house_row_blocks(self, db_session):
        db_session.add(HousePipelineRun(started_at=utcnow() - timedelta(minutes=5), status=PipelineStatus.RUNNING))
        db_session.commit()
        assert stock_pipeline._other_pipeline_running(db_session) is True

    def test_stale_running_senate_row_does_not_block(self, db_session):
        db_session.add(PipelineRun(started_at=utcnow() - timedelta(hours=13), status=PipelineStatus.RUNNING))
        db_session.commit()
        assert stock_pipeline._other_pipeline_running(db_session) is False

    def test_stale_running_house_row_does_not_block(self, db_session):
        db_session.add(HousePipelineRun(started_at=utcnow() - timedelta(hours=13), status=PipelineStatus.RUNNING))
        db_session.commit()
        assert stock_pipeline._other_pipeline_running(db_session) is False

    def test_no_running_rows_does_not_block(self, db_session):
        assert stock_pipeline._other_pipeline_running(db_session) is False
