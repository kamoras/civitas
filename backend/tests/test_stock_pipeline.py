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


def _run(
    db_session, house_result=None, senate_result=None, president_result=None,
    house_holdings_result=None, senate_holdings_result=None,
):
    with patch("app.pipeline.stock_pipeline.SessionLocal", return_value=db_session), \
         patch("app.pipeline.stock_pipeline._other_pipeline_running", return_value=False), \
         patch("app.pipeline.stock_pipeline._ingest_house", new_callable=AsyncMock) as mock_house, \
         patch("app.pipeline.stock_pipeline._ingest_senate", new_callable=AsyncMock) as mock_senate, \
         patch("app.pipeline.stock_pipeline._ingest_president", new_callable=AsyncMock) as mock_president, \
         patch("app.pipeline.holdings_pipeline.ingest_house_holdings", new_callable=AsyncMock) as mock_house_h, \
         patch("app.pipeline.holdings_pipeline.ingest_senate_holdings", new_callable=AsyncMock) as mock_senate_h, \
         patch("app.pipeline.holdings_pipeline.send_ops_alert") as mock_alert:
        for mock, result in (
            (mock_house, house_result), (mock_senate, senate_result), (mock_president, president_result),
            (mock_house_h, house_holdings_result), (mock_senate_h, senate_holdings_result),
        ):
            if isinstance(result, Exception):
                mock.side_effect = result
            else:
                mock.return_value = result if result is not None else 0

        import asyncio
        result = asyncio.run(stock_pipeline.run_stock_trades_pipeline())
        result["_holdings_alerts"] = mock_alert.call_count
        return result


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
            house_holdings_result=RuntimeError("House Clerk down"),
            senate_holdings_result=RuntimeError("Senate session expired"),
        )

        assert result["status"] == "failed"

        run = db_session.query(StockTradesPipelineRun).one()
        assert run.status == "failed"
        assert "House" in run.error_message
        assert "Senate" in run.error_message
        assert "President" in run.error_message
        assert "holdings" in run.error_message

    def test_all_trade_phases_failing_fails_the_run_even_when_holdings_succeed(self, db_session):
        """Holdings phases rarely raise, so counting them toward "every phase
        failed" would record a run with a dead trade ingest as COMPLETED —
        and the staleness alert would never fire."""
        result = _run(
            db_session,
            house_result=RuntimeError("shared bug"), senate_result=RuntimeError("shared bug"),
            president_result=RuntimeError("shared bug"),
            house_holdings_result=10, senate_holdings_result=20,
        )
        assert result["status"] == "failed"

    def test_both_holdings_phases_failing_alerts_but_does_not_fail_the_trades_run(self, db_session):
        """A holdings-only outage must not report stock trades as stale (the
        run row is the trades pipeline's); it raises its own ops alert."""
        result = _run(
            db_session, house_result=2, senate_result=1,
            house_holdings_result=RuntimeError("index gone"),
            senate_holdings_result=RuntimeError("search broken"),
        )
        assert result["status"] == "completed"
        assert result["_holdings_alerts"] == 2  # one per failed phase
        run = db_session.query(StockTradesPipelineRun).one()
        assert "House holdings" in run.error_message and "Senate holdings" in run.error_message

    def test_one_holdings_phase_failing_alerts_for_that_phase(self, db_session):
        """One chamber failing night after night would otherwise go unseen:
        the run stays COMPLETED on the trades' account."""
        result = _run(db_session, house_holdings_result=RuntimeError("down"), senate_holdings_result=3)
        assert result["status"] == "completed"
        assert result["_holdings_alerts"] == 1

    def test_trade_steps_are_every_non_holdings_step(self):
        assert stock_pipeline.TRADE_STEPS == ("house_ptr", "senate_ptr", "president_ptr")

    def test_holdings_failing_alone_leaves_the_trades_run_completed(self, db_session):
        result = _run(
            db_session, house_result=2, senate_result=1,
            house_holdings_result=RuntimeError("House Clerk down"), senate_holdings_result=40,
        )

        assert result["status"] == "completed"
        assert result["house_trades"] == 2
        assert result["house_holdings"] == 0
        assert result["senate_holdings"] == 40
        run = db_session.query(StockTradesPipelineRun).one()
        assert "House holdings" in (run.error_message or "")

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
        from app.models import ApiCache
        from tests.conftest import start_senate_run_then_stop_beating

        start_senate_run_then_stop_beating(db_session)
        assert stock_pipeline._other_pipeline_running(db_session) is True
        db_session.query(ApiCache).update({"cached_at": utcnow() - timedelta(hours=2)})
        db_session.commit()
        assert stock_pipeline._other_pipeline_running(db_session) is False  # its lease: dead

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

    @pytest.mark.slow
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


