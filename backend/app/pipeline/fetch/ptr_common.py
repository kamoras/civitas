"""Shared parsing helpers for STOCK Act periodic transaction reports (PTRs).

Used by both house_ptr.py and senate_ptr.py — the underlying data (owner
codes, transaction-type vocabulary, amount-range formatting, date format)
is defined by the same federal disclosure form conventions in both chambers,
only the delivery mechanism (PDF vs. HTML) differs.
"""

import difflib
import logging
import re
import statistics
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage

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
    # None only for a scanned row whose date isn't legible (ocr_extract_rows,
    # keep_undated): everything else on it read.
    transaction_date: str | None
    disclosure_date: str
    amount_low: float
    amount_high: float
    parse_confidence: str = "text"
    source_url: str = ""
    filing_id: str = ""
    industry: str | None = None
    # "periodic" (a PTR / 278-T) or "annual" (a presidential 278e's Part 7,
    # which states no notification date: president_fd).
    report_kind: str = "periodic"

# Bump whenever a parser here reads the same filing differently: every
# stored trade an older version read — House, Senate and presidential — is
# read again (stock_pipeline._reread_trades), within a nightly budget.
# 2: owners printed as words, and an owner the form doesn't state is
# "unknown" rather than the filer.
# 3: scanned filings read as a table by word position, amounts only as the
# form's ranges, dates only inside the filing's window.
# 4: a presidential scanned row whose date alone is illegible is kept
# undated (ocr_extract_rows, keep_undated).
PARSER_VERSION = 4

# PTR owner codes -> our owner vocabulary (StockTrade.owner / RepStockTrade.owner).
OWNER_CODES = {"SP": "spouse", "DC": "dependent", "JT": "joint"}
# The Senate's eFD tables print the owner as a word instead (every value
# seen on file, 2026-09) — its annual reports and its electronic PTRs alike.
OWNER_WORDS = {
    "self": "self", "spouse": "spouse", "joint": "joint",
    "child": "dependent", "dependent child": "dependent", "dependent": "dependent",
}


def owner_from_cell(cell: str | None, *, blank: str = "self") -> str:
    """Our owner value for a form's owner cell; None when the table has no
    owner column at all, which states no owner ("unknown"). A blank cell is
    `blank`: the House's forms leave the column empty for the filer. A value
    that is neither a code nor a word the forms print is "unknown" too,
    never assumed to be the filer's — it may well be a spouse's or child's."""
    if cell is None:
        return "unknown"
    text = " ".join(cell.split())
    if not text:
        return blank
    owner = OWNER_CODES.get(text.upper()) or OWNER_WORDS.get(text.lower())
    if owner is None:
        logger.info("Unrecognized disclosure owner value %r", text)
        return "unknown"
    return owner

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
# a genuine 1-5 letter ticker, storing ticker="THE" instead. No real US
# equity ticker is the word "the".
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
# Without this, a single-figure cell fails to parse and the whole row is
# dropped, so a member's (or the president's) largest disclosed
# transactions become the ones silently missing from the record —
# precisely inverted from what a reader would assume a gap meant.
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


