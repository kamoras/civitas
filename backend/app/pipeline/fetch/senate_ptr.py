"""Fetch + parse Senate STOCK Act periodic transaction reports (PTRs).

Source: efdsearch.senate.gov (Senate Electronic Financial Disclosure). There
is no bulk download or documented API — access requires accepting the
statutory-use restriction via a session-gated form before the search
endpoint will respond, then querying a DataTables-backed search endpoint for
report links. Electronic PTRs (the large majority since ~2012) render as an
HTML transactions table and are parsed directly; older paper filings are
PDF/scanned images and reuse the same pdfplumber/OCR path as House.

The DataTables search row's column order matters (see _parse_search_row's
own comment for the actual order) — getting it wrong silently produces
zero rows rather than an error, since a swapped/mis-positioned column
just means every row fails to parse cleanly.

`/search/report/data/`, the actual search endpoint, is behind Akamai
bot-management that a plain HTTP client cannot pass regardless of
headers, cookies transplanted from a real authenticated browser session,
or both combined — only a genuine, OS-trusted UI event (a real click, not
a scripted `fetch()` even from within the same page) gets a 200.
search_ptr_filings below drives an actual headless Chromium tab
(Playwright) through the real search form for this reason — every other
request in this module (accept_terms, and fetch_and_parse_ptr's
per-filing page fetch) is NOT behind this gate and stays on the fast
httpx path. (Since 2026-10-23 Akamai refuses the terms agreement itself,
to httpx and the browser alike: see EFD_WALL_NOTE.) A full historical backfill (~2,400 filings, ~24 pages) runs in
roughly 20s; a normal incremental run (since_date watermark, usually well
under 100 new filings) is closer to 9s, almost all of it one-time browser
launch/navigation overhead.

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
import weakref
from datetime import datetime, timezone
from dataclasses import asdict, dataclass, field

import httpx
from lxml import html as lxml_html
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright
from sqlalchemy.orm import Session

from app import crawl_policy
from app.config import settings
from app.contact import USER_AGENT
from app.error_utils import ExpectedSourceFailure
from app.http_client import is_bot_challenge
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.fetch.ptr_common import TradeRow, normalize_date, parse_pdf_bytes, parse_table_rows
from app.pipeline.fetch.ptr_common import PARSER_VERSION as PTR_PARSER_VERSION
from app.pipeline.rate_limiter import RateLimiter
from app.web_bot_auth import sign_browser_page

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
# completes well under this on the live site, and a click that never
# resolves is more likely a page structure change than something worth
# blocking a nightly pipeline run over.
_ACTION_TIMEOUT_MS = 8000

# Playwright's full Chromium, not its default headless shell: only the full
# browser reads the image's managed policy that distrusts compromised roots
# (backend/Dockerfile, AGENTS.md "Distrusted roots"). Checked live against
# efdsearch.senate.gov 2026-10-22: the search returns the same filings.
CHROMIUM_CHANNEL = "chromium"


async def _click(locator) -> None:
    """Best-effort click: fires the action and swallows a timeout rather
    than propagating it. A genuine OS-trusted click (what Playwright
    dispatches) reliably lands on this site even when Playwright's own
    post-click settle-state wait times out for unrelated reasons (a
    lingering background request, an analytics beacon). Callers verify
    success by checking resulting page state, not by trusting this to
    raise on failure."""
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


class EfdRefused(ExpectedSourceFailure, RuntimeError):
    """eFD's edge refused this client outright (a bot wall's answer, not an
    outage): nothing is read past it (AGENTS.md §7). Raised rather than
    returned as None, so the failed step says which it is."""

    failure_kind = "walled"
    source_host = "efdsearch.senate.gov"


# Since 2026-10-23 Akamai's Bot Manager answers the terms agreement's POST
# with its own 403 "Access Denied" (Server: AkamaiGHost, a bm_s cookie) to
# httpx, curl and Playwright's Chromium (full browser and headless shell
# alike), while the home page still loads; the search and every report page
# sit behind that agreement, and eFD is the only public source of senators'
# reports. Nothing here passes the wall: each run asks once, and reads again
# the first night the agreement is accepted.
EFD_WALL_NOTE = (
    "Senate eFD's Akamai edge refused the statutory terms agreement (HTTP 403 Access Denied: "
    "a bot wall, not an outage), and nothing is read past it. Stored Senate trades, holdings "
    "and scanned pages stay as last read; the next stock-trades run asks again"
)


EFD_HOST = "efdsearch.senate.gov"

# One accepted session per client, shared by every step that reads eFD on
# it (the stock run's trades, holdings and scanned pages use one client):
# until 2026-10-25 each step accepted the terms again, four times in thirty
# seconds on a night the agreement was refused.
_sessions: "weakref.WeakKeyDictionary[httpx.AsyncClient, str]" = weakref.WeakKeyDictionary()


def raise_if_refusing() -> None:
    """EfdRefused, sending nothing, while eFD's refusal backoff holds
    (app/crawl_policy.py): the first step of a night asks once, and every
    later step, job or run waits out the backoff instead of asking again."""
    entry = crawl_policy.refusing(EFD_HOST)
    if entry:
        until = datetime.fromtimestamp(entry["until"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        raise EfdRefused(
            f"{EFD_WALL_NOTE} (refused {entry.get('count', 1)} time(s) in a row since {entry.get('since')}; "
            f"not asked again before {until})."
        )


def _raise_if_walled(response: httpx.Response) -> None:
    if is_bot_challenge(response):
        reference = response.headers.get("x-reference-error")
        raise EfdRefused(EFD_WALL_NOTE + (f" (Akamai reference {reference})" if reference else "") + ".")


async def accept_terms(client: httpx.AsyncClient, *, renew: bool = False) -> str | None:
    """Establish a session and accept the statutory use-restriction gate,
    once per client: a later call returns the same session's token unless
    `renew` (the session lapsed).

    Returns the CSRF token to use for the subsequent search request, or
    None if the gate couldn't be passed (caller should abort the run
    rather than silently searching without a valid session). Raises
    EfdRefused when a bot wall answers instead, or while eFD's refusal
    backoff holds (raise_if_refusing), without a request.
    """
    raise_if_refusing()
    if not renew and client in _sessions:
        return _sessions[client]
    token = await _accept(client)
    if token is not None:
        _sessions[client] = token
    return token


async def _accept(client: httpx.AsyncClient) -> str | None:
    home_resp = await _request_with_retry(client, "GET", HOME_URL, bot_challenge_ok=True)
    if home_resp is None:
        return None
    _raise_if_walled(home_resp)
    token = _extract_csrf_token(home_resp.text)
    if token is None:
        logger.error("Senate eFD home page had no CSRF token — page structure may have changed")
        return None

    accept_resp = await _request_with_retry(
        client, "POST", HOME_URL,
        data={"prohibition_agreement": "1", "csrfmiddlewaretoken": token},
        headers={"Referer": HOME_URL},
        follow_redirects=True,
        bot_challenge_ok=True,
    )
    if accept_resp is None:
        return None
    _raise_if_walled(accept_resp)

    search_resp = await _request_with_retry(client, "GET", SEARCH_URL)
    if search_resp is None:
        return None
    return _extract_csrf_token(search_resp.text) or token


def _parse_search_row(row: list) -> dict | None:
    """One DataTables row -> a filing dict, or None if it can't be parsed.

    Column order: [first, last, office/filer description, link_html,
    filed_date]. Getting this wrong (e.g. assuming link_html comes first)
    means searching for an href inside a plain name string and silently
    matching nothing on every row.

    The office cell ("Doe, Jane (Senator)", "Candidate (Candidate)")
    is kept because an annual-report search returns candidates' reports
    too, and a candidate who shares a sitting senator's surname must not be
    matched to that senator (see senate_fd.is_senator_filing).
    """
    if len(row) < 5:
        return None
    first, last, office, link_html, filed_date_raw = row[0], row[1], row[2], row[3], row[4]
    link_match = re.search(r'href="([^"]+)"', link_html or "")
    if not link_match:
        return None
    report_path = link_match.group(1)
    title = " ".join(re.sub(r"<[^>]+>", " ", link_html or "").split())
    return {
        "last": (last or "").strip(),
        "first": (first or "").strip(),
        "office": (office or "").strip(),
        "title": title,
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

    See search_filings — this is its "Periodic Transactions" report type.
    """
    return await search_filings(since_date, PTR_REPORT_TYPE)