class TestRereadTrades:
    """Stored filings an older PTR parser read are read again from their
    stored URLs; a filing that doesn't read keeps its rows and waits a week."""

    def _stored(self, db_session, filing_id, url, version=1, owner="self"):
        from app.models import Senator, StockTrade

        if db_session.get(Senator, "S1") is None:
            db_session.add(Senator(id="S1", name="Jane Doe", state="TX", party="R"))
        db_session.add(StockTrade(
            senator_id="S1", asset_name="Apple Inc.", owner=owner, transaction_type="purchase",
            transaction_date="2026-01-02", disclosure_date="2026-01-20", amount_low=1001.0, amount_high=15000.0,
            source_url=url, filing_id=filing_id, parser_version=version,
        ))
        db_session.commit()

    @staticmethod
    def _row(filing_id, url, owner="spouse"):
        from app.pipeline.fetch.ptr_common import TradeRow

        return TradeRow(ticker="AAPL", asset_name="Apple Inc.", owner=owner, transaction_type="purchase",
                        transaction_date="2026-01-02", disclosure_date="2026-01-20",
                        amount_low=1001.0, amount_high=15000.0, source_url=url, filing_id=filing_id)

    async def _reread(self, db_session, fetch):
        with patch.object(stock_pipeline, "fetch_senate_ptr", side_effect=fetch) as mock_fetch, \
             patch.object(stock_pipeline, "senate_accept_terms", new_callable=AsyncMock, return_value="tok"), \
             patch.object(stock_pipeline, "_classify_rows_industry", new_callable=AsyncMock):
            count = await stock_pipeline._reread_trades(db_session, None)
        return count, mock_fetch

    async def test_rereads_old_filings_and_keeps_what_does_not_read(self, db_session):
        from app.models import StockTrade
        from app.pipeline.fetch.ptr_common import PARSER_VERSION

        base = "https://efdsearch.senate.gov/search/view"
        self._stored(db_session, "a", f"{base}/ptr/a/")
        self._stored(db_session, "b", f"{base}/ptr/b/")                          # won't load tonight
        self._stored(db_session, "c", f"{base}/ptr/c/", version=PARSER_VERSION)  # already current

        async def fetch(_client, _db, filing):
            # No search result: the page states the filed date, and failing
            # that the one the stored rows carry.
            assert "filed_date" not in filing and filing["stored_filed_date"] == "2026-01-20"
            return [self._row("a", filing["report_url"])] if filing["report_url"].endswith("/a/") else []

        count, mock_fetch = await self._reread(db_session, fetch)

        assert count == 1
        assert sorted(c.args[2]["report_url"][-2] for c in mock_fetch.call_args_list) == ["a", "b"]
        by_filing = {t.filing_id: t for t in db_session.query(StockTrade).all()}
        assert (by_filing["a"].owner, by_filing["a"].parser_version, by_filing["a"].senator_id) == ("spouse", PARSER_VERSION, "S1")
        assert (by_filing["b"].owner, by_filing["b"].parser_version) == ("self", 1)

    async def test_a_filing_that_did_not_read_waits_instead_of_starving_the_rest(self, db_session):
        base = "https://efdsearch.senate.gov/search/view/ptr"
        self._stored(db_session, "a", f"{base}/a/")
        self._stored(db_session, "b", f"{base}/b/")

        async def fetch(_client, _db, filing):
            if filing["report_url"].endswith("/b/"):
                raise RuntimeError("this filing's page is broken")
            return [self._row("a", filing["report_url"])]

        await self._reread(db_session, fetch)            # a reads, b fails and is remembered
        _, mock_fetch = await self._reread(db_session, fetch)
        mock_fetch.assert_not_called()

    async def test_a_night_that_reads_nothing_costs_a_night_not_a_month(self, db_session):
        """An outage, or dead links at the head of the order: either way the
        next night reaches past them — whatever time it runs — and the one
        after tries them again."""
        from datetime import timedelta

        base = "https://efdsearch.senate.gov/search/view/ptr"
        for n in range(stock_pipeline._REREAD_OUTAGE_AFTER + 2):
            self._stored(db_session, f"f{n}", f"{base}/f{n}/")

        async def fetch(_client, _db, filing):
            return []

        def night(hours):
            return patch.object(stock_pipeline, "utcnow", return_value=stock_pipeline.utcnow() + timedelta(hours=hours))

        _, first = await self._reread(db_session, fetch)
        assert first.call_count == stock_pipeline._REREAD_OUTAGE_AFTER  # stopped: looks down
        with night(24 + 6):  # the next night, run late
            _, second = await self._reread(db_session, fetch)
        assert second.call_count == 2  # it reaches the ones behind them
        with night(48 - 6):  # the night after, run early
            _, third = await self._reread(db_session, fetch)
        assert third.call_count == stock_pipeline._REREAD_OUTAGE_AFTER  # tried again

    async def test_a_marker_from_before_retry_times_waits_its_month(self, db_session):
        from app.pipeline.cache import api_cache_set

        self._stored(db_session, "a", "https://efdsearch.senate.gov/search/view/ptr/a/")
        api_cache_set(db_session, stock_pipeline._REREAD_TIER, "failed-Senate-a", {"url": "u"})
        _, fetched = await self._reread(db_session, AsyncMock(return_value=[]))
        fetched.assert_not_called()

    async def test_a_paper_filing_is_fetched_as_one(self, db_session):
        self._stored(db_session, "p", "https://efdsearch.senate.gov/search/view/paper/p/")

        async def fetch(_client, _db, filing):
            assert filing["is_paper"] is True
            return [self._row("p", filing["report_url"], owner="unknown")]

        count, _ = await self._reread(db_session, fetch)
        assert count == 1

    async def test_a_row_without_a_real_filed_date_offers_none(self, db_session):
        """Stored before the filed-date fix, a row's disclosure date is its
        transaction date — not a filed date to fall back on."""
        from app.models import StockTrade

        self._stored(db_session, "a", "https://efdsearch.senate.gov/search/view/ptr/a/")
        db_session.query(StockTrade).update({"disclosure_date": "2026-01-02"})
        db_session.commit()
        seen = []

        async def fetch(_client, _db, filing):
            seen.append(filing["stored_filed_date"])
            return []

        await self._reread(db_session, fetch)
        assert seen == [None]