def parse_table_rows(table: list[list[str | None]], *, blank_owner: str = "self") -> list[TradeRow]:
    """Parse a header + data-rows table (from pdfplumber or an HTML table)
    into transaction dicts. Locates columns by header text rather than
    fixed position, since column order isn't perfectly consistent across
    years/chambers, and skips (never guesses) any row it can't confidently
    parse — a fabricated ticker/amount is worse than a missing row.
    `blank_owner` is what the form means by an empty owner cell (see
    owner_from_cell).
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

        owner_cell = (raw_row[col_owner] or "") if col_owner is not None else None
        notify_cell = (raw_row[col_notify] or "").strip() if col_notify is not None else ""
        notify_date = normalize_date(notify_cell) or txn_date

        rows.append(TradeRow(
            ticker=extract_ticker(asset_cell),
            asset_name=asset_cell,
            owner=owner_from_cell(owner_cell, blank=blank_owner),
            transaction_type=txn_type,
            transaction_date=txn_date,
            disclosure_date=notify_date,
            amount_low=amount_range[0],
            amount_high=amount_range[1],
        ))
    return rows


# The value ranges every periodic transaction report prints, the OGE 278-T
# and the House and Senate PTRs alike: a documented form convention, like
# OWNER_CODES. An amount read by OCR is accepted only as one of them. One
# misread bound ("$250,004 - $500,000") is recovered from the other; a
# pair matching neither is not a range the filing states, so the row is
# not read rather than stored with an amount nobody disclosed.
AMOUNT_BRACKETS = (
    (1_001, 15_000), (15_001, 50_000), (50_001, 100_000), (100_001, 250_000),
    (250_001, 500_000), (500_001, 1_000_000), (1_000_001, 5_000_000),
    (5_000_001, 25_000_000), (25_000_001, 50_000_000),
)
# The open-ended ranges: "Over $50,000,000", and "Over $1,000,000" for a
# spouse's or dependent child's asset.
OPEN_ENDED_FLOORS = (1_000_000, 50_000_000)

_OCR_NUMBER_RE = re.compile(r"\d[\d,]{2,}")
_OCR_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?!\d)")
_OCR_TYPES = {"purchase": "purchase", "sale": "sale_full", "exchange": "exchange"}
_AMOUNT_WORD_RE = re.compile(r"^[|\[{(]?\$\s?\d{1,3}(?:[,.]\d{3})+")


def form_bracket(text: str) -> tuple[float, float] | None:
    """The form's own amount range an OCR'd amount names, or None."""
    numbers = [int(n.replace(",", "")) for n in _OCR_NUMBER_RE.findall(text or "")]
    if numbers and numbers[0] in OPEN_ENDED_FLOORS and OPEN_ENDED_AMOUNT_RE.search(text):
        return float(numbers[0]), float(numbers[0])
    if len(numbers) < 2:
        return None
    for low, high in AMOUNT_BRACKETS:
        if numbers[0] == low or numbers[1] == high:
            return float(low), float(high)
    return None


def window_date(text: str, not_before: str | None, not_after: str | None) -> str | None:
    """The first M/D/YYYY date in OCR'd text as ISO, or None when there is
    none, it is no calendar date ("14/19/2025"), or it falls outside
    [not_before, not_after]: a transaction reported on a filing can't
    postdate the filing, nor predate the filer's office."""
    match = _OCR_DATE_RE.search(text or "")
    if not match:
        return None
    year = int(match[3]) + (2000 if len(match[3]) == 2 else 0)
    try:
        iso = datetime(year, int(match[1]), int(match[2])).strftime("%Y-%m-%d")
    except ValueError:
        return None
    if (not_before and iso < not_before) or (not_after and iso > not_after):
        return None
    return iso


def ocr_transaction_type(text: str) -> str | None:
    """The transaction type an OCR'd word spells, allowing the misreads
    real scans show ("purchaso", "salo", "Durchase"): the nearest of the
    form's three words, if close enough and nearly as long, so a fragment
    or a name ("CHASE", of JPMorgan Chase, 0.77 like "purchase") isn't read
    as a type."""
    letters = re.sub(r"[^a-z]", "", (text or "").lower())
    if len(letters) < 4:
        return None
    if "partial" in letters:
        return "sale_partial"
    ratios = {word: difflib.SequenceMatcher(None, letters, word).ratio() for word in _OCR_TYPES}
    word = max(ratios, key=ratios.get)
    return _OCR_TYPES[word] if ratios[word] >= 0.75 and len(letters) >= len(word) - 2 else None


# Matches one OCR'd line of a scanned form that the table reader below
# can't lay out (no transaction-type column found on the page): the asset,
# the type, the date, then the amount. Verified against real tesseract
# output on a live presidential filing (2026-08 audit): a leading row
# number OCRs as a stray letter as often as a digit, and gridlines and the
# notification column OCR as an inconsistent mix of "|", "]", "}", ":",
# ".", so the asset isn't anchored to a row number and the separators are
# skipped rather than matched. The amount is anchored to the segment
# after the date, which keeps a row number from being read as part of it.
_OCR_LINE_RE = re.compile(
    r"(?P<asset>[A-Za-z].*?)(?:\s|[\[\]{}|:.,])*(?P<type>purchase|sale(?:\s*\(partial\))?|exchange)\b[^\d$]*"
    r"(?P<date>\d{1,2}/\d{1,2}/\d{2,4})[^\d$]*"
    r"(?P<amount>\$?[\d,]+\s*[-~]+\s*\$?[\d,]+)",
    re.IGNORECASE,
)


