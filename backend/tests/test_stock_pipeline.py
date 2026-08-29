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


def _run(db_session, house_result=None, senate_result=None, president_result=None):
    with patch("app.pipeline.stock_pipeline.SessionLocal", return_value=db_session), \
         patch("app.pipeline.stock_pipeline._other_pipeline_running", return_value=False), \
         patch("app.pipeline.stock_pipeline._ingest_house", new_callable=AsyncMock) as mock_house, \
         patch("app.pipeline.stock_pipeline._ingest_senate", new_callable=AsyncMock) as mock_senate, \
         patch("app.pipeline.stock_pipeline._ingest_president", new_callable=AsyncMock) as mock_president:
        for mock, result in (
            (mock_house, house_result), (mock_senate, senate_result), (mock_president, president_result),
        ):
            if isinstance(result, Exception):
                mock.side_effect = result
            else:
                mock.return_value = result if result is not None else 0

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
        assert run.president_trades_ingested == 0
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

    def test_every_phase_failing_marks_run_failed(self, db_session):
        result = _run(
            db_session,
            house_result=RuntimeError("House PTR site down"),
            senate_result=RuntimeError("Senate session expired"),
            president_result=RuntimeError("OGE index unreachable"),
        )

        assert result["status"] == "failed"

        run = db_session.query(StockTradesPipelineRun).one()
        assert run.status == "failed"
        assert "House" in run.error_message
        assert "Senate" in run.error_message
        assert "President" in run.error_message

    def test_president_failing_alone_leaves_the_run_completed(self, db_session):
        """Same best-effort-per-phase rule the chambers get: the president's
        source is the newest and least-proven of the three, and its being
        down must not discard the congressional rows this run did ingest."""
        result = _run(
            db_session,
            house_result=4,
            senate_result=6,
            president_result=RuntimeError("OGE index unreachable"),
        )

        assert result["status"] == "completed"
        assert result["house_trades"] == 4
        assert result["president_trades"] == 0

        run = db_session.query(StockTradesPipelineRun).one()
        assert run.status == "completed"
        assert "President" in (run.error_message or "")

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


class TestIngestHouseYearWindow:
    """_ingest_house's current_year (2026-07-23 timezone-consistency
    pass) must come from the project's canonical UTC clock, not a
    local-timezone-dependent date.today() call."""

    async def test_current_year_drives_the_two_years_fetched(self, db_session):
        from datetime import datetime
        from unittest.mock import AsyncMock, patch

        from app.pipeline.stock_pipeline import _ingest_house

        with (
            patch("app.pipeline.stock_pipeline.utcnow", return_value=datetime(2026, 3, 15)),
            patch("app.pipeline.stock_pipeline.fetch_ptr_filing_index", new_callable=AsyncMock, return_value=[]) as mock_fetch,
        ):
            client = AsyncMock()
            result = await _ingest_house(db_session, client)

        assert result == 0
        years_requested = {call.args[2] for call in mock_fetch.call_args_list}
        assert years_requested == {2025, 2026}


class TestIngestSenateColdStartWindow:
    """_ingest_senate's cold-start lookback (no prior disclosure_date in
    the DB) must come from the canonical UTC clock, not a local-
    timezone-dependent date.today() call."""

    async def test_cold_start_since_date_computed_from_canonical_clock(self, db_session):
        from datetime import datetime, timedelta
        from unittest.mock import AsyncMock, patch

        from app.pipeline.stock_pipeline import COLD_START_LOOKBACK_DAYS, _ingest_senate

        with (
            patch("app.pipeline.stock_pipeline.utcnow", return_value=datetime(2026, 3, 15)),
            patch("app.pipeline.stock_pipeline.senate_accept_terms", new_callable=AsyncMock, return_value="csrf-token"),
            patch("app.pipeline.stock_pipeline.search_ptr_filings", new_callable=AsyncMock, return_value=[]) as mock_search,
        ):
            client = AsyncMock()
            result = await _ingest_senate(db_session, client)

        assert result == 0
        expected_since = (datetime(2026, 3, 15) - timedelta(days=COLD_START_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        mock_search.assert_called_once_with(expected_since)


class TestClassifyRowsIndustryUntickered:
    """2026-08: untickered-line classification (crypto has no SEC ticker to
    resolve at all) used to be an opt-in classify_untickered flag, on only
    for presidential 278-Ts — House/Senate untickered lines silently stayed
    UNCLASSIFIED, including their genuinely-disclosed crypto holdings. Now
    unconditional; House/Senate rows get the same treatment as the
    president's already did."""

    async def test_house_style_untickered_crypto_row_gets_classified(self, db_session):
        from app.pipeline.fetch.ptr_common import TradeRow
        from app.pipeline.stock_pipeline import _classify_rows_industry

        rows = [
            TradeRow(
                ticker=None, asset_name="Bitcoin", owner="self",
                transaction_type="purchase", transaction_date="2026-01-01",
                disclosure_date="2026-01-15", amount_low=1001.0, amount_high=15000.0,
            ),
        ]

        await _classify_rows_industry(db_session, AsyncMock(), rows)

        assert rows[0].industry == "CRYPTO"

    async def test_a_confident_other_classification_does_not_overwrite_unclassified(self, db_session):
        # 2026-08 audit (independent review of #445): classify_batch_with_
        # learning always returns an entry per name, including the literal
        # string "OTHER" for names it can't confidently place — it never
        # returns None/absent. An untickered line's asset_name is often a
        # non-tradeable holding (rental property, private partnership)
        # that was never a real classification candidate; writing "OTHER"
        # for it would surface a spurious industry badge in the UI (which
        # only hides for exactly "UNCLASSIFIED", not "OTHER") where none
        # showed before this change made classification unconditional.
        from unittest.mock import patch

        from app.pipeline.fetch.ptr_common import TradeRow
        from app.pipeline.stock_pipeline import _classify_rows_industry

        rows = [
            TradeRow(
                ticker=None, asset_name="123 Main St Rental LLC", owner="self",
                transaction_type="purchase", transaction_date="2026-01-01",
                disclosure_date="2026-01-15", amount_low=1001.0, amount_high=15000.0,
            ),
        ]

        with patch(
            "app.pipeline.stock_pipeline.classify_batch_with_learning",
            return_value=({"123 Main St Rental LLC": "OTHER"}, ["123 Main St Rental LLC"]),
        ):
            await _classify_rows_industry(db_session, AsyncMock(), rows)

        assert rows[0].industry is None  # stays the model default (UNCLASSIFIED at the DB layer)