# The search form's report-type checkbox labels (the site's own wording).
PTR_REPORT_TYPE = "Periodic Transactions"
ANNUAL_REPORT_TYPE = "Annual"
SENATOR_FILER_TYPE = "Senator"


class SearchUnavailable(Exception):
    """The eFD search could not be read: the browser failed, the form
    produced no response, or the response was not the search's JSON. Never
    an empty result, which a caller would read as "nothing filed"."""


# A failed search is tried once more in a fresh browser before it is
# reported: one nightly search failed outright (2026-10-22, the annual
# search, minutes after the PTR search succeeded in its own browser) and the
# same search, rerun that evening, returned all 238 rows.
_SEARCH_ATTEMPTS = 2


async def search_filings(since_date: str, report_type: str, filer_type: str | None = None) -> list[dict]:
    """Search for filings of `report_type` (a checkbox label on the search
    form) submitted on or after since_date (YYYY-MM-DD).

    Returns one dict per filing: {last, first, office, filed_date,
    report_url, is_paper}, possibly none; raises SearchUnavailable when the
    search could not be read. Does not cache across runs (session-bound), unlike the
    House index — a fresh search is cheap and the session itself expires.

    Drives a real headless Chromium tab through the actual search form
    (see module docstring for why: the search endpoint is behind Akamai
    bot-management that nothing short of a genuine browser gets past).
    Paginates via the DataTable's own "Next" control at its max page size
    (100) rather than reconstructing the AJAX call directly — an in-page
    `fetch()` to the same endpoint, even from this same authenticated
    page, is blocked the same way a plain HTTP client is: only an actual
    OS-trusted click gets through.

    Browser lifecycle only — the actual scraping steps are in
    _scrape_via_page, split out so that logic is unit-testable against a
    mocked page without needing a real browser.
    """
    raise_if_refusing()  # no browser is started for a host that is refusing
    reason = ""
    for attempt in range(1, _SEARCH_ATTEMPTS + 1):
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, channel=CHROMIUM_CHANNEL)
                try:
                    page = await browser.new_page(user_agent=USER_AGENT)  # Civitas, not HeadlessChrome
                    await sign_browser_page(page)  # identified as Civitas, verifiably (web_bot_auth)
                    page.set_default_timeout(_ACTION_TIMEOUT_MS)
                    return await _scrape_via_page(page, since_date, report_type, filer_type)
                finally:
                    await browser.close()
        except EfdRefused:
            raise  # a wall answers a fresh browser the same way
        except SearchUnavailable as e:
            reason = str(e)
        except Exception as e:
            logger.exception("Senate eFD Playwright search failed")
            reason = f"the browser search raised {type(e).__name__}: {e}"
        logger.warning("Senate eFD %s search, attempt %d of %d: %s", report_type, attempt, _SEARCH_ATTEMPTS, reason)
    raise SearchUnavailable(f"Senate eFD {report_type} search failed {_SEARCH_ATTEMPTS} times; last: {reason}")