def _parse_ocr_line(line: str, not_before: str | None = None, not_after: str | None = None) -> TradeRow | None:
    """One OCR'd line as a trade row, or None. There is no looser second
    try: it read whatever two numbers a line held as the amount and its
    first date as the transaction's, which on a bond was the maturity
    ("DUE 12/15/2078"), and stored the whole line as the asset (2026-09)."""
    match = _OCR_LINE_RE.search(line)
    if not match:
        return None
    txn_type = classify_transaction_type(match.group("type"))
    txn_date = window_date(match.group("date"), not_before, not_after)
    amount = form_bracket(match.group("amount"))
    if txn_type is None or txn_date is None or amount is None:
        return None
    asset_name = match.group("asset").strip(" |")
    return TradeRow(
        ticker=extract_ticker(asset_name),
        asset_name=asset_name,
        # The line pattern doesn't read an owner column, so the owner is
        # not stated rather than assumed to be the filer.
        owner="unknown",
        transaction_type=txn_type,
        transaction_date=txn_date,
        disclosure_date=not_after or txn_date,
        amount_low=amount[0],
        amount_high=amount[1],
    )


# Scans run 150-200 dpi; rendered at this, the form's print is large
# enough for tesseract's word boxes to hold one cell each.
_OCR_DPI = 300
# A dark run this long (0.4 inch at _OCR_DPI) is a ruling line of the
# form's table, not a stroke of any letter.
_RULE_PX = 120


def _ruled(image) -> tuple[Image.Image, list[float]]:
    """(the page with its table's ruling lines erased, the heights of its
    horizontal rules). Print that sits on a rule OCRs as noise: "INTL
    FLAVORS & FRAGRANCES INC" read as "__—dnurtavorsarmacrancesine", and 2
    of a page's 33 "sale"s were found, before the rules were taken out (31
    after). The horizontal rules are the table's rows: every filing rules
    each row, while where a row's number and text sit within it varies."""
    gray = np.asarray(ImageOps.grayscale(image))
    dark = gray < 160
    horizontal = ndimage.binary_opening(dark, structure=np.ones((1, _RULE_PX)))
    rules = horizontal | ndimage.binary_opening(dark, structure=np.ones((_RULE_PX, 1)))
    cleaned = gray.copy()
    cleaned[ndimage.binary_dilation(rules, iterations=2)] = 255
    # A table rule spans a good part of the page; a short one is an
    # underline or a form field.
    ys = np.flatnonzero(horizontal.sum(axis=1) > gray.shape[1] * 0.4)
    heights = [float(np.mean(run)) for run in np.split(ys, np.flatnonzero(np.diff(ys) > 3) + 1) if len(run)]
    return Image.fromarray(cleaned), heights


def _ocr_cell(image, box: tuple[int, int, int, int], whitelist: str, scale: int = 1) -> str:
    """One table cell read on its own, as a single line of the characters
    it can hold; `scale` enlarges it first, for print too small to read."""
    import pytesseract

    cell = image.crop(box)
    if scale > 1:
        cell = cell.resize((cell.width * scale, cell.height * scale), Image.LANCZOS)
    return pytesseract.image_to_string(
        cell, config=f"--psm 7 -c tessedit_char_whitelist={whitelist}",
    ).strip()


