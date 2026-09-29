"""Fetch + parse House STOCK Act periodic transaction reports (PTRs).

Source: the House Clerk's official financial disclosure system
(disclosures-clerk.house.gov). There is no structured transaction-level API —
the yearly ZIP only indexes *filings* (who, when, a PDF link); the actual
buy/sell/ticker/amount data lives inside each filing's PDF and must be
parsed. Electronic filings (the large majority since ~2012) have a real text
layer; older paper filings are scanned images and fall back to OCR (see
ptr_common.parse_pdf_bytes).

See issue #45 and the plan at the time of writing for the source-selection
rationale (House/Senate Stock Watcher, the two previously-proposed
shortcuts, are both dead as of 2026-07).
"""

import io
import logging
import zipfile
from dataclasses import asdict

import httpx
from defusedxml import ElementTree
from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import fetch_bytes_with_retry
from app.pipeline.fetch.ptr_common import TradeRow, normalize_date, parse_pdf_bytes
from app.pipeline.fetch.ptr_common import PARSER_VERSION as PTR_PARSER_VERSION
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

CLERK_BASE = "https://disclosures-clerk.house.gov/public_disc"

_rate_limiter = RateLimiter(settings.HOUSE_PTR_RPS)


async def fetch_ptr_filing_index(
    client: httpx.AsyncClient, db: Session, year: int,
) -> list[dict]:
    """Fetch and parse the yearly House financial disclosure index.

    Returns one dict per Periodic Transaction Report filing:
    {last, first, state_district, filing_date, doc_id, pdf_url}.
    The index itself never carries transaction-level data — see module
    docstring.
    """
    # An index that couldn't be loaded means no PTRs this run; the next run
    # asks again.
    return await fetch_filing_index(client, db, year, filing_types={"P"}, pdf_dir="ptr-pdfs") or []


async def fetch_filing_index(
    client: httpx.AsyncClient, db: Session, year: int, *,
    filing_types: set[str], pdf_dir: str,
) -> list[dict] | None:
    """The yearly index filtered to `filing_types`, with each filing's PDF
    link built under `pdf_dir`. Shared by the PTR ingest ("P" filings under
    ptr-pdfs/) and the annual holdings ingest (house_fd.py: annual reports
    and their amendments under financial-pdfs/).

    The whole index is cached once per year and filtered on read, so the
    two ingests — which both want last year's — share one download of the
    multi-megabyte ZIP instead of each fetching it through the Clerk's
    1 req/s limit. None when the index couldn't be loaded — not the same
    thing as a year with no filings of these types.
    """
    entries = await _fetch_index_entries(client, db, year)
    if entries is None:
        return None
    return [
        {**entry, "pdf_url": f"{CLERK_BASE}/{pdf_dir}/{year}/{entry['doc_id']}.pdf"}
        for entry in entries
        if entry["filing_type"] in filing_types
    ]


async def _fetch_index_entries(client: httpx.AsyncClient, db: Session, year: int) -> list[dict] | None:
    """Every filing in the yearly index, whatever its type; None when it
    couldn't be downloaded or read (not cached, so the next run retries)."""
    cache_key = f"fd-index-{year}"
    cached = api_cache_get(db, "house_ptr", cache_key)
    if cached is not None:
        return cached

    # headers=None: this site has always been fetched with httpx's own
    # bare defaults, never BROWSER_HEADERS (fetch_bytes_with_retry's own
    # default) -- preserved explicitly rather than silently picked up as
    # a side effect of sharing this helper with callers that do want it.
    zip_bytes = await fetch_bytes_with_retry(
        client, _rate_limiter, f"{CLERK_BASE}/financial-pdfs/{year}FD.zip", "House Clerk",
        headers=None, rate_limit_backoff_multiplier=2.0, retry_on_4xx=False,
    )
    if zip_bytes is None:
        return None

    entries: list[dict] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            xml_name = next((n for n in zf.namelist() if n.lower().endswith(".xml")), None)
            if xml_name is None:
                logger.error("House FD %d ZIP contained no XML index", year)
                return None
            root = ElementTree.fromstring(zf.read(xml_name))
    except (zipfile.BadZipFile, ElementTree.ParseError) as e:
        logger.error("Failed to parse House FD %d index: %s", year, e)
        return None

    for member in root.findall("Member"):
        doc_id = (member.findtext("DocID") or "").strip()
        if not doc_id:
            continue
        entries.append({
            "last": (member.findtext("Last") or "").strip(),
            "first": (member.findtext("First") or "").strip(),
            # "Hon." for a sitting member; candidates file with "Mr."/"Dr."
            # or nothing (see holdings_pipeline's use of it).
            "prefix": (member.findtext("Prefix") or "").strip(),
            "state_district": (member.findtext("StateDst") or "").strip(),
            "filing_type": (member.findtext("FilingType") or "").strip(),
            "year": year,
            "filing_date": normalize_date(member.findtext("FilingDate") or ""),
            "doc_id": doc_id,
        })

    api_cache_set(db, "house_ptr", cache_key, entries)
    return entries


