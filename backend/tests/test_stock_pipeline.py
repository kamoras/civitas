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

from app.models import HousePipelineRun, PipelineRun, PipelineStatus, Senator, StockTrade, StockTradesPipelineRun
from app.pipeline import stock_pipeline
from app.pipeline.fetch.ptr_common import TradeRow
from app.pipeline.fetch.senate_ptr import report_version
from app.pipeline.run_tracker import PipelineRunTracker
from app.time_utils import utcnow


@pytest.fixture(autouse=True)
def _reset_running_flag(monkeypatch):
    """A fresh in-process tracker for each test, the module's own put back
    after. (This fixture used to assign two module globals the tracker
    replaced — creating them, resetting nothing.)"""
    monkeypatch.setattr(stock_pipeline, "_tracker", PipelineRunTracker())


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
            if isinstance(result, Exception) or callable(result):
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
        seen_during = []

        def house(*_args, **_kwargs):
            seen_during.append(stock_pipeline.is_stock_pipeline_running())
            return 0

        _run(db_session, house_result=house, senate_result=0)
        assert seen_during == [True]
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

    def test_trade_steps_are_every_non_holdings_step(self):
        assert stock_pipeline.TRADE_STEPS == ("house_ptr", "senate_ptr", "president_ptr")

    def test_holdings_failing_alone_leaves_the_trades_run_completed(self, db_session):
        """One holdings phase failing alerts for that phase — one chamber
        failing night after night would otherwise go unseen, since the run
        stays COMPLETED on the trades' account."""
        result = _run(
            db_session, house_result=2, senate_result=1,
            house_holdings_result=RuntimeError("House Clerk down"), senate_holdings_result=40,
        )

        assert result["status"] == "completed"
        assert result["_holdings_alerts"] == 1
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

    @pytest.mark.parametrize("model, age, blocks", [
        pytest.param(HousePipelineRun, timedelta(minutes=5), True, id="recent_running_house_row_blocks"),
        pytest.param(PipelineRun, timedelta(hours=13), False, id="stale_running_senate_row_does_not_block"),
        pytest.param(HousePipelineRun, timedelta(hours=13), False, id="stale_running_house_row_does_not_block"),
    ])
    def test_running_row_by_age(self, db_session, model, age, blocks):
        db_session.add(model(started_at=utcnow() - age, status=PipelineStatus.RUNNING))
        db_session.commit()
        assert stock_pipeline._other_pipeline_running(db_session) is blocks

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


class TestClassifyRowsIndustry:
    """Trade industry comes from structured metadata only: the filer's own
    crypto asset type, else the SIC code the SEC assigned the issuer (by
    ticker, else by exact company name). The donor-name embedding guess it
    replaced put Broadcom under LOBBYISTS and GitLab under PRIVATE_PRISON
    (production, 2026-09-29)."""

    @staticmethod
    def _row(asset_name, ticker=None, asset_type=None):
        return TradeRow(
            ticker=ticker, asset_name=asset_name, owner="self", transaction_type="purchase",
            transaction_date="2026-01-01", disclosure_date="2026-01-15",
            amount_low=1001.0, amount_high=15000.0, asset_type=asset_type,
        )

    async def _classify(self, db_session, rows, by_ticker=None, by_name=None):
        with patch(
            "app.pipeline.stock_pipeline.issuer_industries", new_callable=AsyncMock,
            return_value=(by_ticker or {}, by_name or {}),
        ) as issuers:
            await stock_pipeline._classify_rows_industry(db_session, AsyncMock(), rows)
        return issuers.call_args.args[2:]

    async def test_a_tickered_row_takes_its_issuers_sec_industry(self, db_session):
        rows = [self._row("Broadcom Inc. - Common Stock\n(AVGO) [ST]", ticker="AVGO")]
        await self._classify(db_session, rows, by_ticker={"AVGO": "TECH"})
        assert rows[0].industry == "TECH"

    async def test_an_untickered_row_is_matched_by_its_cleaned_name(self, db_session):
        # The House code, the ticker parenthetical and the line breaks are
        # not part of the issuer's name.
        rows = [self._row("Allstate\nCorp [ST]")]
        tickers, names = await self._classify(db_session, rows, by_name={"Allstate Corp": "INSURANCE"})
        assert names == ["Allstate Corp"]
        assert rows[0].industry == "INSURANCE"

    async def test_a_row_the_sec_does_not_know_stays_unclassified(self, db_session):
        rows = [self._row("US Treasury Bill [GS]"), self._row("123 Main St Rental LLC")]
        await self._classify(db_session, rows)
        assert [r.industry for r in rows] == [None, None]  # UNCLASSIFIED at the DB layer

    async def test_an_sec_code_with_no_category_of_ours_stays_unclassified(self, db_session):
        rows = [self._row("Waste Management (WM) [ST]", ticker="WM")]
        await self._classify(db_session, rows, by_ticker={"WM": None})
        assert rows[0].industry is None

    async def test_declared_crypto_is_crypto(self, db_session):
        rows = [self._row("Bitcoin [CT]"), self._row("Ethereum", asset_type="Cryptocurrency")]
        await self._classify(db_session, rows)
        assert [r.industry for r in rows] == ["CRYPTO", "CRYPTO"]

    async def test_an_undeclared_coin_name_is_not_guessed_crypto(self, db_session):
        # All 18 Senate CRYPTO rows the name embedding produced were
        # municipal bonds or ETF options.
        rows = [self._row("Port of Seattle Washington Revenue Bond", asset_type="Municipal Security")]
        await self._classify(db_session, rows)
        assert rows[0].industry is None


