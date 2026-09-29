"""Read the sitting president's annual financial disclosure report (OGE
Form 278e) from OGE's public index (president_ptr.fetch_ptr_filing_index,
kind "annual"): its transactions (Part 7) and its assets (Parts 2, 5, 6).

Its Part 7 lists every transaction of the report's calendar year, as text.
The periodic 278-T filings the same transactions were first reported on
are scans, read by OCR, so for every year an annual report covers it is
the record: 21,285 transactions for 2025, where the 278-Ts had yielded
2,362 rows, some misread (a bond's maturity, "DUE 12/15/2078", taken for
its trade date). stock_pipeline._ingest_president replaces the year's
periodic rows with these.

Part 7 prints no owner column and no notification date, so a row's owner
is "unknown" and it supports no timeliness figure (report_kind "annual").

The assets back the scorecard's holdings breakdown (holdings_pipeline):
Part 2's Schedule 1 (the business entities), Part 5 (the spouse's) and
Part 6 (the investment accounts). Unlike the House and Senate forms, the
278e has no asset-type column, and no asset is given a type from its name
(fd_common). A category comes only from what the form states: a business
line's own "Underlying Assets" (real estate, a bank account, ...) and, in
Part 6, the excepted-investment-fund column; every other security is "type
not stated".
"""

import asyncio
import io
import logging
import re
from dataclasses import asdict
from urllib.parse import urlparse

import pdfplumber
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.fd_common import HoldingRow, parse_holding_value, ticker_for
from app.pipeline.fetch.http_utils import fetch_with_retry_requests
from app.pipeline.fetch.president_ptr import _ALLOWED_PDF_HOSTS, _rate_limiter
from app.pipeline.fetch.ptr_common import TradeRow, extract_ticker, normalize_date, parse_amount_range

logger = logging.getLogger(__name__)

# Bump when the same report would be read differently; each parse is cached
# under its own. A new HOLDINGS_PARSER_VERSION also re-reads the stored
# holdings (holdings_pipeline.ingest_president_holdings).
PARSER_VERSION = 1
HOLDINGS_PARSER_VERSION = 1

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


# The parts that list assets, and whose they are: Part 2 the filer's, Part
# 5 the spouse's; Part 6 covers the filer, spouse and dependent children
# without saying whose each is.
_ASSET_PART_RE = re.compile(r"Part ([256]): ")
_PART_OWNER = {"2": "self", "5": "spouse", "6": "unknown"}
# "21", "21.4", "21.4a", "1.": a line of the table; a sub-line (21.4) is an
# asset of the entity on the line it numbers.
_LINE_NUMBER_RE = re.compile(r"^(\d+)(\.\d+[a-z]?)?\.?$")
_UNDERLYING_RE = re.compile(r"Underlying Assets?:\s*(.*?)(?:Location:|$)", re.I)
# Where Part 5 prints an entity's underlying assets and location inside its
# description; the name is what comes before them.
_NAME_END_RE = re.compile(r"\s*(?:Underlying Assets?:|Location:)", re.I)
# A business line's category, from the underlying assets it states (first
# match wins: "residential real estate and U.S. bank account" is real
# estate). Anything else it states (an equity interest, a license, a book
# deal) is a business interest.
_STATED_CATEGORIES = [
    (re.compile(r"cryptocurrency", re.I), "CRYPTO"),
    (re.compile(r"real estate|golf club|\bland\b", re.I), "REAL_ESTATE"),
    (re.compile(r"pension", re.I), "RETIREMENT"),
    (re.compile(r"bank account|\bcash\b", re.I), "CASH"),
]


def _stated_category(stated: str) -> str:
    return next((category for pattern, category in _STATED_CATEGORIES if pattern.search(stated)), "BUSINESS")


def _cells(raw: list[str | None]) -> list[str]:
    return [" ".join((cell or "").split()) for cell in raw]


def _holding(part: str, number: str, row: dict[str, str], account: str | None) -> HoldingRow:
    value = row.get("Value", "")
    name = row.get("Description", "").lstrip("* ").strip()
    if part == "6":
        fund = row.get("EIF") == "Yes"
        category, asset_type = ("FUNDS", "Excepted investment fund") if fund else ("UNSTATED", "")
    else:
        # A sub-line states its asset as its description ("U.S Bank
        # Account"); an entity, in its Underlying Assets (Part 5 prints them
        # under the description).
        underlying = _UNDERLYING_RE.search(row.get("Underlying Assets and Location") or row.get("Description", ""))
        asset_type = underlying[1].strip() if underlying else (name if "." in number else "")
        category = _stated_category(asset_type)
        name = _NAME_END_RE.split(name)[0].strip() or name
    # A value in euros is kept as printed, never read as dollars.
    low, high = (None, None) if "euro" in value.lower() else parse_holding_value(value)
    return HoldingRow(
        asset_name=name, asset_type=asset_type[:200], category=category, owner=_PART_OWNER[part],
        value_text=value, value_low=low, value_high=high, account=account, ticker=ticker_for(name),
    )


