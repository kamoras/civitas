"""Fetch + parse Senate STOCK Act periodic transaction reports (PTRs).

Source: efdsearch.senate.gov (Senate Electronic Financial Disclosure). There
is no bulk download or documented API — access requires accepting the
statutory-use restriction via a session-gated form before the search
endpoint will respond, then querying a DataTables-backed search endpoint for
report links. Electronic PTRs (the large majority since ~2012) render as an
HTML transactions table and are parsed directly; older paper filings are
PDF/scanned images and reuse the same pdfplumber/OCR path as House.

Verified live against the real site 2026-07-25 (this module's original
comment flagged that it never had been): the DataTables row's column order
was wrong (first/last were swapped and the link-html column was
mis-positioned — see _parse_search_row's own comment), so the original
implementation would have silently produced zero rows even if it could
reach the endpoint at all.

It couldn't: `/search/report/data/`, the actual search endpoint, is behind
Akamai bot-management that a plain HTTP client cannot pass — confirmed live
2026-07-25 that neither httpx nor requests nor curl_cffi's Chrome-TLS-
impersonation get through (403/fake-503 "Site Under Maintenance" page)
regardless of headers, cookies transplanted from a real authenticated
browser session, or both combined. Only a genuine, OS-trusted UI event
(a real click, not a scripted `fetch()` even from within the same page)
gets a 200. search_ptr_filings below drives an actual headless Chromium
tab (Playwright) through the real search form for this reason — every
other request in this module (accept_terms, and fetch_and_parse_ptr's
per-filing page fetch) is NOT behind this gate and stays on the fast
httpx path, confirmed live. Full historical backfill (~2,400 filings,
~24 pages) took ~20s in testing; a normal incremental run (since_date
watermark, usually well under 100 new filings) takes ~9s, almost all of
it one-time browser launch/navigation overhead.

See the Legal note in the issue #45 plan: efdsearch.senate.gov requires
actually presenting/accepting the Ethics in Government Act use restriction
(5 U.S.C. §§13101-13111), not silently skipping past it — accept_terms
below (and search_ptr_filings' own terms step) does that as a real
acceptance, not a bypass.
"""

import asyncio
import logging
import re
import time
from dataclasses import asdict

import httpx
from lxml import html as lxml_html
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright
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

# The DataTable's own page-length options (see search_ptr_filings) — 100 is
# the largest, minimizing the number of "Next" clicks a real backfill needs.
_PAGE_LEN = 100
_MAX_PAGES = 50  # 5,000 filings — far above any real window; loop backstop
# Individual Playwright actions get a short timeout and are treated as
# best-effort (see _click below): a real click's own action reliably
# completes well under this on the live site (confirmed live 2026-07-25,
# ~9s for an entire incremental run including browser launch), and a click
# that never resolves is more likely a page structure change than
# something worth blocking a nightly pipeline run over.
_ACTION_TIMEOUT_MS = 8000


