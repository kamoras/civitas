"""Fetch + parse Senate STOCK Act periodic transaction reports (PTRs).

Source: efdsearch.senate.gov (Senate Electronic Financial Disclosure). There
is no bulk download or documented API — access requires accepting the
statutory-use restriction via a session-gated form before the search
endpoint will respond, then querying a DataTables-backed search endpoint for
report links. Electronic PTRs (the large majority since ~2012) render as an
HTML transactions table and are parsed directly; older paper filings are
PDF/scanned images and reuse the same pdfplumber/OCR path as House.

IMPORTANT — verify against the live site before relying on this in
production: the exact DataTables field names/report-type codes below
(`REPORT_TYPE_PTR`, the `/search/report/data/` POST body) were not
confirmed against a live session as part of this implementation (no
outbound network access was available in the environment this was written
in — see issue #45 investigation notes). Fetch a handful of real filings
through this module in an environment with real network access and diff the
parsed output against the filed reports before trusting it in the nightly
pipeline.

See the Legal note in the issue #45 plan: efdsearch.senate.gov requires
actually presenting/accepting the Ethics in Government Act use restriction
(5 U.S.C. §§13101-13111), not silently skipping past it — accept_terms
below does that as a real POST, not a bypass.
"""

import logging
import re
from dataclasses import asdict

import httpx
from lxml import html as lxml_html
from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.fetch.ptr_common import TradeRow, normalize_date, parse_pdf_bytes, parse_table_rows
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

EFD_BASE = "https://efdsearch.senate.gov"
HOME_URL = f"{EFD_BASE}/search/home/"
SEARCH_URL = f"{EFD_BASE}/search/"
SEARCH_DATA_URL = f"{EFD_BASE}/search/report/data/"

# efdsearch's DataTables endpoint filters by a numeric report_type code.
# Periodic Transaction Report has historically been type 11 in this system
# (per publicly documented senate eFD scrapers) — reconfirm against a live
# session, since a wrong code would silently return zero PTR rows rather
# than erroring.
REPORT_TYPE_PTR = 11

_rate_limiter = RateLimiter(settings.SENATE_PTR_RPS)


async def _request_with_retry(
    client: httpx.AsyncClient, method: str, url: str, **kwargs,
) -> httpx.Response | None:
    return await fetch_with_retry(
        client, _rate_limiter, method, url,
        rate_limit_backoff_multiplier=2.0, retry_on_4xx=False,
        timeout=60.0, log_label="Senate eFD", **kwargs,
    )


def _extract_csrf_token(html_text: str) -> str | None:
    match = re.search(r"name=[\"']csrfmiddlewaretoken[\"']\s+value=[\"']([^\"']+)[\"']", html_text)
    return match.group(1) if match else None


async def accept_terms(client: httpx.AsyncClient) -> str | None:
    """Establish a session and accept the statutory use-restriction gate.

    Returns the CSRF token to use for the subsequent search request, or
    None if the gate couldn't be passed (caller should abort the run
    rather than silently searching without a valid session).
    """
    home_resp = await _request_with_retry(client, "GET", HOME_URL)
    if home_resp is None:
        return None
    token = _extract_csrf_token(home_resp.text)
    if token is None:
        logger.error("Senate eFD home page had no CSRF token — page structure may have changed")
        return None

    accept_resp = await _request_with_retry(
        client, "POST", HOME_URL,
        data={"prohibition_agreement": "1", "csrfmiddlewaretoken": token},
        headers={"Referer": HOME_URL},
        follow_redirects=True,
    )
    if accept_resp is None:
        return None

    search_resp = await _request_with_retry(client, "GET", SEARCH_URL)
    if search_resp is None:
        return None
    return _extract_csrf_token(search_resp.text) or token


