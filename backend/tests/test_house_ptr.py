"""Tests for house_ptr.py — index parsing and the fetch/parse orchestration.

Network is mocked (no live requests); PDF table parsing itself is covered
by test_ptr_common.py, so here we only verify the wiring (dedupe via cache,
tagging rows with source_url/filing_id/parse_confidence).
"""

import io
import zipfile
from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.fetch.house_ptr import fetch_and_parse_ptr, fetch_ptr_filing_index

_SAMPLE_XML = b"""<?xml version="1.0"?>
<Members>
  <Member>
    <Last>Smith</Last>
    <First>Jane</First>
    <FilingType>P</FilingType>
    <StateDst>CA05</StateDst>
    <Year>2026</Year>
    <FilingDate>2/1/2026</FilingDate>
    <DocID>20026590</DocID>
  </Member>
  <Member>
    <Last>Doe</Last>
    <First>John</First>
    <FilingType>O</FilingType>
    <StateDst>TX01</StateDst>
    <Year>2026</Year>
    <FilingDate>2/2/2026</FilingDate>
    <DocID>20026591</DocID>
  </Member>
</Members>
"""


def _sample_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("2026FD.xml", _SAMPLE_XML)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_fetch_ptr_filing_index_filters_to_ptr_only(db_session):
    with patch(
        "app.pipeline.fetch.house_ptr._fetch_bytes_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = _sample_zip()
        filings = await fetch_ptr_filing_index(None, db_session, 2026)

    assert len(filings) == 1
    assert filings[0]["last"] == "Smith"
    assert filings[0]["first"] == "Jane"
    assert filings[0]["doc_id"] == "20026590"
    assert filings[0]["filing_date"] == "2026-02-01"
    assert filings[0]["pdf_url"].endswith("/ptr-pdfs/2026/20026590.pdf")


@pytest.mark.asyncio
async def test_fetch_ptr_filing_index_bad_zip_returns_empty(db_session):
    with patch(
        "app.pipeline.fetch.house_ptr._fetch_bytes_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = b"not a zip file"
        filings = await fetch_ptr_filing_index(None, db_session, 2026)
    assert filings == []


@pytest.mark.asyncio
async def test_fetch_ptr_filing_index_fetch_failure_returns_empty(db_session):
    with patch(
        "app.pipeline.fetch.house_ptr._fetch_bytes_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = None
        filings = await fetch_ptr_filing_index(None, db_session, 2026)
    assert filings == []


@pytest.mark.asyncio
async def test_fetch_and_parse_ptr_tags_rows(db_session):
    filing = {"doc_id": "20026590", "pdf_url": "https://example.com/20026590.pdf"}
    parsed_rows = [
        {
            "ticker": "AAPL", "asset_name": "Apple Inc. (AAPL)", "owner": "self",
            "transaction_type": "purchase", "transaction_date": "2026-01-02",
            "disclosure_date": "2026-02-01", "amount_low": 1001.0, "amount_high": 15000.0,
        }
    ]
    with patch(
        "app.pipeline.fetch.house_ptr._fetch_bytes_with_retry", new_callable=AsyncMock
    ) as mock_fetch, patch(
        "app.pipeline.fetch.house_ptr.parse_pdf_bytes"
    ) as mock_parse:
        mock_fetch.return_value = b"%PDF-fake-bytes"
        mock_parse.return_value = (parsed_rows, "text")
        rows = await fetch_and_parse_ptr(None, db_session, filing)

    assert len(rows) == 1
    assert rows[0]["parse_confidence"] == "text"
    assert rows[0]["source_url"] == "https://example.com/20026590.pdf"
    assert rows[0]["filing_id"] == "20026590"


@pytest.mark.asyncio
async def test_fetch_and_parse_ptr_pdf_fetch_failure_returns_empty(db_session):
    filing = {"doc_id": "20026590", "pdf_url": "https://example.com/20026590.pdf"}
    with patch(
        "app.pipeline.fetch.house_ptr._fetch_bytes_with_retry", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = None
        rows = await fetch_and_parse_ptr(None, db_session, filing)
    assert rows == []


@pytest.mark.asyncio
async def test_fetch_and_parse_ptr_parse_exception_returns_empty_not_raises(db_session):
    filing = {"doc_id": "20026590", "pdf_url": "https://example.com/20026590.pdf"}
    with patch(
        "app.pipeline.fetch.house_ptr._fetch_bytes_with_retry", new_callable=AsyncMock
    ) as mock_fetch, patch(
        "app.pipeline.fetch.house_ptr.parse_pdf_bytes"
    ) as mock_parse:
        mock_fetch.return_value = b"garbage"
        mock_parse.side_effect = Exception("corrupt PDF")
        rows = await fetch_and_parse_ptr(None, db_session, filing)
    assert rows == []