def _ocr_table_page(page, not_before: str | None, not_after: str | None) -> tuple[list[TradeRow], int] | None:
    """A scanned page's transactions, read by where each word sits: the
    form is a ruled table, but tesseract's plain text reads its columns as
    separate blocks (every description, then every type and date, then
    every amount), so row order is lost before any line can be parsed.

    Each row is found by its transaction-type word (the column is located
    from where those words cluster) or its row number, and its description
    is the words left of the type column on its line. The date and amount
    cells are read again on their own and must be a real date inside the
    filing's window and one of the form's ranges; a row that isn't both is
    counted, not guessed. Returns (rows, rows not read), or None when the
    page has no type column (not a transaction table)."""
    import pytesseract

    image, rule_heights = _ruled(page.to_image(resolution=_OCR_DPI).original)
    width = image.size[0]
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config="--psm 11")
    words = [
        {"t": text.strip(), "x": data["left"][i], "y": data["top"][i], "w": data["width"][i], "h": data["height"][i]}
        for i, text in enumerate(data["text"]) if text.strip()
    ]
    types = [w for w in words if ocr_transaction_type(w["t"])]
    if not types:
        return None
    # The type column is where most type words sit: a stray match elsewhere
    # (a description's "exchange") doesn't move it.
    near = width * 0.03
    type_x = max((w["x"] for w in types), key=lambda x: sum(abs(o["x"] - x) < near for o in types))
    types = [w for w in types if abs(w["x"] - type_x) < near]
    line = statistics.median(w["h"] for w in types)
    type_right = max(w["x"] + w["w"] for w in types)
    # An amount word is a comma-grouped figure: a date misread with a "$"
    # ("$/9/2025") is not, and taking it for one put the amount column at
    # the date's.
    amount_x = min((w["x"] for w in words if w["x"] > type_right and _AMOUNT_WORD_RE.search(w["t"])), default=None)
    if amount_x is None:
        return None
    dates = [w["x"] for w in words if "/" in w["t"] and type_right < w["x"] < amount_x]
    date_x = statistics.median(dates) if dates else type_right
    # The row-number column is left of the Description header, wherever a
    # scan's margin puts it (6% of the page's width on one, 15% on another).
    heading = next((w for w in words if w["t"].lower().startswith("description")), None)
    number_right = heading["x"] if heading else width * 0.06
    header = heading["y"] if heading else 0
    numbers = [w for w in words if re.fullmatch(r"\d{1,4}", w["t"]) and w["x"] < number_right and w["y"] > header]
    desc_left = max((w["x"] + w["w"] for w in numbers), default=number_right) + 5
    # A row is the space between two of the table's rules, one line of text
    # tall at least. A page without them (none has been seen) is read by
    # the rows its type words and numbers name.
    regions = [(a, b) for a, b in zip(rule_heights, rule_heights[1:]) if line * 0.9 <= b - a <= line * 8]
    if len(regions) < 3:
        centers: list[float] = []
        for center in sorted(w["y"] + w["h"] / 2 for w in types + numbers):
            if not centers or center - centers[-1] >= line * 0.8:
                centers.append(center)
        regions = [(c - line * 0.9, c + line * 0.9) for c in centers]

    rows: list[TradeRow] = []
    unread = 0
    for region_top, region_bottom in regions:
        band = sorted((w for w in words if region_top <= w["y"] + w["h"] / 2 < region_bottom), key=lambda w: w["x"])
        # The row's text line: its type word's, else the middle of its words.
        on_type = [w for w in band if abs(w["x"] - type_x) < near and ocr_transaction_type(w["t"])]
        texts = on_type or [w for w in band if w["x"] > desc_left]
        if not texts:
            continue
        center = statistics.median(w["y"] + w["h"] / 2 for w in texts)
        asset = " ".join(w["t"] for w in band if desc_left < w["x"] < type_x - line).strip(" |[]{}")
        top, bottom = int(center - line * 1.1), int(center + line * 1.1)
        txn_type = next((t for w in band if abs(w["x"] - type_x) < near and (t := ocr_transaction_type(w["t"]))), None)
        if txn_type is None:
            letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ()"
            txn_type = ocr_transaction_type(_ocr_cell(image, (int(type_x - line), top, int(type_right + line), bottom), letters))
        if any("partial" in w["t"].lower() for w in band if w["x"] > type_x - line):
            txn_type = "sale_partial"
        if not asset and txn_type is None:
            continue
        # From the type column's edge: a filing that right-aligns its dates
        # in a wide column starts each a different distance from the
        # median, and a cell cut at the median lost the month ("23/2026").
        date_box = (int(type_right + line / 2), top, int(date_x + (amount_x - date_x) * 0.55), bottom)
        # Enlarged when the first reading isn't a date: one filing prints
        # the table at half size, and tesseract drops digits from it.
        txn_date = window_date(_ocr_cell(image, date_box, "0123456789/"), not_before, not_after) or window_date(
            _ocr_cell(image, date_box, "0123456789/", scale=2), not_before, not_after,
        ) or window_date(
            " ".join(w["t"] for w in band if type_right < w["x"] < amount_x), not_before, not_after,
        )
        cell_amount = _ocr_cell(image, (int(amount_x - line), top, int(width * 0.98), bottom), "0123456789,$-Ovr ")
        amount = form_bracket(cell_amount) or form_bracket(" ".join(w["t"] for w in band if w["x"] >= amount_x - line))
        # A row whose date alone didn't read is kept undated; the caller
        # decides whether it is used (ocr_extract_rows, keep_undated).
        if not (asset and txn_type and amount):
            unread += 1
            continue
        rows.append(TradeRow(
            ticker=extract_ticker(asset),
            asset_name=asset,
            owner="unknown",  # the form's rows state no owner
            transaction_type=txn_type,
            transaction_date=txn_date,
            disclosure_date=not_after or txn_date or "",
            amount_low=amount[0],
            amount_high=amount[1],
        ))
    return rows, unread


