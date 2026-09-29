"""The President's annual report (OGE 278e): its Part 7 is the transaction
record for its year and replaces that year's periodic (278-T) rows, which
are OCR'd scans (president_fd, stock_pipeline._ingest_president_annual)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.models import FinancialDisclosure, President, PresidentTrade
from app.pipeline import holdings_pipeline
from app.pipeline.fetch.fd_common import HoldingRow
from app.pipeline.fetch.president_fd import annual_holdings, annual_transactions
from app.pipeline.fetch.ptr_common import TradeRow
from app.pipeline.stock_pipeline import _ingest_president
from app.services.holdings_service import get_president_holdings

# Lines as pdfplumber extracts them from the 2026 filing's pages 1 and 401.
_PAGE_1 = "Report Type: Annual GOVERNMENT ETHICS\nYear (Annual Report only): 2025\nDate of Appointment: 01/2025"
_PAGE_401 = """Part 7: Transactions
# Description Type Date Amount
INVESTMENT ACCOUNT #7
490 YELP INC Purchase 7/31/2025 $15,001 - $50,000
491 MARCUS & MILLICHAP INC Purchase 4/28/2025 $15,001 - $50,000
497 FMC CORP Sale (partial) 9/2/2025 $1,000,001 - $5,000,000
498 JACKSON FINL INC Sale 1/30/2025 Over $50,000,000"""
_PART_6 = """Part 6: Other Assets and Income
86 APPLE INC N/A $1,000,001 - $5,000,000 None (or less than $201)"""


def test_part_7_is_read_and_nothing_else_is():
    year, rows = annual_transactions([_PAGE_1, _PART_6, _PAGE_401])
    assert year == 2025
    assert [(r.asset_name, r.transaction_type, r.transaction_date) for r in rows] == [
        ("YELP INC", "purchase", "2025-07-31"),
        ("MARCUS & MILLICHAP INC", "purchase", "2025-04-28"),
        ("FMC CORP", "sale_partial", "2025-09-02"),
        ("JACKSON FINL INC", "sale_full", "2025-01-30"),
    ]
    assert (rows[3].amount_low, rows[3].amount_high) == (50_000_000.0, 50_000_000.0)
    assert {r.owner for r in rows} == {"unknown"}  # Part 7 prints no owner


def test_a_report_without_its_year_or_part_7_is_not_read():
    assert annual_transactions([_PAGE_401]) is None
    assert annual_transactions([_PAGE_1, _PART_6]) is None


def _row(date, **kw):
    return TradeRow(
        ticker=None, asset_name=kw.get("asset", "Bond"), owner="unknown", transaction_type="purchase",
        transaction_date=date, disclosure_date=kw.get("filed", date), amount_low=1001.0, amount_high=15000.0,
        filing_id=kw.get("filing", "annual-2025"), source_url="https://extapps2.oge.gov/a.pdf",
        report_kind=kw.get("kind", "annual"), parse_confidence=kw.get("confidence", "text"),
    )


@pytest.mark.asyncio
async def test_the_annual_report_replaces_its_years_periodic_rows(db_session):
    db_session.add(President(id="trump-47", name="Donald Trump", party="R", number=47,
                             term_start="2025-01-20", is_current=True))
    # A periodic row inside 2025 (replaced) and one in 2026 (kept); rows
    # whose date isn't legible go by their filing's date: filed in 2025
    # (the annual report's), filed in 2026 (kept).
    for date, filed, filing in (("2025-11-14", "2025-11-14", "t-2025"), ("2026-02-02", "2026-02-02", "t-2026"),
                                (None, "2025-12-10", "t-2025-dec"), (None, "2026-05-14", "t-2026-may")):
        db_session.add(PresidentTrade(
            president_id="trump-47", asset_name="Scan", transaction_type="purchase", transaction_date=date,
            disclosure_date=filed, amount_low=1001, amount_high=15000, filing_id=filing, parse_confidence="ocr",
        ))
    db_session.commit()
    index = [
        {"doc_id": "annual-2025", "filing_date": "2026-06-29", "pdf_url": "https://extapps2.oge.gov/a.pdf", "kind": "annual"},
        {"doc_id": "t-2026-later", "filing_date": "2026-03-01", "pdf_url": "https://extapps2.oge.gov/t.pdf", "kind": "periodic"},
    ]
    # A later 278-T that also reports a 2025 transaction: the annual report
    # holds it already.
    periodic = [_row("2025-12-30", kind="periodic", filing="t-2026-later", confidence="ocr"),
                _row("2026-02-20", kind="periodic", filing="t-2026-later", confidence="ocr"),
                _row(None, kind="periodic", filing="t-2026-later", confidence="ocr", filed="2026-03-01")]
    with (
        patch("app.pipeline.stock_pipeline.fetch_president_ptr_index", new_callable=AsyncMock, return_value=index),
        patch("app.pipeline.stock_pipeline.fetch_annual_transactions", new_callable=AsyncMock,
              return_value=(2025, [_row("2025-03-04", asset="YELP INC"), _row("2025-11-14", asset="Bond")])),
        patch("app.pipeline.stock_pipeline.fetch_president_ptr", new_callable=AsyncMock, return_value=periodic),
        patch("app.pipeline.stock_pipeline.issuer_industries", new_callable=AsyncMock, return_value=({}, {})),
    ):
        await _ingest_president(db_session, AsyncMock())

    stored = {(t.transaction_date, t.filing_id, t.report_kind) for t in db_session.query(PresidentTrade)}
    assert stored == {
        ("2025-03-04", "annual-2025", "annual"),
        ("2025-11-14", "annual-2025", "annual"),
        ("2026-02-02", "t-2026", "periodic"),
        ("2026-02-20", "t-2026-later", "periodic"),
        (None, "t-2026-may", "periodic"),
        (None, "t-2026-later", "periodic"),
    }


# The asset parts (2's Schedule 1, 5, 6), as pdfplumber's tables give them
# from the 2026 filing: one cell per column, wrapped text joined.
class _Page:
    def __init__(self, text, tables=()):
        self._text, self._tables = text, list(tables)

    def extract_text(self):
        return self._text

    def extract_tables(self):
        return self._tables


class _Pdf:
    def __init__(self, *pages):
        self.pages = list(pages)


_SCHEDULE_1 = _Page("Schedule 1 for Part 2\nPart 2: Filer's Employment Assets & Income", [[
    ["Part 2: Filer's Employment Assets", "", "", "", "", "", "", ""],
    ["#", "Description", "Underlying Assets and Location", "EIF", "Value", "Income Type", "Income Amount", "Ownership %"],
    ["1", "1125 South Ocean LLC", "Underlying Assets: residential real estate and\nU.S.bank account\nLocation: Palm Beach, FL",
     "N/A", "Over $50,000,000", "Rent and Interest", "$1,000,001 - $5,000,000", "DJT Holdings LLC: 100%"],
    ["8", "40 Wall Street Member Corp.", "See line 7", "No", "", "", "", "40 Wall Development Associates LLC: 100%"],
    ["21", "CIC Digital LLC", "", "N/A", "", "", "", "DJT Holdings LLC: 100%"],
    ["21.1", "U.S Bank Account", "", "N/A", "$1,000,001 to $5,000,000", "Interest", "$1,001 - $2,500", ""],
    ["21.5", "Cryptocurrency Wallet Virtual Bitcoin Key (held in cold wallet)", "", "N/A", "Over $50,000,000", "", "", ""],
    ["137.1", "Allied Irish Bank - Cash", "", "N/A", "15,001 to 25,000 Euro", "", "", ""],
    ["204", "DT Endeavor I LLC", "Underlying Asset: 25% equity interest", "N/A", "$5,000,001 to $25,000,000", "", "", ""],
]])
_PART_5 = _Page("Part 5: Spouse's Employment Assets & Income", [[
    ["#", "Description", "EIF", "Value", "Income Type", "Income Amount", ""],
    ["2", "721 33H Holdings LLC\n(pass-thru company for 721 33H LLC)\nLocation: New York, NY", "No", "", "None (or less than $201)", "", ""],
    ["2.1", "721 33H LLC\nUnderlying Assets: US bank account and residential real estate\nLocation: New York, NY",
     "N/A", "$500,001 - $1,000,000", "None (or less than $201)", "", ""],
]])
_PART_6_TABLE = _Page("Part 6: Other Assets and Income", [[
    ["#", "Description", "EIF", "Value", "Income Type", "Income Amount"],
    ["", "INVESTMENT ACCOUNT #1", "", "", "", ""],
    ["1", "***CARNIVAL CORP", "N/A", "$1,000,001 - $5,000,000", "", "None (or less than $201)"],
    ["17", "SPDR SERIES TRUST STATE STREET SPDR PORTFOLIO HIGH YIELD BOND ETF", "Yes", "$1,000,001 - $5,000,000",
     "DIVIDEND", "$50,001 - $100,000"],
    ["", "*JP MORGAN IS THE SOLE TRUSTEE", "", "", "", ""],
    ["43", "***EATON CORPORATION PLC", "N/A", "None (or less than $1,001)", "", "None (or less than $201)"],
]])
_EXHIBIT = _Page("Exhibit A for Part 2\nRegistered Owner Country Trademark Name", [[
    ["#", "Description", "Value"], ["1", "TRUMP TOWER", "$1,001 - $15,000"],
]])


def test_assets_are_categorized_only_by_what_the_form_states():
    year, rows = annual_holdings(_Pdf(_Page(_PAGE_1), _PART_5, _PART_6_TABLE, _SCHEDULE_1, _EXHIBIT))
    assert year == 2025
    got = [(r.asset_name, r.category, r.owner, r.account, r.value_low, r.value_high) for r in rows]
    assert got == [
        # The spouse's: named before its stated assets, under the entity it belongs to.
        ("721 33H LLC", "REAL_ESTATE", "spouse", "721 33H Holdings LLC (pass-thru company for 721 33H LLC)",
         500001.0, 1000000.0),
        # Part 6: a fund only where the EIF column says so; every other
        # security's type is not stated, whatever its name.
        ("CARNIVAL CORP", "UNSTATED", "unknown", "INVESTMENT ACCOUNT #1", 1000001.0, 5000000.0),
        ("SPDR SERIES TRUST STATE STREET SPDR PORTFOLIO HIGH YIELD BOND ETF", "FUNDS", "unknown",
         "INVESTMENT ACCOUNT #1", 1000001.0, 5000000.0),
        ("EATON CORPORATION PLC", "UNSTATED", "unknown", "INVESTMENT ACCOUNT #1", 0.0, 1000.0),
        # Schedule 1: by the stated underlying assets, first match winning.
        ("1125 South Ocean LLC", "REAL_ESTATE", "self", None, 50000000.0, 50000000.0),
        ("U.S Bank Account", "CASH", "self", "CIC Digital LLC", 1000001.0, 5000000.0),
        ("Cryptocurrency Wallet Virtual Bitcoin Key (held in cold wallet)", "CRYPTO", "self", "CIC Digital LLC",
         50000000.0, 50000000.0),
        # A value in euros is kept as printed, not read as dollars.
        ("Allied Irish Bank - Cash", "CASH", "self", "CIC Digital LLC", None, None),
        ("DT Endeavor I LLC", "BUSINESS", "self", None, 5000001.0, 25000000.0),
    ]
    # A managing-member line with no value of its own is not an asset, and
    # the exhibit restating Schedule 1's trademarks is not read.
    assert not any(r.asset_name in ("40 Wall Street Member Corp.", "CIC Digital LLC", "TRUMP TOWER") for r in rows)


def test_a_report_with_no_year_or_no_assets_is_not_read():
    assert annual_holdings(_Pdf(_PART_6_TABLE)) is None
    assert annual_holdings(_Pdf(_Page(_PAGE_1))) is None


def _holding(name="CARNIVAL CORP", low=1000001.0, high=5000000.0):
    return HoldingRow(asset_name=name, asset_type="", category="UNSTATED", owner="unknown",
                      value_text="$1,000,001 - $5,000,000", value_low=low, value_high=high)


_ANNUAL = {"doc_id": "A2026", "filing_date": "2026-07-01", "kind": "annual",
           "pdf_url": "https://extapps2.oge.gov/201/Presiden.nsf/A2026.pdf"}


async def _ingest_holdings(db_session, filings, report):
    with patch.object(holdings_pipeline, "fetch_president_filing_index", AsyncMock(return_value=filings)), \
         patch.object(holdings_pipeline, "fetch_president_annual", AsyncMock(return_value=report)) as fetch:
        count = await holdings_pipeline.ingest_president_holdings(db_session, None)
    return count, fetch


async def test_the_newest_annual_reports_assets_are_stored_once(db_session):
    db_session.add(President(id="trump-47", name="Donald J. Trump", party="R", number=47,
                             term_start="2025-01-20", is_current=True))
    db_session.commit()
    older = {**_ANNUAL, "doc_id": "A2025", "filing_date": "2025-06-14"}
    count, _ = await _ingest_holdings(db_session, [older, _ANNUAL], (2025, [_holding(), _holding("TESLA INC")]))
    assert count == 2
    disclosure = db_session.query(FinancialDisclosure).one()
    assert (disclosure.president_id, disclosure.filing_id, disclosure.report_label, disclosure.as_of_date) == (
        "trump-47", "A2026", "2025 annual report", "2025-12-31",
    )
    # The same filing, read by this parser: not fetched again.
    count, fetch = await _ingest_holdings(db_session, [older, _ANNUAL], (2025, [_holding()]))
    assert count == 0 and not fetch.called
    holdings = get_president_holdings(db_session, "trump-47")
    assert holdings.available and holdings.holdings_count == 2
    assert [c.label for c in holdings.categories] == ["Type not stated"]
    assert get_president_holdings(db_session, "nobody") is None


async def test_a_report_that_cannot_be_read_fails_the_phase_and_keeps_the_stored_one(db_session):
    db_session.add(President(id="trump-47", name="Donald J. Trump", party="R", number=47,
                             term_start="2025-01-20", is_current=True))
    db_session.commit()
    await _ingest_holdings(db_session, [{**_ANNUAL, "doc_id": "A2025"}], (2024, [_holding()]))
    with pytest.raises(RuntimeError):
        await _ingest_holdings(db_session, [_ANNUAL], None)
    assert db_session.query(FinancialDisclosure).one().filing_id == "A2025"