def annual_holdings(pdf) -> tuple[int, list[HoldingRow]] | None:
    """(the report's year, its assets) from an opened 278e: every line of
    Parts 2, 5 and 6 that states a value. A line with no value of its own
    (an entity that is the managing member of another, "See Line 43") is
    not an asset. None when the report states no year or lists no asset."""
    year = None
    holdings: list[HoldingRow] = []
    # Part 6: the account a line is in (each page heads its lines with it).
    # Parts 2 and 5: the entity a sub-line belongs to, which can be on the
    # page before.
    part, context = None, None
    for page in pdf.pages:
        text = page.extract_text() or ""
        if year is None and (match := _YEAR_RE.search(text)):
            year = int(match[1])
        found = _ASSET_PART_RE.search(text)
        # Exhibits after Schedule 1 (the trademark list) restate its lines.
        if found is None or "Exhibit" in text[:300]:
            continue
        if found[1] != part:
            part, context = found[1], None
        for table in page.extract_tables():
            header = None
            for raw in table:
                cells = _cells(raw)
                if cells and cells[0] == "#":
                    header = cells
                    continue
                if header is None:
                    continue
                row = dict(zip(header, cells))
                number, description, value = row.get("#", ""), row.get("Description", ""), row.get("Value", "")
                line = _LINE_NUMBER_RE.match(number)
                if line is None:
                    # A footnote under a heading starts with "*".
                    if part == "6" and description and not value and not description.startswith("*"):
                        context = description
                    continue
                sub_line = line[2] is not None
                if part != "6" and not sub_line:
                    context = _NAME_END_RE.split(description)[0].strip() or None
                if value:
                    holdings.append(_holding(part, number, row, context if part == "6" or sub_line else None))
    if year is None or not holdings:
        return None
    return year, holdings


def _page_texts(pdf_bytes: bytes) -> list[str]:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def _read_holdings(pdf_bytes: bytes) -> tuple[int, list[HoldingRow]] | None:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return annual_holdings(pdf)


async def _download(filing: dict) -> bytes | None:
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
    return resp.content


async def fetch_annual_transactions(db: Session, filing: dict) -> tuple[int, list[TradeRow]] | None:
    """The annual report `filing` names (president_ptr index entry, kind
    "annual"), read: (its year, its transactions, each tagged with the
    filing), or None when it can't be fetched or read. Cached: the report is
    8 MB and 900 pages, and read off the event loop."""
    cache_key = f"annual-transactions-v{PARSER_VERSION}-{filing['doc_id']}"
    cached = api_cache_get(db, _CACHE_TIER, cache_key, max_age_hours=_REPORT_MAX_AGE_HOURS)
    if cached is not None:
        return cached["year"], [TradeRow(**row) for row in cached["rows"]]
    url = filing["pdf_url"]
    pdf_bytes = await _download(filing)
    if pdf_bytes is None:
        return None
    try:
        result = annual_transactions(await asyncio.to_thread(_page_texts, pdf_bytes))
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


async def fetch_annual_holdings(db: Session, filing: dict) -> tuple[int, list[HoldingRow]] | None:
    """The same report's assets (annual_holdings): (its year, its holdings),
    or None when it can't be fetched or read. Cached like the
    transactions."""
    cache_key = f"annual-holdings-v{HOLDINGS_PARSER_VERSION}-{filing['doc_id']}"
    cached = api_cache_get(db, _CACHE_TIER, cache_key, max_age_hours=_REPORT_MAX_AGE_HOURS)
    if cached is not None:
        return cached["year"], [HoldingRow(**row) for row in cached["rows"]]
    pdf_bytes = await _download(filing)
    if pdf_bytes is None:
        return None
    try:
        result = await asyncio.to_thread(_read_holdings, pdf_bytes)
    except Exception:
        logger.exception("Failed to read the assets of presidential annual report %s", filing["pdf_url"])
        return None
    if result is None:
        logger.warning("Presidential annual report %s listed no readable assets", filing["pdf_url"])
        return None
    year, rows = result
    api_cache_set(
        db, _CACHE_TIER, cache_key, {"year": year, "rows": [asdict(r) for r in rows]},
        normal_ttl_hours=_REPORT_MAX_AGE_HOURS,
    )
    return year, rows