class TestReclassifyStoredTrades:
    """Stored trades take the current classification every night, whatever
    parser read them: the re-read that would otherwise refresh them is
    rationed and skips filings that don't read."""

    async def test_stored_labels_are_brought_up_to_date(self, db_session):
        from app.models import Representative, RepStockTrade, Senator, StockTrade

        db_session.add(Senator(id="S1", name="Jane Doe", state="TX", party="R"))
        db_session.add(Representative(id="R1", name="John Roe", state="IN", district=6, party="R"))
        common = dict(owner="self", transaction_type="purchase", transaction_date="2026-01-02",
                      disclosure_date="2026-01-20", filing_id="f")
        db_session.add_all([
            # Production labels, 2026-09-29.
            RepStockTrade(representative_id="R1", ticker="AVGO", industry="LOBBYISTS",
                          asset_name="Broadcom Inc. - Common Stock\n(AVGO) [ST]", **common),
            RepStockTrade(representative_id="R1", ticker=None, industry="LOBBYISTS",
                          asset_name="US Treasury Bill [GS]", **common),
            RepStockTrade(representative_id="R1", ticker=None, industry="UNCLASSIFIED",
                          asset_name="Bitcoin [CT]", **common),
            StockTrade(senator_id="S1", ticker=None, industry="CRYPTO", asset_type="Municipal Security",
                       asset_name="Port of Seattle Washington Revenue Bond", **common),
            StockTrade(senator_id="S1", ticker=None, industry="UNCLASSIFIED", asset_type="Cryptocurrency",
                       asset_name="Ethereum", **common),
        ])
        db_session.commit()

        with patch.object(stock_pipeline, "issuer_industries", new_callable=AsyncMock,
                          return_value=({"AVGO": "TECH"}, {})):
            changed = await stock_pipeline._reclassify_stored_trades(db_session, AsyncMock())

        assert changed == 5
        assert {t.asset_name: t.industry for t in db_session.query(RepStockTrade)} == {
            "Broadcom Inc. - Common Stock\n(AVGO) [ST]": "TECH",
            "US Treasury Bill [GS]": "UNCLASSIFIED",
            "Bitcoin [CT]": "CRYPTO",
        }
        assert {t.asset_name: t.industry for t in db_session.query(StockTrade)} == {
            "Port of Seattle Washington Revenue Bond": "UNCLASSIFIED",
            "Ethereum": "CRYPTO",
        }


    async def test_an_unreadable_sec_changes_no_label(self, db_session):
        from app.models import Representative, RepStockTrade
        from app.pipeline.fetch.sec_tickers import SecUnavailable

        db_session.add(Representative(id="R1", name="John Roe", state="IN", district=6, party="R"))
        db_session.add(RepStockTrade(
            representative_id="R1", ticker="AVGO", industry="TECH", asset_name="Broadcom (AVGO) [ST]",
            owner="self", transaction_type="purchase", transaction_date="2026-01-02",
            disclosure_date="2026-01-20", filing_id="f",
        ))
        db_session.commit()

        with patch.object(stock_pipeline, "issuer_industries", new_callable=AsyncMock,
                          side_effect=SecUnavailable("down")), pytest.raises(SecUnavailable):
            await stock_pipeline._reclassify_stored_trades(db_session, AsyncMock())

        assert db_session.query(RepStockTrade).one().industry == "TECH"


