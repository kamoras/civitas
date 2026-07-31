"""Tests for president_ptr.py and stock_pipeline._ingest_president.

Network is mocked (no live requests); the 278-T transaction table parse
itself is ptr_common.py's, covered by test_ptr_common.py. What's tested
here is everything specific to the presidential path: picking this
president's periodic transaction reports out of an index that also lists
hundreds of other appointees' filings, refusing to follow a scraped link
off-host, and the ingest's dedupe/classification wiring.

The OGE index markup these fixtures imitate could not be verified against
the live page from the environment this was written in (see the module
docstring's NOT LIVE-VERIFIED note) — the parse is deliberately written
against structure that survives a layout change (anchors plus row text),
and these tests pin that behavior rather than a specific column order.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models import President, PresidentTrade
from app.pipeline.fetch.president_ptr import (
    _parse_index,
    _surname_matches,
    fetch_and_parse_ptr,
    fetch_ptr_filing_index,
)
from app.pipeline.fetch.ptr_common import TradeRow

_BASE = "https://extapps2.oge.gov/201/Presiden.nsf/PAS%20Index?OpenView"

_SAMPLE_INDEX = """
<html><body><table>
  <tr>
    <td>Trump, Donald J.</td><td>President</td>
    <td>Periodic Transaction Report</td><td>11/14/2025</td>
    <td><a href="/201/Presiden.nsf/files/trump-278t-111425.pdf">Download</a></td>
  </tr>
  <tr>
    <td>Trump, Donald J.</td><td>President</td>
    <td>Annual Report (OGE Form 278e)</td><td>06/30/2026</td>
    <td><a href="/201/Presiden.nsf/files/trump-278e-2026.pdf">Download</a></td>
  </tr>
  <tr>
    <td>Zinberg, Joel</td><td>Special Government Employee</td>
    <td>Periodic Transaction Report</td><td>09/03/2025</td>
    <td><a href="/201/Presiden.nsf/files/zinberg-278t-090325.pdf">Download</a></td>
  </tr>
</table></body></html>
"""


class TestIndexParsing:
    def test_keeps_only_this_presidents_periodic_transaction_reports(self):
        filings = _parse_index(_SAMPLE_INDEX, _BASE, "Donald Trump")

        assert len(filings) == 1
        assert filings[0]["doc_id"] == "trump-278t-111425"
        assert filings[0]["filing_date"] == "2025-11-14"
        assert filings[0]["pdf_url"] == (
            "https://extapps2.oge.gov/201/Presiden.nsf/files/trump-278t-111425.pdf"
        )

    def test_annual_278e_is_not_ingested_as_transactions(self):
        """The annual report lists holdings and income in ranges, not
        buy/sell transactions — parsing it into this table would invent
        transactions that were never disclosed."""
        filings = _parse_index(_SAMPLE_INDEX, _BASE, "Donald Trump")
        assert all("278e" not in f["doc_id"] for f in filings)

    def test_another_officials_filing_is_not_attributed_to_the_president(self):
        filings = _parse_index(_SAMPLE_INDEX, _BASE, "Donald Trump")
        assert all("zinberg" not in f["pdf_url"] for f in filings)

    def test_a_different_president_matches_nothing_here(self):
        assert _parse_index(_SAMPLE_INDEX, _BASE, "Joseph Biden") == []

    def test_index_without_the_expected_markup_parses_to_nothing(self):
        assert _parse_index("<html><body>Site under maintenance</body></html>", _BASE, "Donald Trump") == []

    def test_surname_matching_tolerates_the_indexs_lastname_first_format(self):
        assert _surname_matches("Trump, Donald J. President", "Donald Trump") is True
        assert _surname_matches("Trumbull, Lyman", "Donald Trump") is False


class TestFetchIndex:
    @pytest.mark.asyncio
    async def test_zero_parsed_filings_is_not_cached_as_a_real_result(self, db_session):
        """A structural break must not be frozen in as 'no filings' for a
        day — the next run has to try the live page again."""
        resp = MagicMock(status_code=200, text="<html><body>nothing here</body></html>")
        with patch(
            "app.pipeline.fetch.president_ptr.fetch_with_retry_requests",
            new_callable=AsyncMock, return_value=resp,
        ), patch("app.pipeline.fetch.president_ptr.api_cache_set") as mock_cache_set:
            filings = await fetch_ptr_filing_index(db_session, "Donald Trump")

        assert filings == []
        mock_cache_set.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_empty(self, db_session):
        with patch(
            "app.pipeline.fetch.president_ptr.fetch_with_retry_requests",
            new_callable=AsyncMock, return_value=None,
        ):
            assert await fetch_ptr_filing_index(db_session, "Donald Trump") == []


class TestFetchAndParseFiling:
    @pytest.mark.asyncio
    async def test_tags_rows_with_source_and_filing_id(self, db_session):
        filing = {
            "doc_id": "trump-278t-111425",
            "pdf_url": "https://extapps2.oge.gov/201/Presiden.nsf/files/trump-278t-111425.pdf",
        }
        parsed = [TradeRow(
            ticker=None, asset_name="Bitcoin", owner="self",
            transaction_type="purchase", transaction_date="2025-10-30",
            disclosure_date="2025-11-14", amount_low=1000001.0, amount_high=5000000.0,
        )]
        resp = MagicMock(status_code=200, content=b"%PDF-fake")
        with patch(
            "app.pipeline.fetch.president_ptr.fetch_with_retry_requests",
            new_callable=AsyncMock, return_value=resp,
        ), patch(
            "app.pipeline.fetch.president_ptr.parse_pdf_bytes", return_value=(parsed, "text"),
        ):
            rows = await fetch_and_parse_ptr(db_session, filing)

        assert len(rows) == 1
        assert rows[0].source_url == filing["pdf_url"]
        assert rows[0].filing_id == "trump-278t-111425"
        assert rows[0].parse_confidence == "text"

    @pytest.mark.asyncio
    async def test_refuses_a_link_pointing_off_the_allowed_hosts(self, db_session):
        """pdf_url comes from a scraped page, so it's untrusted input."""
        filing = {"doc_id": "evil", "pdf_url": "https://evil.example.com/x.pdf"}
        with patch(
            "app.pipeline.fetch.president_ptr.fetch_with_retry_requests", new_callable=AsyncMock,
        ) as mock_fetch:
            rows = await fetch_and_parse_ptr(db_session, filing)

        assert rows == []
        mock_fetch.assert_not_called()


