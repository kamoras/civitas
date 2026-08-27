"""Tests for shared PTR parsing helpers (ptr_common.py). Pure functions —
no network, no DB, deterministic.
"""

from app.pipeline.fetch.ptr_common import (
    TradeRow,
    _parse_ocr_line,
    classify_transaction_type,
    extract_ticker,
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


def test_classify_transaction_type_house_form_letter_codes():
    """The House electronic PTR form's actual Transaction Type column value
    is the form's own single-letter code (P/S/E), not the spelled-out word
    — confirmed live 2026-07-25 against real filings. Every row on every
    House filing was silently dropped as unparseable until these were
    recognized (parse_table_rows' final "type is None" guard skips a row
    it can't classify, same as it does for `something else entirely`
    above — this is not a new leniency, just closing a real gap in what
    the form actually prints)."""
    assert classify_transaction_type("P") == "purchase"
    assert classify_transaction_type("S (partial)") == "sale_partial"
    assert classify_transaction_type("S (full)") == "sale_full"
    assert classify_transaction_type("S") == "sale_full"
    assert classify_transaction_type("E") == "exchange"


def test_parse_amount_range_valid():
    assert parse_amount_range("$1,001 - $15,000") == (1001.0, 15000.0)
    assert parse_amount_range("$50,001 - $100,000") == (50001.0, 100000.0)


def test_parse_amount_range_missing_second_bound_returns_none():
    assert parse_amount_range("$1,001") is None
    assert parse_amount_range("no numbers here") is None


def test_parse_amount_range_open_ended_top_bracket():
    """The top bracket states a floor and no ceiling. It used to fail to
    parse, which dropped the row — so the largest disclosed transactions
    were the ones missing from the record."""
    assert parse_amount_range("Over $50,000,000") == (50000000.0, 50000000.0)
    assert parse_amount_range("$50,000,001 +") == (50000001.0, 50000001.0)
    assert parse_amount_range("$1,000,001 or more") == (1000001.0, 1000001.0)


def test_parse_amount_range_open_ended_marker_is_required():
    """A lone figure with no open-ended marker is an unparseable cell, not
    an open-ended bracket — guessing which it was would invent a floor."""
    assert parse_amount_range("$50,000,000") is None


def test_parse_table_rows_keeps_an_open_ended_top_bracket_row():
    rows = parse_table_rows(_table(
        ["SP", "Bitcoin", "P", "10/30/2025", "11/14/2025", "Over $50,000,000"],
    ))
    assert len(rows) == 1
    assert (rows[0].amount_low, rows[0].amount_high) == (50000000.0, 50000000.0)


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


def test_parse_table_rows_asset_type_column_does_not_shadow_type():
    """The live Senate eFD layout puts an "Asset Type" column before
    "Type". The old first-header-containing-any-keyword binding captured
    "type" at the Asset Type column, so every row's transaction type read
    "Stock"/"Bond", classify_transaction_type returned None, and the whole
    filing was silently skipped."""
    header = ["Transaction Date", "Owner", "Ticker", "Asset Name",
              "Asset Type", "Type", "Amount"]
    table = [
        header,
        ["1/2/2026", "SP", "AAPL", "Apple Inc. (AAPL)", "Stock",
         "Purchase", "$1,001 - $15,000"],
    ]
    rows = parse_table_rows(table)
    assert len(rows) == 1
    assert rows[0].transaction_type == "purchase"
    assert rows[0].ticker == "AAPL"


def test_extract_ticker_finds_a_real_ticker():
    assert extract_ticker("Apple Inc. (AAPL)") == "AAPL"


def test_extract_ticker_rejects_the_word_the():
    """"Kroger Co (The)"/"Cigna Group (The)" are real company names whose
    trailing parenthetical isn't a ticker — confirmed live on a
    presidential 278-T (2026-08 audit): both stored ticker="THE" before
    this fix. No real US equity ticker is the word "the"."""
    assert extract_ticker("KROGER CO (THE)") is None
    assert extract_ticker("Some Municipal Bond Fund") is None


class TestParseOcrLine:
    """_parse_ocr_line — the per-line parser behind ocr_extract_rows.
    Every line here is real tesseract output from a live presidential
    278-T (2026-08 audit), not a hand-written idealized sample: OCR noise
    on this form is a fairly unpredictable mix of stray brackets, glued
    punctuation, and misread characters."""

    def test_a_clean_line_parses_correctly(self):
        row = _parse_ocr_line("2 |Ametek Inc purchase 6/23/2026, No|$15,001 - $50,000")
        assert row is not None
        assert row.asset_name == "Ametek Inc"
        assert row.transaction_type == "purchase"
        assert row.transaction_date == "2026-06-23"
        assert row.amount_low == 15001.0
        assert row.amount_high == 50000.0

    def test_leading_row_number_does_not_pollute_the_amount(self):
        """The confirmed live bug: the fallback-only version of this
        parser extracted (1, 6) as the amount pair — the leading row
        number and a digit from the date — instead of ($1,001, $15,000)."""
        row = _parse_ocr_line("1 Goldman Sachs Group Inc purchase 6/23/2026) No] $1,001 - $15,000")
        assert row is not None
        assert row.amount_low == 1001.0
        assert row.amount_high == 15000.0

    def test_a_misread_row_number_does_not_prevent_parsing(self):
        # "s" instead of a digit — OCR misreading a cramped row-number
        # column as a stray letter.
        row = _parse_ocr_line("s Howmet Aerospace Inc purchase 6/23/2026 No| $15,003 - $50,000")
        assert row is not None
        assert "Howmet Aerospace" in row.asset_name
        assert row.amount_low == 15003.0
        assert row.amount_high == 50000.0

    def test_a_bracket_glued_directly_onto_the_type_keyword_still_parses(self):
        row = _parse_ocr_line("7 KIMBERLY CLARK CORPORATION [purchase 6/12/2026 Yes |$15,001 - $50,000")
        assert row is not None
        assert row.asset_name.strip() == "KIMBERLY CLARK CORPORATION"
        assert row.amount_low == 15001.0
        assert row.amount_high == 50000.0

    def test_a_double_hyphen_amount_separator_still_parses(self):
        row = _parse_ocr_line("853 MAXLINEAR INC CLASS CLASS A purchase 6/22/2026 No|$15,001-- $50,000")
        assert row is not None
        assert row.amount_low == 15001.0
        assert row.amount_high == 50000.0

    def test_a_ticker_never_printed_on_this_form_is_none_not_required(self):
        """The old parser required a ticker match to accept ANY row,
        silently dropping ~95% of a real filing's transactions because
        this form prints none at all — see extract_ticker's docstring."""
        row = _parse_ocr_line("2 |Ametek Inc purchase 6/23/2026, No|$15,001 - $50,000")
        assert row is not None
        assert row.ticker is None

    def test_a_reversed_amount_bracket_is_dropped_not_stored(self):
        """A misread digit produced a real, live "$31,001 - $15,000" —
        low > high is never a valid disclosed bracket on this form, so
        this is dropped rather than stored as a range it never was."""
        row = _parse_ocr_line("535 EXXON MOBIL CORP [purchase 6/23/2026 No|$31,001 - $15,000")
        assert row is None

    def test_a_missing_amount_is_dropped_not_fabricated(self):
        # This exact row's dollar bracket did not survive OCR at all —
        # nothing to extract, so no row rather than a guessed one.
        row = _parse_ocr_line("36 Federated Hermes Government Obligations Fund purchase 6/3/2026")
        assert row is None

    def test_an_ocr_garbled_type_keyword_is_dropped_not_guessed(self):
        # "yurchase" for "purchase" — an OCR misread this parser can't
        # recover from, and shouldn't guess at.
        row = _parse_ocr_line(
            "3 International Flavors & Fragrances Inc yurchase 6/23/2026 No] $1,001 - $15,000"
        )
        assert row is None

    def test_a_line_with_no_transaction_data_is_dropped(self):
        assert _parse_ocr_line("OGE Form 278-T (Updated February 2024)") is None
        assert _parse_ocr_line("") is None