class TestRereadTrades:
    """Stored filings an older PTR parser read are read again from their
    stored URLs; a filing that doesn't read keeps its rows and waits a week."""

    def _stored(self, db_session, filing_id, url, version=1, owner="self", confidence="text"):
        if db_session.get(Senator, "S1") is None:
            db_session.add(Senator(id="S1", name="Jane Doe", state="TX", party="R"))
        db_session.add(StockTrade(
            senator_id="S1", asset_name="Apple Inc.", owner=owner, transaction_type="purchase",
            transaction_date="2026-01-02", disclosure_date="2026-01-20", amount_low=1001.0, amount_high=15000.0,
            source_url=url, filing_id=filing_id, parser_version=version, parse_confidence=confidence,
        ))
        db_session.commit()

    @staticmethod
    def _row(filing_id, url, owner="spouse"):
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

    async def test_a_scan_that_now_reads_nothing_loses_its_older_reading(self, db_session):
        """Filing 9116328's version-2 OCR rows included a trade "made" on
        2033-11-15 — a bond's maturity, from an asset text of
        'S¥ 11/15/33 MN 08/31/26)'. The current reader finds no row it can
        stand behind; keeping the old reading on that answer kept the
        fabrication forever. A fetch that fails (None) still keeps it, and so
        does a text filing that reads nothing (the parser failing)."""
        from app.models import StockTrade

        base = "https://efdsearch.senate.gov/search/view/paper"
        self._stored(db_session, "scan-empty", f"{base}/scan-empty/", confidence="ocr")
        self._stored(db_session, "scan-down", f"{base}/scan-down/", confidence="ocr")
        self._stored(db_session, "text-empty", f"{base}/text-empty/")

        async def fetch(_client, _db, filing):
            return None if "scan-down" in filing["report_url"] else []

        count, _ = await self._reread(db_session, fetch)

        assert count == 1
        assert {t.filing_id for t in db_session.query(StockTrade)} == {"scan-down", "text-empty"}

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


    async def test_a_backlog_in_one_source_leaves_the_next_its_share(self, db_session):
        """Every source gets an equal part of what is left: a Senate backlog
        whose filings each take 40% of the night stops after one, and the
        president's filings, last in the order, are still read."""
        from app.models import President, PresidentTrade

        for fid in "abc":
            self._stored(db_session, fid, f"https://efdsearch.senate.gov/search/view/ptr/{fid}/")
        db_session.add(President(id="p-1", name="Test President", party="R", number=99,
                                 term_start="2025-01-20", is_current=True))
        db_session.add(PresidentTrade(
            president_id="p-1", asset_name="Bitcoin", owner="self", transaction_type="purchase",
            transaction_date="2025-11-01", disclosure_date="2025-12-30",
            source_url="https://example.test/p.pdf", filing_id="p", parser_version=1,
        ))
        db_session.commit()
        budget = stock_pipeline.PTR_REREAD_BUDGET.total_seconds()
        clock = [0.0]

        async def slow_fetch(_client, _db, filing):
            clock[0] += 0.4 * budget
            return [self._row(filing["report_url"].rstrip("/")[-1], filing["report_url"])]

        president_read = AsyncMock(return_value=[self._row("p", "https://example.test/p.pdf")])
        with patch.object(stock_pipeline.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(stock_pipeline, "_read_president_filing", president_read):
            count, mock_fetch = await self._reread(db_session, slow_fetch)

        assert mock_fetch.call_count == 1
        president_read.assert_awaited_once()
        assert count == 2


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


def test_a_trade_two_reports_list_counts_once_at_its_first_disclosure(db_session):
    db_session.add(Senator(id="S1", name="Sen One", state="CA", party="D"))
    trade = dict(senator_id="S1", ticker="CVX", asset_name="Chevron", owner="self", transaction_type="purchase",
                 transaction_date="2026-07-02", days_to_disclose=0, amount_low=1001.0, amount_high=15000.0,
                 industry="OIL_GAS", source_url="https://example.com")
    db_session.add_all([
        StockTrade(**trade, filing_id="later", disclosure_date="2026-08-20"),
        StockTrade(**trade, filing_id="first", disclosure_date="2026-08-17"),
        # Two same-day lots in one report stay two.
        StockTrade(**{**trade, "asset_name": "Ecolab"}, filing_id="first", disclosure_date="2026-08-17"),
        StockTrade(**{**trade, "asset_name": "Ecolab"}, filing_id="first", disclosure_date="2026-08-17"),
    ])
    db_session.commit()
    assert stock_pipeline.collapse_refiled_trades(db_session, StockTrade, "senator_id") == 1
    assert sorted((t.asset_name, t.filing_id) for t in db_session.query(StockTrade)) == [
        ("Chevron", "first"), ("Ecolab", "first"), ("Ecolab", "first")]
    assert stock_pipeline.collapse_refiled_trades(db_session, StockTrade, "senator_id") == 0


@pytest.mark.parametrize("title,version", [
    ("Periodic Transaction Report for 11/15/2024 (Amendment 2)", ("2024-11-15", 2)),
    ("Periodic Transaction Report for 09/11/2026", ("2026-09-11", 0)),
    ("Something else", (None, 0)),
])
def test_an_efd_title_names_its_report_and_amendment(title, version):
    assert report_version(title) == version


def test_an_amended_report_keeps_its_newest_version_at_first_disclosure(db_session):
    db_session.add(Senator(id="S1", name="Sen One", state="CA", party="D"))
    base = dict(senator_id="S1", ticker=None, owner="self", transaction_type="purchase",
                transaction_date="2024-10-29", days_to_disclose=0, amount_low=1001.0, amount_high=15000.0,
                industry="TECH", source_url="https://example.com")
    db_session.add_all([
        StockTrade(**base, asset_name="Acme", filing_id="orig", disclosure_date="2024-11-15"),
        StockTrade(**{**base, "amount_high": 50000.0}, asset_name="Acme", filing_id="amend", disclosure_date="2026-08-05"),
        StockTrade(**base, asset_name="Added Later", filing_id="amend", disclosure_date="2026-08-05"),
    ])
    db_session.commit()
    filings = [
        {"last": "One", "first": "Sen", "title": "Periodic Transaction Report for 11/15/2024",
         "report_url": "https://efd/view/ptr/orig/", "filed_date": "2024-11-15"},
        {"last": "One", "first": "Sen", "title": "Periodic Transaction Report for 11/15/2024 (Amendment 1)",
         "report_url": "https://efd/view/ptr/amend/", "filed_date": "2026-08-05"},
    ]
    assert stock_pipeline.settle_amended_reports(db_session, filings) == 1
    rows = {t.asset_name: (t.filing_id, t.disclosure_date, t.amount_high) for t in db_session.query(StockTrade)}
    # The corrected amount stands, at the date the trade was first disclosed;
    # the trade the amendment added was first disclosed by the amendment.
    assert rows == {"Acme": ("amend", "2024-11-15", 50000.0), "Added Later": ("amend", "2026-08-05", 15000.0)}
    assert stock_pipeline._marked(db_session, stock_pipeline._SUPERSEDED_KEY.format("orig"))


@pytest.mark.asyncio
async def test_the_president_s_annual_report_rows_are_never_reread(db_session):
    """They are president_fd's: the 278-T parser the re-read runs would
    replace them with whatever it made of a 278e (21,285 rows at stake)."""
    from app.models import President, PresidentTrade

    db_session.add(President(id="p-1", name="Test President", party="R", number=99,
                             term_start="2025-01-20", is_current=True))
    common = dict(president_id="p-1", owner="self", transaction_type="purchase",
                  transaction_date="2025-11-01", disclosure_date="2026-05-15", parser_version=1)
    db_session.add_all([
        PresidentTrade(asset_name="Annual holding", source_url="https://example.test/annual.pdf",
                       filing_id="annual", report_kind="annual", **common),
        PresidentTrade(asset_name="Periodic trade", source_url="https://example.test/p.pdf",
                       filing_id="p", report_kind="periodic", **common),
    ])
    db_session.commit()
    read = AsyncMock(return_value=[])
    with patch.object(stock_pipeline, "_read_president_filing", read), \
         patch.object(stock_pipeline, "senate_accept_terms", new_callable=AsyncMock, return_value="tok"):
        await stock_pipeline._reread_trades(db_session, None)
    assert [c.args[1]["doc_id"] for c in read.await_args_list] == ["p"]