async def _submit_search(
    page, since_date: str, report_type: str, filer_type: str | None, last_name: str = "",
) -> list:
    """Fill and submit the search form at its largest page size; returns
    the list the search responses arrive in (the newest last)."""
    await page.goto(SEARCH_URL, wait_until="domcontentloaded")
    await _click(page.get_by_role("checkbox", name=report_type))
    if filer_type:
        # Exact: "Senator" is also a substring of "Former Senator".
        await _click(page.get_by_role("checkbox", name=filer_type, exact=True))
    if last_name:
        await page.locator('input[name="last_name"]').fill(last_name)
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
        raise SearchUnavailable("the search form produced no response (the page may have changed)")

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
    return responses


async def _scrape_via_page(
    page, since_date: str, report_type: str = PTR_REPORT_TYPE, filer_type: str | None = None,
) -> list[dict]:
    """The actual eFD search flow, given an already-launched Playwright
    page. See search_ptr_filings for why this exists as a real browser
    session at all."""
    await page.goto(HOME_URL, wait_until="domcontentloaded")

    # Accept the statutory use-restriction gate if presented (a fresh
    # browser context always starts logged out, so this runs every time —
    # see the Legal note in the module docstring on why this is a real
    # acceptance, not a bypass).
    if await page.locator("#agree_statement").count() > 0:
        await _click(page.locator("#agree_statement"))
        await _click(page.locator("#agreement_form button, #agreement_form input[type=submit]"))
        if await page.title() == "Access Denied":  # Akamai's own refusal page
            crawl_policy.record_refusal(EFD_HOST, "Akamai's Access Denied to the terms agreement (browser)")
            raise EfdRefused(EFD_WALL_NOTE + ".")

    responses = await _submit_search(page, since_date, report_type, filer_type)
    found = _Found()
    await _page_through(page, responses, found)
    if found.total and found.received >= found.total and found.distinct < found.total:
        # Every row arrived, but some twice. The search orders by last
        # name, then first, and nothing after: a filer's own reports are
        # tied, and their order can change between page requests, so a
        # filer whose reports straddle a page boundary can have one shown
        # on both pages and another on neither (2026-10-22: one of 238
        # annual reports). The missing one shares the repeated one's name,
        # so that filer is searched again on their own, which fits one page.
        for last in sorted({found.by_id[fid]["last"] for fid in found.repeated}):
            await _page_through(page, await _submit_search(page, since_date, report_type, filer_type, last), found,
                                count=False)
    # Reported, not retried in the same browser session: every caller
    # searches again on its next nightly run, and holdings never lets a
    # partial search replace a newer stored report (_is_older).
    if found.total and found.received < found.total:
        logger.warning("Senate eFD search returned %d of %d rows — a results page didn't load",
                       found.received, found.total)
    elif found.total and found.distinct < found.total:
        # The filer's own search didn't find it either: the same filing
        # listed twice, which the search can't tell from one never shown.
        logger.warning(
            "Senate eFD search showed %d distinct rows of %d — a filing listed twice, "
            "or one displaced by a shifting order", found.distinct, found.total,
        )
    return list(found.by_id.values())


