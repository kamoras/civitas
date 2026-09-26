"""Fetch + parse House annual Financial Disclosure reports (asset holdings).

Source: the same House Clerk yearly index house_ptr.py reads
(disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip), filtered
to annual reports ("O") and their amendments ("A") instead of periodic
transaction reports. Each filing is a PDF under financial-pdfs/{year}/.

Only Schedule A (Assets and "Unearned" Income) is read. Its rows are parsed
from word positions, not pdfplumber's table extraction: the table has no
ruling lines and alternately-shaded rows, and extract_tables() silently drops
most of them (live-checked 2026-09 — on one 22-page report it returned 1 row
of 9 on the first page). Word positions are unambiguous because every column
starts at the x offset of its header word, and every asset ends with its
bracketed asset-type code ("[ST]"), which is what closes a row.

A scanned paper filing has no text layer and is reported as unparsed rather
than OCR'd: Schedule A's wrapped multi-line cells don't survive OCR well
enough to attribute a value bracket to the right asset, and a misattributed
value is worse than a link to the filing.
"""

import io
import logging
import re
from dataclasses import asdict, dataclass

import httpx
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.fd_common import (
    HoldingRow,
    house_category,
    parse_holding_value,
    split_account,
    strip_house_code,
    ticker_for,
)
from app.pipeline.fetch.house_ptr import _rate_limiter, fetch_filing_index
from app.pipeline.fetch.http_utils import fetch_bytes_with_retry
from app.pipeline.fetch.ptr_common import OWNER_CODES

logger = logging.getLogger(__name__)

# Annual report and its amendment. Candidate, termination and new-filer
# reports list assets too, but a sitting member's current picture is the
# annual report; the others either predate office or describe leaving it.
ANNUAL_FILING_TYPES = {"O", "A"}

_CACHE_TIER = "house_fd"
_FILING_MAX_AGE_HOURS = 24 * 30

# Rows start at the asset column; a new value bracket starts with a dollar
# figure followed by the bracket's dash, or one of the form's non-bracket
# values. A wrapped bracket's second line ("$50,000") has no dash, which is
# how a continuation is told apart from a new row's value.
_VALUE_START_RE = re.compile(r"^(\$[\d,]+\s*-|none\b|over\b|spouse|undetermined)", re.I)
# The form prints detail lines ("Location:", "Description:", "Comments:")
# under an asset in a smaller font whose label glyphs don't extract, leaving
# a token like "D          :" — a single letter, filler, and a colon.
_DETAIL_LABEL_RE = re.compile(r"^[A-Z][^A-Za-z0-9]*:$")
# Body text on this form is 9pt; detail lines and footnotes are 8.5pt and
# below, section headings 12pt.
_BODY_MIN_SIZE = 8.8
# A token ending in an asset-type code. Usually its own token ("[ST]"), but
# some filers type it straight onto the name ("Fixed Fund[MF]").
_CODE_TOKEN_RE = re.compile(r"\[[0-9A-Z]{2}\]$")
_HEADING_MIN_SIZE = 11.0
_LINE_TOLERANCE = 2.0


def _group_lines(words: list[dict]) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(word["top"] - lines[-1][0]["top"]) <= _LINE_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


class _OpenRow:
    def __init__(self) -> None:
        self.asset: list[str] = []
        self.owner: list[str] = []
        self.value: list[str] = []
        self.closed = False  # asset-type code seen

    def to_holding(self) -> HoldingRow | None:
        asset_text = " ".join(self.asset).strip()
        if not asset_text:
            return None
        asset_text, code = strip_house_code(asset_text)
        account, name = split_account(asset_text)
        if not name:
            return None
        value_text = " ".join(self.value).strip()
        low, high = parse_holding_value(value_text)
        owner_code = " ".join(self.owner).strip().upper()
        return HoldingRow(
            asset_name=name,
            asset_type=code or "",
            category=house_category(code),
            owner=OWNER_CODES.get(owner_code, "self"),
            value_text=value_text,
            value_low=low,
            value_high=high,
            account=account,
            ticker=ticker_for(name),
        )