class TestIngestPresident:
    """stock_pipeline._ingest_president — storage, dedupe, and the
    untickered-asset classification that crypto lines depend on."""

    @staticmethod
    def _seed_president(db_session) -> President:
        president = President(
            id="trump-47", name="Donald Trump", party="R", number=47,
            term_start="2025-01-20", is_current=True,
        )
        db_session.add(president)
        db_session.commit()
        return president

    @staticmethod
    def _rows() -> list[TradeRow]:
        return [
            TradeRow(
                ticker=None, asset_name="Bitcoin", owner="self",
                transaction_type="purchase", transaction_date="2025-10-30",
                disclosure_date="2025-11-14", amount_low=1000001.0, amount_high=5000000.0,
                filing_id="f1", source_url="https://www.whitehouse.gov/x.pdf",
            ),
            TradeRow(
                ticker="AAPL", asset_name="Apple Inc. (AAPL)", owner="spouse",
                transaction_type="sale_full", transaction_date="2025-10-31",
                disclosure_date="2025-11-14", amount_low=15001.0, amount_high=50000.0,
                filing_id="f1", source_url="https://www.whitehouse.gov/x.pdf",
            ),
        ]

    async def _ingest(self, db_session, rows, filings=None):
        from app.pipeline.stock_pipeline import _ingest_president

        with (
            patch(
                "app.pipeline.stock_pipeline.fetch_president_ptr_index",
                new_callable=AsyncMock,
                return_value=filings if filings is not None else [
                    {"doc_id": "f1", "filing_date": "2025-11-14", "pdf_url": "https://www.whitehouse.gov/x.pdf"},
                ],
            ),
            patch(
                "app.pipeline.stock_pipeline.fetch_president_ptr",
                new_callable=AsyncMock, return_value=rows,
            ),
            patch(
                "app.pipeline.stock_pipeline.resolve_tickers",
                new_callable=AsyncMock, return_value={"AAPL": "Apple Inc"},
            ),
            patch(
                "app.pipeline.stock_pipeline.classify_batch_with_learning",
                return_value=({"Apple Inc": "TECH", "Bitcoin": "CRYPTO"}, []),
            ),
        ):
            return await _ingest_president(db_session, AsyncMock())

    async def test_stores_disclosed_transactions_against_the_current_president(self, db_session):
        self._seed_president(db_session)

        inserted = await self._ingest(db_session, self._rows())

        assert inserted == 2
        stored = db_session.query(PresidentTrade).order_by(PresidentTrade.transaction_date).all()
        assert [t.president_id for t in stored] == ["trump-47", "trump-47"]
        assert [t.transaction_type for t in stored] == ["purchase", "sale_full"]
        assert [t.owner for t in stored] == ["self", "spouse"]
        # Ranges as filed — the form reports no single figure and none is invented.
        assert (stored[0].amount_low, stored[0].amount_high) == (1000001.0, 5000000.0)

    async def test_untickered_crypto_is_classified_not_left_unclassified(self, db_session):
        """A ticker-only classification pass leaves every crypto line
        UNCLASSIFIED — virtual currency has no SEC ticker to resolve."""
        self._seed_president(db_session)

        await self._ingest(db_session, self._rows())

        by_asset = {t.asset_name: t.industry for t in db_session.query(PresidentTrade).all()}
        assert by_asset["Bitcoin"] == "CRYPTO"
        assert by_asset["Apple Inc. (AAPL)"] == "TECH"

    async def test_disclosure_timeliness_is_computed_from_the_filed_dates(self, db_session):
        self._seed_president(db_session)

        await self._ingest(db_session, self._rows())

        trade = db_session.query(PresidentTrade).filter_by(asset_name="Bitcoin").one()
        assert trade.days_to_disclose == 15  # 2025-10-30 -> 2025-11-14

    async def test_an_already_ingested_filing_is_not_stored_twice(self, db_session):
        self._seed_president(db_session)

        assert await self._ingest(db_session, self._rows()) == 2
        assert await self._ingest(db_session, self._rows()) == 0
        assert db_session.query(PresidentTrade).count() == 2

    async def test_no_current_president_row_is_a_no_op(self, db_session):
        assert await self._ingest(db_session, self._rows()) == 0
        assert db_session.query(PresidentTrade).count() == 0

    async def test_a_filing_that_parses_to_nothing_stores_nothing(self, db_session):
        self._seed_president(db_session)

        assert await self._ingest(db_session, []) == 0
        assert db_session.query(PresidentTrade).count() == 0