@dataclass
class _Found:
    """What a search's pages held: the filings by id, rows that didn't
    parse (by content), ids seen more than once, how many rows the main
    search's pages held in all, and its recordsTotal."""

    by_id: dict = field(default_factory=dict)
    unparsed: set = field(default_factory=set)
    repeated: set = field(default_factory=set)
    received: int = 0
    total: int = 0

    @property
    def distinct(self) -> int:
        return len(self.by_id) + len(self.unparsed)


async def _page_through(page, responses: list, found: _Found, count: bool = True) -> None:
    """Collect every results page from the latest response on into
    `found`. `count`: this is the main search, whose recordsTotal and row
    count `found` reports (a follow-up search only adds filings).

    Paging stops early only once every row is accounted for by distinct
    content: a row can reappear on a later page when the ordering shifts,
    and counted twice it would stop paging before the page that holds the
    row it displaced. Distinct content can undercount (two identical rows),
    which only means paging on to the last page, where Next is disabled."""
    received = pages = 0
    for _ in range(_MAX_PAGES):
        try:
            payload = await responses[-1].json()
        except Exception as e:
            if not pages:
                raise SearchUnavailable(f"the search response was not JSON ({type(e).__name__})") from e
            logger.error("Senate eFD search response was not JSON — session/endpoint may have changed")
            break
        pages += 1
        total = payload.get("recordsTotal", 0)
        rows = payload.get("data", [])
        received += len(rows)
        _collect_rows(found, rows)
        if count:
            found.received, found.total = received, total
        # The main search is done once its distinct rows reach its total; a
        # follow-up once it has received its own total.
        if (found.distinct if count else received) >= total:
            break

        next_el = page.get_by_text("Next", exact=True)
        if "disabled" in (await next_el.get_attribute("class") or ""):
            break
        before = len(responses)
        await _click(next_el)
        if not await _wait_until(lambda: len(responses) > before):
            break


def _collect_rows(found: _Found, rows: list) -> None:
    """Add a results page's rows to `found.by_id`, keyed by filing, and the
    rows that don't parse to `found.unparsed`, keyed by their content. The
    first sighting of a filing is kept; a filing seen again is noted in
    `found.repeated`."""
    for row in rows:
        parsed = _parse_search_row(row)
        if parsed is None:
            found.unparsed.add(repr(row))
            continue
        fid = senate_filing_id(parsed["report_url"])
        if fid in found.by_id:
            found.repeated.add(fid)
        else:
            found.by_id[fid] = parsed


_REPORT_VERSION_RE = re.compile(r"\bfor (\d{1,2}/\d{1,2}/\d{4})(?:\s*\(Amendment (\d+)\))?", re.IGNORECASE)


def report_version(title: str) -> tuple[str | None, int]:
    """(the date of the report this filing is a version of, its amendment
    number; 0 for the original) from an eFD title: "Periodic Transaction
    Report for 11/15/2024 (Amendment 2)" amends the report filed 11/15/2024.
    An amendment is the whole report again, corrected; its own filed date
    is not when its trades were first disclosed."""
    m = _REPORT_VERSION_RE.search(title or "")
    if not m:
        return None, 0
    return normalize_date(m.group(1)), int(m.group(2) or 0)


