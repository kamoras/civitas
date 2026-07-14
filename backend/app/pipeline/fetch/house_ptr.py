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

import asyncio
import io
import logging
import zipfile

import httpx
from defusedxml import ElementTree
from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.ptr_common import normalize_date, parse_pdf_bytes
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

CLERK_BASE = "https://disclosures-clerk.house.gov/public_disc"
MAX_RETRIES = 3
RETRY_BACKOFF_S = 2.0

_rate_limiter = RateLimiter(settings.HOUSE_PTR_RPS)


async def _fetch_bytes_with_retry(
    client: httpx.AsyncClient, url: str, retries: int = MAX_RETRIES,
) -> bytes | None:
    """Fetch raw bytes (ZIP/PDF) with rate limiting and retries."""
    await _rate_limiter.acquire()
    for attempt in range(1, retries + 1):
        try:
            logger.debug("House Clerk fetch: %s (attempt %d)", url, attempt)
            resp = await client.get(url, timeout=60.0)
            if resp.status_code == 429:
                wait = RETRY_BACKOFF_S * attempt * 2
                logger.warning("House Clerk rate limited, waiting %.1fs...", wait)
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 400:
                if 400 <= resp.status_code < 500:
                    logger.error("House Clerk client error (no retry): %s — HTTP %d", url, resp.status_code)
                    return None
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp,
                )
            return resp.content
        except Exception as e:
            if attempt == retries:
                logger.error("House Clerk fetch failed after %d attempts: %s — %s", retries, url, e)
                return None
            await asyncio.sleep(RETRY_BACKOFF_S * attempt)
    return None


async def fetch_ptr_filing_index(
    client: httpx.AsyncClient, db: Session, year: int,
) -> list[dict]:
    """Fetch and parse the yearly House financial disclosure index.

    Returns one dict per Periodic Transaction Report filing:
    {last, first, state_district, filing_date, doc_id, pdf_url}.
    The index itself never carries transaction-level data — see module
    docstring.
    """
    cache_key = f"ptr-index-{year}"
    cached = api_cache_get(db, "house_ptr", cache_key)
    if cached is not None:
        return cached

    zip_bytes = await _fetch_bytes_with_retry(client, f"{CLERK_BASE}/financial-pdfs/{year}FD.zip")
    if zip_bytes is None:
        return []

    filings: list[dict] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            xml_name = next((n for n in zf.namelist() if n.lower().endswith(".xml")), None)
            if xml_name is None:
                logger.error("House FD %d ZIP contained no XML index", year)
                api_cache_set(db, "house_ptr", cache_key, [])
                return []
            root = ElementTree.fromstring(zf.read(xml_name))
    except (zipfile.BadZipFile, ElementTree.ParseError) as e:
        logger.error("Failed to parse House FD %d index: %s", year, e)
        api_cache_set(db, "house_ptr", cache_key, [])
        return []

    for member in root.findall("Member"):
        filing_type = (member.findtext("FilingType") or "").strip()
        if filing_type != "P":
            continue
        doc_id = (member.findtext("DocID") or "").strip()
        if not doc_id:
            continue
        filing_date = normalize_date(member.findtext("FilingDate") or "")
        filings.append({
            "last": (member.findtext("Last") or "").strip(),
            "first": (member.findtext("First") or "").strip(),
            "state_district": (member.findtext("StateDst") or "").strip(),
            "filing_date": filing_date,
            "doc_id": doc_id,
            "pdf_url": f"{CLERK_BASE}/ptr-pdfs/{year}/{doc_id}.pdf",
        })

    api_cache_set(db, "house_ptr", cache_key, filings)
    return filings


async def fetch_and_parse_ptr(
    client: httpx.AsyncClient, db: Session, filing: dict,
) -> list[dict]:
    """Download and parse one PTR PDF into transaction rows.

    Returns rows tagged with parse_confidence ("text" or "ocr"). Returns an
    empty list if the PDF can't be fetched or no transaction table is
    found — never fabricates a row.
    """
    cache_key = f"ptr-parsed-{filing['doc_id']}"
    cached = api_cache_get(db, "house_ptr", cache_key, max_age_hours=24 * 30)
    if cached is not None:
        return cached

    pdf_bytes = await _fetch_bytes_with_retry(client, filing["pdf_url"])
    if pdf_bytes is None:
        return []

    try:
        rows, confidence = parse_pdf_bytes(pdf_bytes)
    except Exception as e:
        logger.error("Failed to parse PTR PDF %s: %s", filing["pdf_url"], e)
        return []

    for row in rows:
        row["parse_confidence"] = confidence
        row["source_url"] = filing["pdf_url"]
        row["filing_id"] = filing["doc_id"]

    api_cache_set(db, "house_ptr", cache_key, rows)
    return rows