async def search_ptr_filings(
    client: httpx.AsyncClient, db: Session, since_date: str, csrf_token: str,
) -> list[dict]:
    """Search for PTR filings submitted on or after since_date (YYYY-MM-DD).

    Returns one dict per filing: {last, first, filed_date, report_url,
    is_paper}. Does not cache across runs (session-bound), unlike the
    House index — a fresh search is cheap and the session itself expires.
    """
    body = {
        "start": "0",
        "length": "100",
        "report_types": f"[{REPORT_TYPE_PTR}]",
        "filer_types": "[]",
        "submitted_start_date": since_date,
        "submitted_end_date": "",
        "candidate_state": "",
        "senator_state": "",
        "office_id": "",
        "first_name": "",
        "last_name": "",
        "csrfmiddlewaretoken": csrf_token,
    }
    resp = await _request_with_retry(
        client, "POST", SEARCH_DATA_URL, data=body, headers={"Referer": SEARCH_URL},
    )
    if resp is None:
        return []

    try:
        payload = resp.json()
    except ValueError:
        logger.error("Senate eFD search response was not JSON — session/endpoint may have changed")
        return []

    filings: list[dict] = []
    for row in payload.get("data", []):
        if len(row) < 5:
            continue
        link_html, last, first, _office, filed_date_raw = row[0], row[1], row[2], row[3], row[4]
        link_match = re.search(r'href="([^"]+)"', link_html or "")
        if not link_match:
            continue
        report_path = link_match.group(1)
        filings.append({
            "last": (last or "").strip(),
            "first": (first or "").strip(),
            "filed_date": normalize_date(filed_date_raw),
            "report_url": f"{EFD_BASE}{report_path}" if report_path.startswith("/") else report_path,
            "is_paper": "/paper/" in report_path,
        })
    return filings


def _html_table_to_rows(table_el) -> list[list[str | None]]:
    rows = []
    for tr in table_el.xpath(".//tr"):
        cells = tr.xpath("./th | ./td")
        rows.append([c.text_content().strip() for c in cells])
    return rows


async def fetch_and_parse_ptr(
    client: httpx.AsyncClient, db: Session, filing: dict,
) -> list[TradeRow]:
    """Fetch one PTR report page and parse its transactions.

    Electronic filings render as an HTML transactions table (parsed
    directly). Paper filings link to a PDF/scanned image and reuse the
    House module's pdfplumber/OCR path. Returns rows tagged with
    parse_confidence ("text" or "ocr"); never fabricates a row it can't
    confidently parse.
    """
    filing_id = filing["report_url"].rstrip("/").rsplit("/", 1)[-1]
    cache_key = f"ptr-parsed-{filing_id}"
    cached = api_cache_get(db, "senate_ptr", cache_key, max_age_hours=24 * 30)
    if cached is not None:
        return [TradeRow(**row) for row in cached]

    resp = await _request_with_retry(client, "GET", filing["report_url"])
    if resp is None:
        return []

    rows: list[TradeRow] = []
    confidence = "text"
    if filing.get("is_paper"):
        pdf_link = re.search(r'href="([^"]+\.pdf)"', resp.text, re.I)
        if pdf_link:
            pdf_resp = await _request_with_retry(client, "GET", f"{EFD_BASE}{pdf_link.group(1)}")
            if pdf_resp is not None:
                try:
                    rows, confidence = parse_pdf_bytes(pdf_resp.content)
                except Exception as e:
                    logger.error("Failed to parse Senate paper PTR %s: %s", filing["report_url"], e)
    else:
        try:
            doc = lxml_html.fromstring(resp.text)
            for table_el in doc.xpath("//table"):
                table_rows = _html_table_to_rows(table_el)
                rows.extend(parse_table_rows(table_rows))
        except Exception as e:
            logger.error("Failed to parse Senate PTR HTML %s: %s", filing["report_url"], e)

    for row in rows:
        row.parse_confidence = confidence
        row.source_url = filing["report_url"]
        row.filing_id = filing_id

    # The API cache stores plain JSON, not dataclasses — convert at this
    # boundary and reconstruct on the cache-hit path above.
    api_cache_set(db, "senate_ptr", cache_key, [asdict(row) for row in rows])
    return rows