async def _click(locator) -> None:
    """Best-effort click: fires the action and swallows a timeout rather
    than propagating it. A genuine OS-trusted click (what Playwright
    dispatches) reliably completes on this site even when Playwright's own
    post-click settle-state wait times out for unrelated reasons (a
    lingering background request, an analytics beacon) — confirmed live
    2026-07-25 the click itself still lands. Callers verify success by
    checking resulting page state, not by trusting this to raise on
    failure."""
    try:
        await locator.click(timeout=_ACTION_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass


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


def _parse_search_row(row: list) -> dict | None:
    """One DataTables row -> a filing dict, or None if it can't be parsed.

    Column order confirmed live 2026-07-25: [first, last, office/filer
    description (unused), link_html, filed_date] — NOT the [link_html,
    last, first, office, filed_date] this module originally assumed
    (never verified against a live session; would have searched for an
    href inside a plain "Alan"/"Armstrong" string and silently matched
    nothing on every single row).
    """
    if len(row) < 5:
        return None
    first, last, _office, link_html, filed_date_raw = row[0], row[1], row[2], row[3], row[4]
    link_match = re.search(r'href="([^"]+)"', link_html or "")
    if not link_match:
        return None
    report_path = link_match.group(1)
    return {
        "last": (last or "").strip(),
        "first": (first or "").strip(),
        "filed_date": normalize_date(filed_date_raw),
        "report_url": f"{EFD_BASE}{report_path}" if report_path.startswith("/") else report_path,
        "is_paper": "/paper/" in report_path,
    }


def _iso_to_us_date(iso_date: str) -> str:
    """YYYY-MM-DD -> MM/DD/YYYY, the format the search form's date field
    expects. Empty input (no watermark to anchor on) passes through
    unchanged — an empty date field means "no lower bound" to the form."""
    if not iso_date:
        return ""
    year, month, day = iso_date.split("-")
    return f"{month}/{day}/{year}"


async def _wait_until(predicate, timeout_s: float = 10.0, poll_s: float = 0.1) -> bool:
    """Poll `predicate` (a zero-arg callable) until it's truthy or the
    timeout elapses. Used to wait on `responses` list mutations from a
    page.on("response") listener, which has no awaitable event of its own."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(poll_s)
    return predicate()


async def search_ptr_filings(since_date: str) -> list[dict]:
    """Search for PTR filings submitted on or after since_date (YYYY-MM-DD).

    Returns one dict per filing: {last, first, filed_date, report_url,
    is_paper}. Does not cache across runs (session-bound), unlike the
    House index — a fresh search is cheap and the session itself expires.

    Drives a real headless Chromium tab through the actual search form
    (see module docstring for why: the search endpoint is behind Akamai
    bot-management that nothing short of a genuine browser gets past).
    Paginates via the DataTable's own "Next" control at its max page size
    (100) rather than reconstructing the AJAX call directly — an in-page
    `fetch()` to the same endpoint, even from this same authenticated
    page, was ALSO blocked in live testing (2026-07-25): only an actual
    OS-trusted click gets through.

    Browser lifecycle only — the actual scraping steps are in
    _scrape_via_page, split out so that logic is unit-testable against a
    mocked page without needing a real browser.
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                page.set_default_timeout(_ACTION_TIMEOUT_MS)
                return await _scrape_via_page(page, since_date)
            finally:
                await browser.close()
    except Exception:
        logger.exception("Senate eFD Playwright search failed")
        return []


async def _scrape_via_page(page, since_date: str) -> list[dict]:
    """The actual eFD search flow, given an already-launched Playwright
    page. See search_ptr_filings for why this exists as a real browser
    session at all."""
    filings: list[dict] = []
    await page.goto(HOME_URL, wait_until="domcontentloaded")

    # Accept the statutory use-restriction gate if presented (a fresh
    # browser context always starts logged out, so this runs every time —
    # see the Legal note in the module docstring on why this is a real
    # acceptance, not a bypass).
    if await page.locator("#agree_statement").count() > 0:
        await _click(page.locator("#agree_statement"))
        await _click(page.locator("#agreement_form button, #agreement_form input[type=submit]"))

    await page.goto(SEARCH_URL, wait_until="domcontentloaded")
    await _click(page.get_by_role("checkbox", name="Periodic Transactions"))
    us_date = _iso_to_us_date(since_date)
    if us_date:
        date_input = page.locator('input[name="submitted_start_date"]')
        if await date_input.count() > 0:
            await date_input.fill(us_date)

    responses: list = []
    page.on(
        "response",
        lambda r: responses.append(r) if SEARCH_DATA_URL in r.url else None,
    )

    await _click(page.get_by_role("button", name="Search Reports"))
    if not await _wait_until(lambda: bool(responses)):
        logger.error("Senate eFD search produced no response — page structure may have changed")
        return []

    # Max page size, so a real backfill needs the fewest possible "Next"
    # clicks (each one a real, slow-ish network round trip through
    # Akamai's checks).
    length_dropdown = page.get_by_role("combobox", name="Show entries")
    if await length_dropdown.count() > 0:
        before = len(responses)
        try:
            await length_dropdown.select_option(str(_PAGE_LEN), timeout=_ACTION_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            pass
        await _wait_until(lambda: len(responses) > before)

    for _ in range(_MAX_PAGES):
        resp = responses[-1]
        try:
            payload = await resp.json()
        except Exception:
            logger.error("Senate eFD search response was not JSON — session/endpoint may have changed")
            break

        total = payload.get("recordsTotal", 0)
        for row in payload.get("data", []):
            parsed = _parse_search_row(row)
            if parsed is not None:
                filings.append(parsed)

        if len(filings) >= total:
            break

        next_el = page.get_by_text("Next", exact=True)
        if "disabled" in (await next_el.get_attribute("class") or ""):
            break
        before = len(responses)
        await _click(next_el)
        if not await _wait_until(lambda: len(responses) > before):
            break

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
        # The eFD electronic transactions table has no notification-date
        # column, so parse_table_rows falls back to disclosure_date =
        # transaction_date — which scored every electronically filed Senate
        # trade as disclosed in 0 days, making the STOCK Act timeliness
        # metric fiction for the whole chamber. The search result's filed
        # date (the date the report was actually filed with the Secretary
        # of the Senate) is the real disclosure date; use it whenever the
        # parser had no genuine notification signal of its own.
        if filing.get("filed_date") and row.disclosure_date == row.transaction_date:
            row.disclosure_date = filing["filed_date"]

    # The API cache stores plain JSON, not dataclasses — convert at this
    # boundary and reconstruct on the cache-hit path above.
    api_cache_set(db, "senate_ptr", cache_key, [asdict(row) for row in rows])
    return rows
