"""Tests for the stock-trade read paths (senator_service, representative_service)
and the StockTradeSchema's derived `late` flag.
"""

from app.models import RepStockTrade, Representative, Senator, StockTrade
from app.schemas import StockTradeSchema
from app.services.representative_service import get_rep_stock_trades
from app.services.senator_service import get_senator_stock_trades


def _senator(db_session, id="S1", name="Jane Smith") -> Senator:
    s = Senator(id=id, name=name, state="CA", party="D", is_current=True)
    db_session.add(s)
    db_session.commit()
    return s


def _rep(db_session, id="R1", name="John Doe") -> Representative:
    r = Representative(id=id, name=name, state="TX", district=1, party="R", is_current=True)
    db_session.add(r)
    db_session.commit()
    return r


def _trade(senator_id, days_to_disclose=10, ticker="AAPL", transaction_date="2026-01-01") -> StockTrade:
    return StockTrade(
        senator_id=senator_id,
        ticker=ticker,
        asset_name=f"{ticker} Inc.",
        owner="self",
        transaction_type="purchase",
        transaction_date=transaction_date,
        disclosure_date="2026-01-11",
        days_to_disclose=days_to_disclose,
        amount_low=1001.0,
        amount_high=15000.0,
        industry="TECH",
        source_url="https://example.com/ptr.pdf",
        filing_id="F1",
        parse_confidence="text",
    )


def test_stock_trade_schema_late_flag_derived():
    on_time = StockTradeSchema(
        ticker="AAPL", assetName="Apple Inc.", owner="self", transactionType="purchase",
        transactionDate="2026-01-01", disclosureDate="2026-01-10", daysToDisclose=9,
        amountLow=1001, amountHigh=15000, industry="TECH", sourceUrl="https://x", parseConfidence="text",
    )
    late = StockTradeSchema(
        ticker="AAPL", assetName="Apple Inc.", owner="self", transactionType="purchase",
        transactionDate="2026-01-01", disclosureDate="2026-03-01", daysToDisclose=59,
        amountLow=1001, amountHigh=15000, industry="TECH", sourceUrl="https://x", parseConfidence="text",
    )
    assert on_time.late is False
    assert late.late is True


def test_get_senator_stock_trades_not_found_returns_none(db_session):
    assert get_senator_stock_trades(db_session, "does-not-exist") is None


def test_get_senator_stock_trades_empty(db_session):
    _senator(db_session)
    result = get_senator_stock_trades(db_session, "S1")
    assert result.total == 0
    assert result.trades == []
    assert result.late_count == 0


def test_get_senator_stock_trades_pagination_and_late_count(db_session):
    _senator(db_session)
    for i in range(3):
        db_session.add(_trade("S1", days_to_disclose=10 + i, ticker=f"T{i}"))
    db_session.add(_trade("S1", days_to_disclose=90, ticker="LATE1"))
    db_session.commit()

    result = get_senator_stock_trades(db_session, "S1", page=1, per_page=2)
    assert result.total == 4
    assert result.late_count == 1
    assert len(result.trades) == 2
    assert result.total_pages == 2

    page2 = get_senator_stock_trades(db_session, "S1", page=2, per_page=2)
    assert len(page2.trades) == 2


def test_get_senator_stock_trades_sorted_most_recent_first(db_session):
    _senator(db_session)
    db_session.add(_trade("S1", transaction_date="2026-01-01", ticker="OLD"))
    db_session.add(_trade("S1", transaction_date="2026-06-01", ticker="NEW"))
    db_session.commit()

    result = get_senator_stock_trades(db_session, "S1")
    assert result.trades[0].ticker == "NEW"
    assert result.trades[1].ticker == "OLD"


def test_get_rep_stock_trades_not_found_returns_none(db_session):
    assert get_rep_stock_trades(db_session, "does-not-exist") is None


def test_get_rep_stock_trades_pagination(db_session):
    _rep(db_session)
    db_session.add(RepStockTrade(
        representative_id="R1", ticker="MSFT", asset_name="Microsoft Corp.",
        owner="spouse", transaction_type="sale_full", transaction_date="2026-02-01",
        disclosure_date="2026-04-01", days_to_disclose=59, amount_low=15001.0,
        amount_high=50000.0, industry="TECH", source_url="https://example.com/ptr.pdf",
        filing_id="F2", parse_confidence="ocr",
    ))
    db_session.commit()

    result = get_rep_stock_trades(db_session, "R1")
    assert result["total"] == 1
    assert result["lateCount"] == 1
    assert result["trades"][0]["late"] is True
    assert result["trades"][0]["parseConfidence"] == "ocr"
