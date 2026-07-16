"""Shared parsing helpers for STOCK Act periodic transaction reports (PTRs).

Used by both house_ptr.py and senate_ptr.py — the underlying data (owner
codes, transaction-type vocabulary, amount-range formatting, date format)
is defined by the same federal disclosure form conventions in both chambers,
only the delivery mechanism (PDF vs. HTML) differs.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class TradeRow:
    """One parsed PTR transaction line.

    Built here with the fields available from the raw filing table
    (ticker..amount_high); parse_confidence/source_url/filing_id are
    filled in by the house_ptr.py/senate_ptr.py caller once it knows
    which filing/confidence produced the row, and industry is filled in
    later still, by stock_pipeline.py's ticker->company->embedding
    classification pass. Previously a plain dict shared across all of
    these stages — a typo'd key on any one access (construction, the
    two callers' tagging, or stock_pipeline.py's DB-row construction)
    surfaced as a silent KeyError deep in an ingest loop rather than at
    the point of the mistake.
    """
    ticker: str | None
    asset_name: str
    owner: str
    transaction_type: str
    transaction_date: str
    disclosure_date: str
    amount_low: float
    amount_high: float
    parse_confidence: str = "text"
    source_url: str = ""
    filing_id: str = ""
    industry: str | None = None

# PTR owner codes -> our owner vocabulary (StockTrade.owner / RepStockTrade.owner).
OWNER_CODES = {"SP": "spouse", "DC": "dependent", "JT": "joint"}

# Transaction-type text as printed on the form -> our vocabulary. Matched
# case-insensitively against a substring since forms vary slightly in
# capitalization/spacing across years and between chambers.
TXN_TYPE_PATTERNS = [
    (re.compile(r"purchase", re.I), "purchase"),
    (re.compile(r"sale.*\(partial\)|partial.*sale", re.I), "sale_partial"),
    (re.compile(r"sale.*\(full\)|sale", re.I), "sale_full"),
    (re.compile(r"exchange", re.I), "exchange"),
]

TICKER_RE = re.compile(r"\(([A-Z]{1,5})\)")
AMOUNT_RE = re.compile(r"\$?([\d,]+)")


def normalize_date(raw: str) -> str | None:
    """Parse a M/D/YYYY (or MM/DD/YYYY) date string to ISO YYYY-MM-DD."""
    raw = (raw or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def classify_transaction_type(text: str) -> str | None:
    for pattern, label in TXN_TYPE_PATTERNS:
        if pattern.search(text):
            return label
    return None


def parse_amount_range(text: str) -> tuple[float, float] | None:
    matches = AMOUNT_RE.findall(text or "")
    if len(matches) < 2:
        return None
    try:
        low = float(matches[0].replace(",", ""))
        high = float(matches[1].replace(",", ""))
        return (low, high)
    except ValueError:
        return None


def parse_table_rows(table: list[list[str | None]]) -> list[TradeRow]:
    """Parse a header + data-rows table (from pdfplumber or an HTML table)
    into transaction dicts. Locates columns by header text rather than
    fixed position, since column order isn't perfectly consistent across
    years/chambers, and skips (never guesses) any row it can't confidently
    parse — a fabricated ticker/amount is worse than a missing row.
    """
    if not table:
        return []
    header = [(cell or "").strip().lower() for cell in table[0]]

    def _find_col(*keywords: str) -> int | None:
        for i, h in enumerate(header):
            if any(kw in h for kw in keywords):
                return i
        return None

    col_owner = _find_col("owner", "id#", "id #", "id")
    col_asset = _find_col("asset")
    col_type = _find_col("transaction type", "type")
    col_date = _find_col("transaction date", "date")
    col_notify = _find_col("notification date")
    col_amount = _find_col("amount")

    if col_asset is None or col_type is None or col_date is None or col_amount is None:
        # Not the transactions table (could be a cover page, filer info
        # block, etc.) — not a parse failure, just not what we're after.
        return []

    rows: list[TradeRow] = []
    for raw_row in table[1:]:
        if raw_row is None or len(raw_row) <= max(col_asset, col_type, col_date, col_amount):
            continue
        asset_cell = (raw_row[col_asset] or "").strip()
        type_cell = (raw_row[col_type] or "").strip()
        date_cell = (raw_row[col_date] or "").strip()
        amount_cell = (raw_row[col_amount] or "").strip()
        if not asset_cell or not type_cell or not date_cell:
            continue

        txn_type = classify_transaction_type(type_cell)
        txn_date = normalize_date(date_cell)
        amount_range = parse_amount_range(amount_cell)
        if txn_type is None or txn_date is None or amount_range is None:
            logger.debug("Skipping unparseable PTR row: %r", raw_row)
            continue

        owner_cell = (raw_row[col_owner] or "").strip().upper() if col_owner is not None else ""
        notify_cell = (raw_row[col_notify] or "").strip() if col_notify is not None else ""
        notify_date = normalize_date(notify_cell) or txn_date

        ticker_match = TICKER_RE.search(asset_cell)
        rows.append(TradeRow(
            ticker=ticker_match.group(1) if ticker_match else None,
            asset_name=asset_cell,
            owner=OWNER_CODES.get(owner_cell, "self"),
            transaction_type=txn_type,
            transaction_date=txn_date,
            disclosure_date=notify_date,
            amount_low=amount_range[0],
            amount_high=amount_range[1],
        ))
    return rows


def ocr_extract_rows(pdf: object) -> list[TradeRow]:
    """Best-effort OCR fallback for scanned (paper) PTR filings.

    Only reached when a PDF has no extractable text layer at all. OCR'd
    amounts/tickers are materially less reliable than a real text layer —
    callers must tag these rows with parse_confidence="ocr" rather than
    presenting them as equivalent to a text-layer parse.
    """
    try:
        import pytesseract
    except ImportError:
        logger.warning("pytesseract not available — cannot OCR scanned PTR")
        return []

    rows: list[TradeRow] = []
    for page in pdf.pages:
        try:
            img = page.to_image(resolution=200).original
            text = pytesseract.image_to_string(img)
        except Exception as e:
            logger.warning("OCR failed on PTR page: %s", e)
            continue
        # OCR output has no reliable table structure — extract only what
        # can be matched with reasonable confidence (ticker + amount range
        # pairs on the same line), and drop anything else rather than
        # guess at row boundaries.
        for line in text.splitlines():
            ticker_match = TICKER_RE.search(line)
            amount_range = parse_amount_range(line)
            txn_type = classify_transaction_type(line)
            dates = re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}", line)
            if not (ticker_match and amount_range and txn_type and dates):
                continue
            txn_date = normalize_date(dates[0])
            if txn_date is None:
                continue
            disclosure_date = normalize_date(dates[1]) if len(dates) > 1 else txn_date
            rows.append(TradeRow(
                ticker=ticker_match.group(1),
                asset_name=line.strip(),
                owner="self",
                transaction_type=txn_type,
                transaction_date=txn_date,
                disclosure_date=disclosure_date or txn_date,
                amount_low=amount_range[0],
                amount_high=amount_range[1],
            ))
    return rows


def parse_pdf_bytes(pdf_bytes: bytes) -> tuple[list[TradeRow], str]:
    """Parse a PTR PDF's bytes into (rows, confidence).

    Tries the text layer first (tables via pdfplumber); falls back to OCR
    only if no text layer exists at all (scanned/paper filings).
    """
    import io

    import pdfplumber

    rows: list[TradeRow] = []
    confidence = "text"
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        has_text = any((page.extract_text() or "").strip() for page in pdf.pages)
        if has_text:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    rows.extend(parse_table_rows(table))
        if not rows:
            confidence = "ocr"
            rows = ocr_extract_rows(pdf)
    return rows, confidence