def parse_schedule_a(pages_words: list[list[dict]]) -> list[HoldingRow] | None:
    """Parse Schedule A from each page's words (pdfplumber extract_words
    with extra_attrs=["size"]).

    Returns None when Schedule A wasn't found at all — not a House annual
    report layout, or no text layer — so the caller can tell "the member
    disclosed no assets" (an empty list: the schedule is present and says
    "None disclosed.") from "this couldn't be read".
    """
    holdings: list[HoldingRow] = []
    saw_header = False
    in_schedule = False
    columns: tuple[float, float, float] | None = None  # owner, value, income x-starts
    row: _OpenRow | None = None

    def flush() -> None:
        nonlocal row
        if row is not None:
            holding = row.to_holding()
            if holding is not None:
                holdings.append(holding)
        row = None

    for words in pages_words:
        for line in _group_lines(words):
            texts = [w["text"] for w in line]
            size = max(w.get("size", 0) for w in line)

            if texts[0] == "Asset" and "Owner" in texts:
                # A table header. Schedule A's is "Asset Owner Value of Asset
                # Income Type(s) Income ..." and repeats at the top of every
                # page it continues onto; any other schedule's header ends it.
                if "Value" in texts and "Income" in texts and "Date" not in texts:
                    saw_header = True
                    in_schedule = True
                    x = {w["text"]: w["x0"] for w in reversed(line)}
                    columns = (x["Owner"], x["Value"], x["Income"])
                else:
                    flush()
                    in_schedule = False
                continue
            if size >= _HEADING_MIN_SIZE:
                # Section headings. Schedule A's own heading is enough to
                # know the schedule was read: when it lists nothing, the
                # form prints "None disclosed." and no table header at all.
                flush()
                in_schedule = False
                if len(texts) > 1 and texts[1] == "A:":
                    saw_header = True
                continue
            if not in_schedule or columns is None:
                continue
            if size < _BODY_MIN_SIZE or _DETAIL_LABEL_RE.match(texts[0]) or texts[0].startswith("*"):
                continue

            owner_x, value_x, income_x = columns
            asset = [w["text"] for w in line if w["x0"] < owner_x - 2]
            owner = [w["text"] for w in line if owner_x - 2 <= w["x0"] < value_x - 2]
            value = [w["text"] for w in line if value_x - 2 <= w["x0"] < income_x - 2]
            # Matched on the joined cell: the bracket's dash is its own word.
            value_starts = bool(_VALUE_START_RE.match(" ".join(value)))

            # A bare repeated code on a wrapped line ("[MF]" alone) is not a
            # new asset's name.
            names_asset = any(not re.fullmatch(r"\[[0-9A-Z]{2}\]", t) for t in asset)
            if row is not None and names_asset and (row.closed or (value_starts and row.value)):
                # A closed row (its asset-type code seen) followed by more
                # asset text is the next asset. A row with no code at all is
                # closed by the next row's value bracket instead.
                flush()
            if row is None:
                if not asset and not value:
                    continue
                row = _OpenRow()
            row.asset.extend(asset)
            row.owner.extend(owner)
            row.value.extend(value)
            if any(_CODE_TOKEN_RE.search(t) for t in asset):
                row.closed = True

    flush()
    return holdings if saw_header else None


@dataclass
class AnnualReport:
    """One parsed annual report: who it says the filer is, and Schedule A.

    holdings is None when Schedule A couldn't be read (scanned paper filing,
    unrecognized layout).
    """
    filer_status: str | None
    holdings: list[HoldingRow] | None


def filer_status(pages_words: list[list[dict]]) -> str | None:
    """The cover page's "Status:" value ("Member", "Congressional Candidate").

    The yearly index doesn't carry it, and a candidate running for a seat
    files the same annual report type as the member holding it — so a
    candidate sharing the member's surname and district would otherwise be
    matched to the member by name.
    """
    for words in pages_words[:1]:
        for line in _group_lines(words):
            if line and line[0]["text"] == "Status:":
                return " ".join(w["text"] for w in line[1:]).strip() or None
    return None


def parse_annual_pdf(pdf_bytes: bytes) -> AnnualReport:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages_words = [page.extract_words(extra_attrs=["size"]) for page in pdf.pages]
    if not any(pages_words):
        return AnnualReport(None, None)  # scanned paper filing — no text layer
    return AnnualReport(filer_status(pages_words), parse_schedule_a(pages_words))


async def fetch_annual_filing_index(
    client: httpx.AsyncClient, db: Session, year: int,
) -> list[dict]:
    """Annual reports (and amendments) for calendar year `year`."""
    return await fetch_filing_index(
        client, db, year, filing_types=ANNUAL_FILING_TYPES, pdf_dir="financial-pdfs",
        cache_key=f"annual-index-{year}",
    )


async def fetch_and_parse_annual(
    client: httpx.AsyncClient, db: Session, filing: dict,
) -> AnnualReport | None:
    """Download and parse one annual report.

    None when the PDF couldn't be fetched or opened at all. Otherwise an
    AnnualReport whose holdings are None when Schedule A couldn't be read
    and an empty list when it was read and lists no assets.
    """
    cache_key = f"annual-parsed-{filing['doc_id']}"
    cached = api_cache_get(db, _CACHE_TIER, cache_key, max_age_hours=_FILING_MAX_AGE_HOURS)
    if cached is not None and "holdings" in cached:
        holdings = cached["holdings"]
        return AnnualReport(
            cached.get("filer_status"),
            [HoldingRow(**row) for row in holdings] if holdings is not None else None,
        )

    pdf_bytes = await fetch_bytes_with_retry(
        client, _rate_limiter, filing["pdf_url"], "House Clerk",
        headers=None, rate_limit_backoff_multiplier=2.0, retry_on_4xx=False,
    )
    if pdf_bytes is None:
        return None

    try:
        report = parse_annual_pdf(pdf_bytes)
    except Exception as e:
        logger.error("Failed to parse House annual report %s: %s", filing["pdf_url"], e)
        return None

    # A filed report never changes (an amendment is its own filing), so a
    # scanned one is cached as unreadable too rather than re-downloaded
    # every run.
    api_cache_set(
        db, _CACHE_TIER, cache_key,
        {
            "filer_status": report.filer_status,
            "holdings": [asdict(h) for h in report.holdings] if report.holdings is not None else None,
        },
        normal_ttl_hours=_FILING_MAX_AGE_HOURS,
    )
    return report
