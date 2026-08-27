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
# capitalization/spacing across years and between chambers. The House's own
# electronic PTR form (verified live, 2026-07) prints the form's official
# single-letter code (P/S/E) in this column, not the spelled-out word — a
# leading `^\s*<letter>\b` alternative catches that, anchored to the start
# so it can't false-match a stray letter inside unrelated text (this column
# is already isolated to the Transaction Type header by _find_col, so its
# value is always just the code, not free text). This was silently
# skipping every single transaction row on every House filing — the
# "sale"/"purchase"/"exchange" word patterns never matched the actual "S
# (partial)"/"P"/"E" values the form prints, and a row with no classifiable
# type is (correctly) dropped as unparseable rather than guessed at.
TXN_TYPE_PATTERNS = [
    (re.compile(r"^\s*p\b|purchase", re.I), "purchase"),
    (re.compile(r"^\s*s\s*\(partial\)|sale.*\(partial\)|partial.*sale", re.I), "sale_partial"),
    (re.compile(r"^\s*s\b|sale.*\(full\)|sale", re.I), "sale_full"),
    (re.compile(r"^\s*e\b|exchange", re.I), "exchange"),
]

TICKER_RE = re.compile(r"\(([A-Z]{1,5})\)")
AMOUNT_RE = re.compile(r"\$?([\d,]+)")

# Parenthetical suffixes real company names carry ("Kroger Co (The)",
# "Cigna Group (The)") that TICKER_RE's own shape can't distinguish from
# a genuine 1-5 letter ticker — confirmed live on a presidential 278-T
# (2026-08 audit): "KROGER CO (THE)" and "CIGNA GROUP (THE)" both stored
# ticker="THE". No real US equity ticker is the word "the".
_NON_TICKER_PARENS = {"THE"}


def extract_ticker(text: str) -> str | None:
    match = TICKER_RE.search(text or "")
    if not match or match.group(1) in _NON_TICKER_PARENS:
        return None
    return match.group(1)

# The highest bracket on every one of these forms is open-ended — printed
# as "Over $50,000,000", "$50,000,001 +", or "$50,000,001 or more" — so it
# carries one figure where every other bracket carries two. Form vocabulary,
# the same documented-data-format exception OWNER_CODES and
# TXN_TYPE_PATTERNS above already rely on.
#
# Until 2026-07-31 a single-figure cell simply failed to parse and the whole
# row was dropped, so a member's (or the president's) largest disclosed
# transactions were the ones silently missing from the record — precisely
# inverted from what a reader would assume a gap meant.
OPEN_ENDED_AMOUNT_RE = re.compile(r"\bover\b|\bor more\b|\+\s*$", re.I)


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
    """Parse a disclosed amount bracket into (low, high).

    An open-ended top bracket ("Over $50,000,000") returns (low, low) —
    high == low is this codebase's encoding for "the form disclosed a floor
    and no ceiling," and is what StockTradeSchema.amount_open_ended keys
    off. It is deliberately not a fabricated upper bound: no real bracket
    on any of these forms has equal bounds, so the equality is unambiguous,
    and every consumer that shows a range shows this one as "$X+" rather
    than inventing a ceiling the filing never stated.
    """
    matches = AMOUNT_RE.findall(text or "")
    if not matches:
        return None
    try:
        low = float(matches[0].replace(",", ""))
        if len(matches) < 2:
            return (low, low) if OPEN_ENDED_AMOUNT_RE.search(text or "") else None
        return (low, float(matches[1].replace(",", "")))
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
        # Exact header matches win before substring matches, and earlier
        # keywords win over later ones. The old single-pass "first header
        # containing any keyword" binding meant an "Asset Type" column
        # appearing before "Type" captured _find_col("transaction type",
        # "type") — every row's type cell then read "Stock"/"Bond",
        # classify_transaction_type returned None, and the whole filing was
        # silently skipped as unparseable.
        for kw in keywords:
            for i, h in enumerate(header):
                if h == kw:
                    return i
        for kw in keywords:
            for i, h in enumerate(header):
                if kw in h:
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

        rows.append(TradeRow(
            ticker=extract_ticker(asset_cell),
            asset_name=asset_cell,
            owner=OWNER_CODES.get(owner_cell, "self"),
            transaction_type=txn_type,
            transaction_date=txn_date,
            disclosure_date=notify_date,
            amount_low=amount_range[0],
            amount_high=amount_range[1],
        ))
    return rows