def ocr_extract_rows(
    pdf: object, not_before: str | None = None, not_after: str | None = None, *, keep_undated: bool = False,
) -> list[TradeRow]:
    """Best-effort OCR for scanned (paper) PTR filings, reached only when a
    PDF has no text layer at all. Each page is read as a table
    (_ocr_table_page), or line by line when it has no transaction-type
    column. OCR'd rows are materially less reliable than a text layer:
    callers tag them parse_confidence="ocr", and a transaction date read
    by OCR supports no timeliness figure (schemas.StockTradeSchema).

    `keep_undated`: keep a table row whose asset, type and amount read but
    whose date didn't, with transaction_date None. One presidential 278-T
    (May 8, 2026) was scanned at 150 dpi and printed at half size: its dates
    are ~6 px tall, and no reading of them was reliable (the best, matching
    rendered candidates, was right 38% of the time with no usable
    confidence), while its assets, types and amounts read."""
    try:
        import pytesseract
    except ImportError:
        logger.warning("pytesseract not available — cannot OCR scanned PTR")
        return []

    rows: list[TradeRow] = []
    unread = 0
    for page in pdf.pages:
        try:
            table = _ocr_table_page(page, not_before, not_after)
            if table is None:
                text = pytesseract.image_to_string(page.to_image(resolution=_OCR_DPI).original)
                rows.extend(r for line in text.splitlines() if (r := _parse_ocr_line(line, not_before, not_after)))
                continue
            undated = [r for r in table[0] if r.transaction_date is None]
            rows.extend(r for r in table[0] if r.transaction_date is not None or keep_undated)
            unread += table[1] + (0 if keep_undated else len(undated))
        except Exception as e:
            logger.warning("OCR failed on PTR page: %s", e)
    if unread:
        logger.warning("OCR: %d transaction rows of a scanned PTR could not be read (%d read)", unread, len(rows))
    if undated := sum(r.transaction_date is None for r in rows):
        logger.info("OCR: %d rows kept without a legible date", undated)
    return rows


def parse_pdf_bytes(
    pdf_bytes: bytes, *, blank_owner: str = "self", not_before: str | None = None, not_after: str | None = None,
    keep_undated: bool = False,
) -> tuple[list[TradeRow], str]:
    """Parse a PTR PDF's bytes into (rows, confidence).

    Tries the text layer first (tables via pdfplumber); falls back to OCR
    only if no text layer exists at all (scanned/paper filings).
    `blank_owner`: see parse_table_rows. `not_before`/`not_after`: the
    window an OCR'd transaction date must fall in (window_date).
    `keep_undated`: see ocr_extract_rows.
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
                    rows.extend(parse_table_rows(table, blank_owner=blank_owner))
        if not rows:
            confidence = "ocr"
            rows = ocr_extract_rows(pdf, not_before, not_after, keep_undated=keep_undated)
    return rows, confidence