class TestTradesEndpoint:
    """/api/presidents/{id}/stock-trades — called directly with db_session,
    the same convention as test_elections_api.py (no TestClient harness
    exists in this suite)."""

    @staticmethod
    def _seed(db_session, *, trades: int = 0) -> President:
        president = President(
            id="trump-47", name="Donald Trump", party="R", number=47,
            term_start="2025-01-20", is_current=True,
        )
        db_session.add(president)
        for i in range(trades):
            db_session.add(PresidentTrade(
                president_id="trump-47", ticker=None, asset_name=f"Bitcoin {i}",
                owner="self", transaction_type="purchase",
                transaction_date=f"2025-11-{i + 1:02d}", disclosure_date="2025-12-30",
                days_to_disclose=60 - i, amount_low=1000001.0, amount_high=5000000.0,
                industry="CRYPTO", source_url="https://www.whitehouse.gov/x.pdf",
                filing_id="f1",
            ))
        db_session.commit()
        return president

    def test_unknown_president_is_a_404_not_an_empty_list(self, db_session):
        from app.api import presidents

        with pytest.raises(HTTPException) as exc:
            presidents.get_trades("nobody-99", 1, 15, db_session)
        assert exc.value.status_code == 404

    def test_returns_disclosed_ranges_and_flags_late_filings(self, db_session):
        from app.api import presidents

        self._seed(db_session, trades=2)
        body = json.loads(presidents.get_trades("trump-47", 1, 15, db_session).body)

        assert body["total"] == 2
        # 60 and 59 days out — both past the 45-day statutory deadline.
        assert body["lateCount"] == 2
        assert all(t["late"] for t in body["trades"])
        assert body["trades"][0]["amountLow"] == 1000001.0
        assert body["trades"][0]["amountHigh"] == 5000000.0
        # Nothing resembling a profit/gain is exposed — the form has none.
        assert not any(
            key in body["trades"][0] for key in ("profit", "gain", "return", "pnl")
        )

    def test_a_president_with_no_filings_returns_an_empty_page_not_a_404(self, db_session):
        from app.api import presidents

        self._seed(db_session)
        body = json.loads(presidents.get_trades("trump-47", 1, 15, db_session).body)

        assert body["total"] == 0
        assert body["trades"] == []
