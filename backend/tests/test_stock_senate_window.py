"""The Senate PTR search re-covers a trailing window every run.

It used to start at the newest disclosure_date already stored, so a filing
skipped on an earlier run — filer not yet matched to a senator (a newly
seated member), a failed detail fetch, an empty parse — was never searched
again once any later filing had been ingested.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

from app.models import Senator, StockTrade
from app.pipeline import stock_pipeline
from app.pipeline.fetch.ptr_common import TradeRow


def _trade(db, filing_id, disclosure):
    db.add(StockTrade(
        senator_id="s-1", asset_name="X", owner="Self", transaction_type="purchase",
        transaction_date=disclosure, disclosure_date=disclosure, days_to_disclose=0,
        amount_low=1001, amount_high=15000, industry="UNCLASSIFIED",
        source_url="u", filing_id=filing_id, parse_confidence=1.0,
    ))


def _senator(db, sid, name):
    db.add(Senator(id=sid, bioguide_id=sid, name=name, state="OH", party="R", is_current=True))


class TestSearchWindow:
    def test_cold_start_uses_the_lookback(self, db_session):
        with patch("app.pipeline.stock_pipeline.utcnow", lambda: datetime(2026, 9, 24)):
            assert stock_pipeline._senate_search_since(db_session) == "2026-05-27"  # 120 days

    def test_re_covers_the_revisit_window_before_the_newest_filing(self, db_session):
        _senator(db_session, "s-1", "Jane Smith")
        _trade(db_session, "f1", "2026-09-20")
        _trade(db_session, "f0", "2026-03-01")
        db_session.commit()
        assert stock_pipeline._senate_search_since(db_session) == "2026-06-22"  # 90 days before


class TestSkippedFilingIsRetried:
    def test_filing_skipped_before_its_senator_existed_is_ingested_later(self, db_session):
        # Ann Lee's Sept 10 filing was found on an earlier run but skipped:
        # she wasn't in the roster yet. Jane Smith's later Sept 20 filing
        # was stored, so the old window (start at the newest stored date)
        # would never search Sept 10 again. Ann has since been seated.
        _senator(db_session, "s-1", "Jane Smith")
        _trade(db_session, "jane-sep-20", "2026-09-20")
        db_session.commit()
        _senator(db_session, "s-2", "Ann Lee")  # seated after her filing was first seen
        db_session.commit()

        filings = [{"last": "Lee", "first": "Ann", "filed_date": "2026-09-10",
                    "report_url": "https://efd/ptr/ann-sep-10/", "is_paper": False}]
        seen_since = []

        async def search(since):
            seen_since.append(since)
            return [f for f in filings if f["filed_date"] >= since]

        row = TradeRow(
            ticker="ABC", asset_name="Abc Corp", owner="Self", transaction_type="purchase",
            transaction_date="2026-09-01", disclosure_date="2026-09-10",
            amount_low=1001, amount_high=15000,
        )
        row.filing_id, row.source_url, row.parse_confidence = "ann-sep-10", "u", 1.0

        with patch.object(stock_pipeline, "senate_accept_terms", AsyncMock(return_value="tok")), \
             patch.object(stock_pipeline, "search_ptr_filings", side_effect=search), \
             patch.object(stock_pipeline, "fetch_senate_ptr", AsyncMock(return_value=[row])), \
             patch.object(stock_pipeline, "_classify_rows_industry", AsyncMock()):
            inserted = asyncio.run(stock_pipeline._ingest_senate(db_session, client=None))

        # The old window started at 2026-09-20 and would have missed it.
        assert seen_since == ["2026-06-22"]
        assert inserted == 1
        assert db_session.query(StockTrade).filter_by(senator_id="s-2").count() == 1

    def test_already_ingested_filings_are_not_refetched(self, db_session):
        _senator(db_session, "s-1", "Jane Smith")
        _trade(db_session, "jane-sep-20", "2026-09-20")
        db_session.commit()
        fetch = AsyncMock(return_value=[])
        filings = [{"last": "Smith", "first": "Jane", "filed_date": "2026-09-20",
                    "report_url": "https://efd/ptr/jane-sep-20/", "is_paper": False}]
        with patch.object(stock_pipeline, "senate_accept_terms", AsyncMock(return_value="tok")), \
             patch.object(stock_pipeline, "search_ptr_filings", AsyncMock(return_value=filings)), \
             patch.object(stock_pipeline, "fetch_senate_ptr", fetch):
            asyncio.run(stock_pipeline._ingest_senate(db_session, client=None))
        fetch.assert_not_called()