def _looks_like_pdf(body: bytes) -> bool:
    """The PDF header, "%PDF-", within the first KB, after nothing but
    binary or whitespace bytes. Every Clerk PDF seen so far starts with it
    at byte 0 (126 of 126 downloaded, 2026-09); the spec allows leading
    bytes, so binary ones (a BOM, stray NULs) are tolerated. Printable text
    is not: that is what an HTML, JSON or plain-text error page quoting the
    string has before it, and taking one for a report would also let the
    outage probe vouch for a source that is down."""
    at = body.find(b"%PDF-", 0, 1024)
    return at != -1 and all(b < 0x21 or b > 0x7E for b in body[:at])


async def download_pdf(client: httpx.AsyncClient, url: str, headers: dict | None = None) -> bytes | None:
    """The one Clerk download policy, for every House PDF fetch (PTRs,
    annual reports, the annual-report probe): the same limiter and retries,
    and a body that isn't a PDF is no download. A 200 HTML page (a block,
    challenge or maintenance page) in a PDF's place is the source failing,
    not a document the parser can't read."""
    body = await fetch_bytes_with_retry(
        client, _rate_limiter, url, "House Clerk",
        headers=headers, rate_limit_backoff_multiplier=2.0, retry_on_4xx=False,
    )
    if body is not None and not _looks_like_pdf(body):
        logger.warning("House Clerk served something other than a PDF for %s", url)
        return None
    return body


async def fetch_and_parse_ptr(
    client: httpx.AsyncClient, db: Session, filing: dict,
) -> list[TradeRow]:
    """Download and parse one PTR PDF into transaction rows.

    Returns rows tagged with parse_confidence ("text" or "ocr"). Returns an
    empty list if the PDF can't be fetched or no transaction table is
    found — never fabricates a row.
    """
    cache_key = f"ptr-parsed-v{PTR_PARSER_VERSION}-{filing['doc_id']}"
    cached = api_cache_get(db, "house_ptr", cache_key, max_age_hours=24 * 30)
    if cached is not None:
        return [TradeRow(**row) for row in cached]

    pdf_bytes = await download_pdf(client, filing["pdf_url"])
    if pdf_bytes is None:
        return []

    # A scan's dates must fall on or before its filing date; a row whose
    # date alone isn't legible is kept undated (ptr_common.ocr_extract_rows),
    # with the filing date as its disclosure date.
    filed = filing.get("filing_date") or None
    try:
        rows, confidence = parse_pdf_bytes(pdf_bytes, not_after=filed, keep_undated=True)
    except Exception as e:
        logger.error("Failed to parse PTR PDF %s: %s", filing["pdf_url"], e)
        return []

    for row in rows:
        if not row.disclosure_date and filed:
            row.disclosure_date = filed
        row.parse_confidence = confidence
        row.source_url = filing["pdf_url"]
        row.filing_id = filing["doc_id"]

    # The API cache stores plain JSON, not dataclasses — convert at this
    # boundary and reconstruct on the cache-hit path above. normal_ttl_hours
    # must match the read's own max_age_hours above (30 days) — see
    # api_cache_set's docstring on why a mismatch here silently defeats the
    # empty-result short-TTL safety net.
    api_cache_set(db, "house_ptr", cache_key, [asdict(row) for row in rows], normal_ttl_hours=24 * 30)
    return rows
