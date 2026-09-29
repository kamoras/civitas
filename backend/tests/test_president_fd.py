"""The President's annual report (OGE 278e): its Part 7 is the transaction
record for its year and replaces that year's periodic (278-T) rows, which
are OCR'd scans (president_fd, stock_pipeline._ingest_president_annual)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.models import President, PresidentTrade
from app.pipeline.fetch.president_fd import annual_transactions
from app.pipeline.fetch.ptr_common import TradeRow

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
    from app.pipeline.stock_pipeline import _ingest_president

    db_session.add(President(id="trump-47", name="Donald Trump", party="R", number=47,
                             term_start="2025-01-20", is_current=True))
    # A periodic row inside 2025 (replaced) and one in 2026 (kept).
    for date, filing in (("2025-11-14", "t-2025"), ("2026-02-02", "t-2026")):
        db_session.add(PresidentTrade(
            president_id="trump-47", asset_name="Scan", transaction_type="purchase", transaction_date=date,
            disclosure_date=date, amount_low=1001, amount_high=15000, filing_id=filing, parse_confidence="ocr",
        ))
    db_session.commit()
    index = [
        {"doc_id": "annual-2025", "filing_date": "2026-06-29", "pdf_url": "https://extapps2.oge.gov/a.pdf", "kind": "annual"},
        {"doc_id": "t-2026-later", "filing_date": "2026-03-01", "pdf_url": "https://extapps2.oge.gov/t.pdf", "kind": "periodic"},
    ]
    # A later 278-T that also reports a 2025 transaction: the annual report
    # holds it already.
    periodic = [_row("2025-12-30", kind="periodic", filing="t-2026-later", confidence="ocr"),
                _row("2026-02-20", kind="periodic", filing="t-2026-later", confidence="ocr")]
    with (
        patch("app.pipeline.stock_pipeline.fetch_president_ptr_index", new_callable=AsyncMock, return_value=index),
        patch("app.pipeline.stock_pipeline.fetch_annual_transactions", new_callable=AsyncMock,
              return_value=(2025, [_row("2025-03-04", asset="YELP INC"), _row("2025-11-14", asset="Bond")])),
        patch("app.pipeline.stock_pipeline.fetch_president_ptr", new_callable=AsyncMock, return_value=periodic),
        patch("app.pipeline.stock_pipeline.classify_batch_with_learning", return_value=({}, [])),
    ):
        await _ingest_president(db_session, AsyncMock())

    stored = {(t.transaction_date, t.filing_id, t.report_kind) for t in db_session.query(PresidentTrade)}
    assert stored == {
        ("2025-03-04", "annual-2025", "annual"),
        ("2025-11-14", "annual-2025", "annual"),
        ("2026-02-02", "t-2026", "periodic"),
        ("2026-02-20", "t-2026-later", "periodic"),
    }