def senate_filing_id(report_url: str) -> str:
    """A report's stable id: the UUID that ends its eFD URL
    (".../view/annual/<uuid>/"). The one derivation every Senate ingest
    dedupes on — a second spelling of it would stop stored filings from
    matching and re-fetch every one of them each run."""
    return report_url.rstrip("/").rsplit("/", 1)[-1]


_FILED_RE = re.compile(r"\bFiled\s+(\d{1,2}/\d{1,2}/\d{4})")


def _page_filed_date(doc) -> str | None:
    """The filing date a parsed report page states in its header, as
    YYYY-MM-DD — the same date the search lists (checked 2026-09-27 on three
    live filings)."""
    # Text nodes joined with spaces: adjacent elements' text would otherwise
    # run together ("ReportFiled").
    match = _FILED_RE.search(" ".join(" ".join(doc.itertext()).split()))
    return normalize_date(match.group(1)) if match else None


def _html_table_to_rows(table_el) -> list[list[str | None]]:
    rows = []
    for tr in table_el.xpath(".//tr"):
        cells = tr.xpath("./th | ./td")
        rows.append([c.text_content().strip() for c in cells])
    return rows


async def fetch_and_parse_ptr(
    client: httpx.AsyncClient, db: Session, filing: dict,
) -> list[TradeRow] | None:
    """Fetch one PTR report page and parse its transactions.

    Electronic filings render as an HTML transactions table (parsed
    directly). Paper filings link to a PDF/scanned image and reuse the
    House module's pdfplumber/OCR path. Returns rows tagged with
    parse_confidence ("text" or "ocr"), an empty list when the report was
    read and holds none, or None when it couldn't be fetched or parsed;
    never fabricates a row it can't confidently parse.
    """
    filing_id = senate_filing_id(filing["report_url"])
    cache_key = f"ptr-parsed-v{PTR_PARSER_VERSION}-{filing_id}"
    cached = api_cache_get(db, "senate_ptr", cache_key, max_age_hours=24 * 30)
    if cached is not None:
        return [TradeRow(**row) for row in cached]

    resp = await _request_with_retry(client, "GET", filing["report_url"])
    if resp is None:
        return None
    try:
        doc = lxml_html.fromstring(resp.text)
    except Exception as e:
        logger.error("Failed to parse Senate PTR HTML %s: %s", filing["report_url"], e)
        return None

    # The search result's filed date; for a re-read of a stored filing,
    # which has no search result, the one the page states in its header
    # ("Filed 09/21/2026 @ 2:52 PM"), or failing that the one its stored
    # rows already carried.
    filed_date = filing.get("filed_date") or _page_filed_date(doc) or filing.get("stored_filed_date")
    rows: list[TradeRow] = []
    confidence = "text"
    if filing.get("is_paper"):
        pdf_link = re.search(r'href="([^"]+\.pdf)"', resp.text, re.I)
        if pdf_link:
            pdf_resp = await _request_with_retry(client, "GET", f"{EFD_BASE}{pdf_link.group(1)}")
            if pdf_resp is None:
                return None
            try:
                # A blank owner is not stated on the Senate's forms, as on
                # its electronic tables. A scan's dates are read only up to
                # the filing's date, as house_ptr's are, and a row whose date
                # alone isn't legible is kept undated
                # (ptr_common.ocr_extract_rows); its disclosure date is the
                # filed date, below.
                rows, confidence = parse_pdf_bytes(
                    pdf_resp.content, blank_owner="unknown", not_after=filed_date, keep_undated=True,
                )
            except Exception as e:
                logger.error("Failed to parse Senate paper PTR %s: %s", filing["report_url"], e)
                return None
    else:
        try:
            for table_el in doc.xpath("//table"):
                # eFD prints every owner as a word, "Self" included.
                rows.extend(parse_table_rows(_html_table_to_rows(table_el), blank_owner="unknown"))
        except Exception as e:
            logger.error("Failed to parse Senate PTR HTML %s: %s", filing["report_url"], e)
            return None

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
        if filed_date and (not row.disclosure_date or row.disclosure_date == row.transaction_date):
            row.disclosure_date = filed_date

    # The API cache stores plain JSON, not dataclasses — convert at this
    # boundary and reconstruct on the cache-hit path above. normal_ttl_hours
    # must match the read's own max_age_hours above (30 days) — see
    # api_cache_set's docstring on why a mismatch here silently defeats the
    # empty-result short-TTL safety net.
    api_cache_set(db, "senate_ptr", cache_key, [asdict(row) for row in rows], normal_ttl_hours=24 * 30)
    return rows