# Matches one OCR'd transaction line on a scanned 278-T form: the asset
# name, the transaction type, the date, then the $low - $high bracket.
# Verified against real tesseract output on a live presidential filing
# (2026-08 audit) rather than an assumed-clean layout — real OCR noise
# is much messier than the form's own printed structure: a leading row
# number just as often OCRs as a stray letter/symbol ("s Howmet...",
# "« (es Centerpoint...") as a digit, and table gridlines and the
# "Yes/No" notified-within-30-days column OCR as an inconsistent mix of
# "|", "]", "}", ":", "." in no fixed position. Two design choices follow
# from that:
#   - `asset` isn't anchored to a leading row number at all — it's left
#     unconstrained on the left and required to START with a letter, so
#     a leading digit/symbol token (real or misread) is simply outside
#     the match rather than needing to be recognized and stripped.
#   - Everything between the asset and the type keyword, and again
#     between the date and the amount, is skipped rather than matched
#     against an enumerated set of expected characters — a bracket can
#     OCR glued directly onto the next word with no space at all
#     ("CORPORATION [purchase"), and the amount separator itself isn't
#     always a single "-" ("$15,001-- $50,000").
# Anchoring the amount pair to this trailing segment (rather than
# scanning the whole line, as the old fallback below still does) is what
# keeps a leading row number from being read as part of the dollar
# amount — confirmed live: "1 Goldman Sachs Group Inc purchase
# 6/23/2026) No] $1,001 - $15,000" first extracted (1, 6) as the amount
# pair (from the row number and the date) before this fix.
_OCR_LINE_RE = re.compile(
    r"(?P<asset>[A-Za-z].*?)(?:\s|[\[\]{}|:.,])*(?P<type>purchase|sale(?:\s*\(partial\))?|exchange)\b[^\d$]*"
    r"(?P<date>\d{1,2}/\d{1,2}/\d{2,4})[^\d$]*"
    r"\$?(?P<low>[\d,]+)\s*[-~]+\s*\$?(?P<high>[\d,]+)",
    re.IGNORECASE,
)


def _parse_ocr_line(line: str) -> TradeRow | None:
    """One structured attempt, then one loose fallback, at parsing a
    single OCR'd line into a trade row. Never guesses a row boundary —
    both paths still require a real date and a real amount pair before
    accepting anything."""
    match = _OCR_LINE_RE.search(line)
    if match:
        txn_type = classify_transaction_type(match.group("type"))
        txn_date = normalize_date(match.group("date"))
        if txn_type is None or txn_date is None:
            return None
        asset_name = match.group("asset").strip(" |")
        try:
            low, high = float(match.group("low").replace(",", "")), float(match.group("high").replace(",", ""))
        except ValueError:
            return None
        if low > high:
            # A misread digit in one bound (confirmed live: "$31,001 -
            # $15,000") is a fact about tesseract, not about the filing —
            # every real bracket on this form has low <= high, so this
            # is dropped rather than stored as a disclosed range it
            # never was.
            return None
        return TradeRow(
            ticker=extract_ticker(asset_name),
            asset_name=asset_name,
            owner="self",
            transaction_type=txn_type,
            transaction_date=txn_date,
            disclosure_date=txn_date,
            amount_low=low,
            amount_high=high,
        )

    # Loose fallback for a differently-formatted scan (older years, a
    # different agency scanner) that doesn't match this form's exact
    # column order — something is better than nothing, but a ticker is
    # no longer required to accept the row (see extract_ticker: this
    # form prints no ticker at all, so requiring one silently dropped
    # nearly every real row rather than corrupting a few).
    amount_range = parse_amount_range(line)
    txn_type = classify_transaction_type(line)
    dates = re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}", line)
    if not (amount_range and txn_type and dates):
        return None
    if amount_range[0] > amount_range[1]:
        return None
    txn_date = normalize_date(dates[0])
    if txn_date is None:
        return None
    disclosure_date = normalize_date(dates[1]) if len(dates) > 1 else txn_date
    return TradeRow(
        ticker=extract_ticker(line),
        asset_name=line.strip(),
        owner="self",
        transaction_type=txn_type,
        transaction_date=txn_date,
        disclosure_date=disclosure_date or txn_date,
        amount_low=amount_range[0],
        amount_high=amount_range[1],
    )


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
        for line in text.splitlines():
            row = _parse_ocr_line(line)
            if row is not None:
                rows.append(row)
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
