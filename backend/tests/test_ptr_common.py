"""Tests for shared PTR parsing helpers (ptr_common.py). Pure functions —
no network, no DB, deterministic.
"""

from app.pipeline.fetch.ptr_common import (
    TradeRow,
    classify_transaction_type,
    normalize_date,
    parse_amount_range,
    parse_table_rows,
)


def test_normalize_date_valid():
    assert normalize_date("1/2/2026") == "2026-01-02"
    assert normalize_date("12/31/2025") == "2025-12-31"


def test_normalize_date_invalid_returns_none():
    assert normalize_date("not a date") is None
    assert normalize_date("") is None


def test_classify_transaction_type():
    assert classify_transaction_type("Purchase") == "purchase"
    assert classify_transaction_type("Sale (Full)") == "sale_full"
    assert classify_transaction_type("Sale (Partial)") == "sale_partial"
    assert classify_transaction_type("Exchange") == "exchange"
    assert classify_transaction_type("something else entirely") is None


def test_parse_amount_range_valid():
    assert parse_amount_range("$1,001 - $15,000") == (1001.0, 15000.0)
    assert parse_amount_range("$50,001 - $100,000") == (50001.0, 100000.0)


def test_parse_amount_range_missing_second_bound_returns_none():
    assert parse_amount_range("$1,001") is None
    assert parse_amount_range("no numbers here") is None


def _table(*rows):
    header = ["Owner", "Asset", "Transaction Type", "Date", "Notification Date", "Amount"]
    return [header, *rows]


def test_parse_table_rows_happy_path():
    table = _table(
        ["SP", "Apple Inc. (AAPL)", "Purchase", "1/2/2026", "2/1/2026", "$1,001 - $15,000"],
        ["", "Microsoft Corp (MSFT)", "Sale (Full)", "1/5/2026", "2/3/2026", "$15,001 - $50,000"],
    )
    rows = parse_table_rows(table)
    assert len(rows) == 2
    assert rows[0] == TradeRow(
        ticker="AAPL",
        asset_name="Apple Inc. (AAPL)",
        owner="spouse",
        transaction_type="purchase",
        transaction_date="2026-01-02",
        disclosure_date="2026-02-01",
        amount_low=1001.0,
        amount_high=15000.0,
    )
    assert rows[1].owner == "self"
    assert rows[1].transaction_type == "sale_full"


def test_parse_table_rows_skips_unparseable_rows_without_fabricating():
    table = _table(
        # No transaction type, no date, no amount — should be skipped, not guessed.
        ["", "Some Fund", "", "", "", ""],
        ["SP", "Apple Inc. (AAPL)", "Purchase", "1/2/2026", "2/1/2026", "$1,001 - $15,000"],
    )
    rows = parse_table_rows(table)
    assert len(rows) == 1
    assert rows[0].ticker == "AAPL"


def test_parse_table_rows_missing_header_returns_empty():
    # A table that isn't the transactions table (e.g. a cover page) has no
    # recognizable header — must return [] rather than misparse it.
    table = [["Filer", "Date Filed"], ["Jane Smith", "1/1/2026"]]
    assert parse_table_rows(table) == []


def test_parse_table_rows_empty_table():
    assert parse_table_rows([]) == []


def test_parse_table_rows_no_ticker_in_asset_name():
    table = _table(
        ["", "Some Municipal Bond Fund", "Purchase", "1/2/2026", "2/1/2026", "$1,001 - $15,000"],
    )
    rows = parse_table_rows(table)
    assert len(rows) == 1
    assert rows[0].ticker is None
