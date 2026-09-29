"""Read the sitting president's annual financial disclosure report (OGE
Form 278e) from OGE's public index (president_ptr.fetch_ptr_filing_index,
kind "annual").

Its Part 7 lists every transaction of the report's calendar year, as text.
The periodic 278-T filings the same transactions were first reported on
are scans, read by OCR, so for every year an annual report covers it is
the record: 21,285 transactions for 2025, where the 278-Ts had yielded
2,362 rows, some misread (a bond's maturity, "DUE 12/15/2078", taken for
its trade date). stock_pipeline._ingest_president replaces the year's
periodic rows with these.

Part 7 prints no owner column and no notification date, so a row's owner
is "unknown" and it supports no timeliness figure (report_kind "annual").
"""

import io
import logging
import re
from dataclasses import asdict
from urllib.parse import urlparse

import pdfplumber
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import fetch_with_retry_requests
from app.pipeline.fetch.president_ptr import _ALLOWED_PDF_HOSTS, _rate_limiter
from app.pipeline.fetch.ptr_common import TradeRow, extract_ticker, normalize_date, parse_amount_range

logger = logging.getLogger(__name__)

# Bump when the same report would be read differently; the parse is cached
# under it.
PARSER_VERSION = 1

_CACHE_TIER = "president_fd"
# A filed report never changes; an amendment is a new filing.
_REPORT_MAX_AGE_HOURS = 24 * 90

# The form's own labels (a documented data-format convention).
_YEAR_RE = re.compile(r"Year \(Annual Report only\):\s*(\d{4})")
_PART_7 = "Part 7: Transactions"
# "# Description Type Date Amount", one transaction per line.
_TRANSACTION_RE = re.compile(
    r"^\d+\s+(?P<asset>.+?)\s+(?P<type>purchase|sale(?:\s*\(partial\))?|exchange)\s+"
    r"(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+(?P<amount>.+)$",
    re.IGNORECASE,
)
_TYPES = {"purchase": "purchase", "sale": "sale_full", "exchange": "exchange"}


def annual_transactions(pages: list[str]) -> tuple[int, list[TradeRow]] | None:
    """(the report's year, its Part 7 transactions) from a 278e's page
    texts, or None when they state no report year or hold no Part 7 (a
    scan, or not a 278e)."""
    rows: list[TradeRow] = []
    year = None
    for text in pages:
        if year is None and (match := _YEAR_RE.search(text)):
            year = int(match[1])
        if _PART_7 not in text:
            continue
        for line in text.splitlines():
            match = _TRANSACTION_RE.match(line.strip())
            if not match:
                continue
            amount = parse_amount_range(match["amount"])
            date = normalize_date(match["date"])
            kind = match["type"].lower()
            if amount is None or date is None:
                logger.info("278e transaction line not read: %r", line[:120])
                continue
            rows.append(TradeRow(
                ticker=extract_ticker(match["asset"]),
                asset_name=match["asset"].strip(),
                owner="unknown",
                transaction_type="sale_partial" if "partial" in kind else _TYPES[kind.split()[0]],
                transaction_date=date,
                disclosure_date=date,  # replaced by the caller with the report's filing date
                amount_low=amount[0],
                amount_high=amount[1],
            ))
    if year is None or not rows:
        return None
    return year, rows


def _page_texts(pdf_bytes: bytes) -> list[str]:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


async def fetch_annual_transactions(db: Session, filing: dict) -> tuple[int, list[TradeRow]] | None:
    """The annual report `filing` names (president_ptr index entry, kind
    "annual"), read: (its year, its transactions, each tagged with the
    filing), or None when it can't be fetched or read. Cached: the report is
    8 MB and 900 pages."""
    cache_key = f"annual-transactions-v{PARSER_VERSION}-{filing['doc_id']}"
    cached = api_cache_get(db, _CACHE_TIER, cache_key, max_age_hours=_REPORT_MAX_AGE_HOURS)
    if cached is not None:
        return cached["year"], [TradeRow(**row) for row in cached["rows"]]

    url = filing["pdf_url"]
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_PDF_HOSTS:
        logger.warning("Rejected non-allowlisted annual report URL: %s", url[:120])
        return None
    resp = await fetch_with_retry_requests(
        _rate_limiter, "GET", url, log_label="Presidential 278e", timeout=180.0,
    )
    if resp is None or resp.status_code != 200:
        return None
    try:
        result = annual_transactions(_page_texts(resp.content))
    except Exception:
        logger.exception("Failed to parse presidential annual report %s", url)
        return None
    if result is None:
        logger.warning("Presidential annual report %s held no readable Part 7", url)
        return None
    year, rows = result
    for row in rows:
        row.disclosure_date = filing.get("filing_date") or row.disclosure_date
        row.source_url = url
        row.filing_id = filing["doc_id"]
        row.report_kind = "annual"
    api_cache_set(
        db, _CACHE_TIER, cache_key, {"year": year, "rows": [asdict(r) for r in rows]},
        normal_ttl_hours=_REPORT_MAX_AGE_HOURS,
    )
    return year, rows