class TestRereadHouseFiling:
    """A stored House filing is read again with its filing date from the
    Clerk's yearly index: a scan read before PTR PARSER_VERSION 5 stored each
    row's transaction date as its disclosure date, so the rows can't say when
    the filing was filed, and an undated row would have no date to show."""

    async def test_the_filing_date_comes_from_that_years_index(self):
        url = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/20012345.pdf"
        index = [
            {"doc_id": "20099999", "filing_date": "2025-02-01"},
            {"doc_id": "20012345", "filing_date": "2025-06-30"},
        ]
        with patch.object(stock_pipeline, "fetch_ptr_filing_index", new_callable=AsyncMock, return_value=index) as idx, \
             patch.object(stock_pipeline, "fetch_house_ptr", new_callable=AsyncMock, return_value=[]) as fetch:
            await stock_pipeline._reread_house_filing(None, None, "20012345", url)

        assert idx.await_args.args[2] == 2025
        assert fetch.await_args.args[2] == {"doc_id": "20012345", "pdf_url": url, "filing_date": "2025-06-30"}

    async def test_a_filing_missing_from_the_index_is_still_read(self):
        url = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/20012345.pdf"
        with patch.object(stock_pipeline, "fetch_ptr_filing_index", new_callable=AsyncMock, return_value=[]), \
             patch.object(stock_pipeline, "fetch_house_ptr", new_callable=AsyncMock, return_value=[]) as fetch:
            await stock_pipeline._reread_house_filing(None, None, "20012345", url)

        assert fetch.await_args.args[2]["filing_date"] is None
