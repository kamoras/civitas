"""Fetch + parse Senate annual Financial Disclosure reports (asset holdings).

Source: efdsearch.senate.gov, the same system senate_ptr.py reads — the
search runs through the same real-browser flow (search_filings, "Annual"
report type; see senate_ptr.py's module docstring for why a browser is
required) and each report page is fetched on the same accepted-terms httpx
session.

An electronically filed report renders "Part 3. Assets" as an HTML table:
#, Asset, Asset Type, Owner, Value, Income Type, Income. Assets held inside
an account are numbered under it ("3", then "3.1", "3.2"); the account row
itself carries no value of its own ("--") when its underlying assets are
itemized, so only the leaves are holdings.

A paper filing is a set of scanned page images with no text to parse; it
is reported as unparsed and linked, never OCR'd into a guessed table (see
house_fd.py's module docstring for the same reasoning).
"""

import logging
from urllib.parse import urlparse

import httpx
from lxml import html as lxml_html
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.fd_common import (
    UNREADABLE_SCANNED,
    UNREADABLE_UNRECOGNIZED,
    AnnualReport,
    HoldingRow,
    crashed_before,
    report_from_cache,
    report_to_cache,
    parse_holding_value,
    senate_category,
    ticker_for,
)
from app.pipeline.fetch.senate_ptr import (
    ANNUAL_REPORT_TYPE,
    SENATOR_FILER_TYPE,
    _request_with_retry,
    search_filings,
    senate_filing_id,
)

logger = logging.getLogger(__name__)

_CACHE_TIER = "senate_fd"
# Bump whenever parse_assets_table's output changes for the same page: it
# keys the parse cache and is stored on each FinancialDisclosure, so a
# parser fix re-reads reports already ingested instead of leaving them as
# the old parser read them.
PARSER_VERSION = 1
_FILING_MAX_AGE_HOURS = 24 * 30

# The form's Owner values as printed (every value seen on file, 2026-09).
_OWNER_VALUES = {
    "self": "self", "spouse": "spouse", "joint": "joint",
    "child": "dependent", "dependent child": "dependent", "dependent": "dependent",
}

# The report titles a senator's asset list appears under. A candidate report
# or a termination report lists assets too, but only as of entering or
# leaving; "Annual Report for CY 2025" and "New Filer Report for ..." are
# the sitting senator's own current picture.
_ANNUAL_TITLE_MARKERS = ("annual report", "new filer report")


def is_senator_filing(filing: dict) -> bool:
    """True when the search row was filed in the senator's own capacity.

    The office cell reads "Baldwin, Tammy (Senator)" (electronic) or
    "Senator" (paper); candidates read "Candidate (Candidate)". A candidate
    sharing a sitting senator's surname would otherwise be matched to the
    senator by name.
    """
    office = (filing.get("office") or "").lower()
    return "senator" in office and "former" not in office


def is_annual_title(title: str) -> bool:
    return any(marker in (title or "").lower() for marker in _ANNUAL_TITLE_MARKERS)


def is_amendment_title(title: str) -> bool:
    """"Annual Report for CY 2025 (Amendment 1)", "Annual Report (Amendment)"."""
    return "amendment" in (title or "").lower()


def is_new_filer_title(title: str) -> bool:
    return "new filer" in (title or "").lower()


async def search_annual_filings(since_date: str) -> list[dict]:
    """Sitting senators' annual reports filed since since_date. Filtered to
    the Senator filer type on the form itself: unfiltered, the search pages
    through every candidate's report too (several times as many rows, each
    extra page a slow real-browser round trip), only for
    is_senator_filing to discard them."""
    return await search_filings(since_date, ANNUAL_REPORT_TYPE, SENATOR_FILER_TYPE)


def _own_text(cell) -> str:
    """A cell's text without its nested <div> sub-lines (the muted location,
    account-type and comment lines), however the main text itself is
    wrapped — bare, or inside a <span>/<a>."""
    parts = [cell.text or ""]
    for child in cell:
        if child.tag != "div":
            parts.append(child.text_content() or "")
        parts.append(child.tail or "")
    return " ".join(" ".join(parts).split())


def _cell_main_text(cell) -> str:
    """The asset's name — its <strong> when the form uses one, otherwise the
    cell's own text — without the muted sub-lines nested under it."""
    strong = cell.find(".//strong")
    if strong is not None:
        return " ".join((strong.text_content() or "").split())
    return _own_text(cell)


def _redirects_to_terms(resp) -> bool:
    """A redirect whose target is eFD's home/terms page."""
    if not 300 <= resp.status_code < 400:
        return False
    location = (getattr(resp, "headers", None) or {}).get("location", "")
    return urlparse(location).path.rstrip("/") in ("/search/home", "/search")


class SessionLapsed(Exception):
    """eFD served something other than the report — its terms page or a
    redirect to it — meaning the accepted-terms session has lapsed."""


def _as_doc(page):
    """An lxml document from page HTML (or one already parsed — the fetch
    parses each page once and hands the tree to every check below). None
    when there's nothing parseable."""
    if not isinstance(page, str):
        return page
    try:
        return lxml_html.fromstring(page)
    except Exception:
        return None


def is_terms_page(page) -> bool:
    """True for eFD's statutory-use agreement page, which it serves (or
    redirects to) in place of any report once the accepted-terms session
    has lapsed — the same form accept_terms and the search flow submit."""
    doc = _as_doc(page)
    return doc is not None and bool(doc.xpath('//*[@id="agreement_form"] | //*[@id="agree_statement"]'))


def is_report_page(page) -> bool:
    """True when the page is a filed report: every electronic report renders
    its content as numbered "Part N." sections (Part 1 honoraria through
    Part 10 compensation), which the terms-agreement and error pages eFD
    serves in a report's place lack."""
    doc = _as_doc(page)
    return doc is not None and any(
        h.text_content().strip().lower().startswith("part ") for h in doc.xpath("//section//h3")
    )


def parse_assets_table(page) -> list[HoldingRow] | None:
    """Parse Part 3 (Assets) of an electronic annual report page (HTML or
    an already-parsed document).

    Returns None when the page has no Part 3 assets table (not an
    electronic annual report), [] when the section exists and lists none.
    """
    doc = _as_doc(page)
    if doc is None:
        return None
    section = None
    for candidate in doc.xpath("//section"):
        heading = candidate.xpath(".//h3")
        if heading and "assets" in heading[0].text_content().lower() and "part 3" in heading[0].text_content().lower():
            section = candidate
            break
    if section is None:
        return None

    table = section.find(".//table")
    if table is None:
        # The section is present but empty ("None disclosed" / no table).
        return []

    header = [" ".join(th.text_content().split()).lower() for th in table.xpath(".//thead//th")]

    def col(name: str) -> int | None:
        return next((i for i, h in enumerate(header) if h == name), None)

    c_asset, c_type, c_owner, c_value = col("asset"), col("asset type"), col("owner"), col("value")
    if None in (c_asset, c_type, c_owner, c_value):
        logger.warning("Senate annual report assets table has unexpected columns: %s", header)
        return None

    raw: list[tuple[str, HoldingRow]] = []
    for tr in table.xpath(".//tbody/tr"):
        cells = tr.xpath("./td")
        if len(cells) <= max(c_asset, c_type, c_owner, c_value):
            continue
        number = " ".join(cells[0].text_content().split())
        name = _cell_main_text(cells[c_asset])
        if not name:
            logger.warning("Senate assets row %r has no readable asset name — skipped", number)
            continue
        type_cell = cells[c_type]
        asset_type = _own_text(type_cell)
        subtype = " ".join(" ".join(div.text_content() for div in type_cell.xpath("./div")).split())
        owner_text = " ".join(cells[c_owner].text_content().split()).lower()
        owner = _OWNER_VALUES.get(owner_text)
        if owner is None:
            # Never default to the filer: an unrecognized value may well be a
            # spouse's or child's asset. Shown as owner not stated, and
            # logged so the table can be extended from the real value.
            logger.info("Unrecognized Senate asset owner %r", owner_text)
            owner = "unknown"
        value_text = " ".join(cells[c_value].text_content().split())
        low, high = parse_holding_value(value_text)
        raw.append((number, HoldingRow(
            asset_name=name,
            asset_type=f"{asset_type} — {subtype}" if subtype else asset_type,
            category=senate_category(asset_type, subtype),
            owner=owner,
            value_text=value_text,
            value_low=low,
            value_high=high,
            ticker=ticker_for(name),
        )))

    names = {number: row.asset_name for number, row in raw}
    holdings: list[HoldingRow] = []
    for number, row in raw:
        # An account whose underlying assets are itemized ("3" with "3.1",
        # "3.2" ...) is a container: counting its own row as well would
        # double-count whatever value it states.
        if number and any(other.startswith(number + ".") for other, _ in raw):
            continue
        parent = number.rsplit(".", 1)[0] if "." in number else None
        if parent:
            row.account = names.get(parent)
        holdings.append(row)
    return holdings


async def report_still_loads(client: httpx.AsyncClient, report_url: str) -> bool:
    """Whether eFD serves a report page for `report_url` right now — a live
    request, never the parse cache (report pages are session-bound Django
    views, not edge-cached files). The holdings phase asks this of a report
    it already stored when a night's fetches all failed, to tell a source
    that is down from a handful of filings that won't load. Raises
    SessionLapsed, as fetch_and_parse_annual does, so the caller can
    re-accept the terms rather than read a lapse as an outage."""
    resp = await _request_with_retry(client, "GET", report_url, follow_redirects=False)
    if resp is None:
        return False
    doc = _as_doc(resp.text) if resp.status_code == 200 else None
    if _redirects_to_terms(resp) or (doc is not None and is_terms_page(doc)):
        raise SessionLapsed(f"HTTP {resp.status_code} for {report_url}")
    return doc is not None and is_report_page(doc)


async def fetch_and_parse_annual(
    client: httpx.AsyncClient, db: Session, filing: dict,
) -> AnnualReport | None:
    """Fetch one annual report page and parse its assets.

    Raises SessionLapsed when eFD answers with its terms page instead of
    the report. None when the page couldn't be fetched or isn't a report
    (a block or error page in its place). Otherwise an
    AnnualReport whose holdings are None when the report can't be read — a
    paper filing (never fetched: it is scanned page images) or a page
    without a recognizable assets table — and an empty list when it was
    read and lists no assets. `client` must already carry an
    accepted-terms session (senate_ptr.accept_terms).
    """
    if filing.get("is_paper"):
        return AnnualReport(None, None, UNREADABLE_SCANNED, live=False)
    cache_key = f"annual-parsed-v{PARSER_VERSION}-{senate_filing_id(filing['report_url'])}"
    cached = report_from_cache(api_cache_get(db, _CACHE_TIER, cache_key, max_age_hours=_FILING_MAX_AGE_HOURS))
    if cached is not None:
        return cached

    # Redirects are not followed (fetch_with_retry follows them by default):
    # a lapsed session answers with a redirect to the terms page, and a
    # withdrawn report with some other redirect. Followed, both would land on
    # a 200 page that is neither a report nor the terms form, and neither
    # would be recognizable for what it is.
    resp = await _request_with_retry(client, "GET", filing["report_url"], follow_redirects=False)
    if resp is None:
        return None
    doc = _as_doc(resp.text) if resp.status_code == 200 else None
    if _redirects_to_terms(resp) or (doc is not None and is_terms_page(doc)):
        # A lapsed session: eFD answers with a redirect to its terms page
        # or with the terms page itself. That says nothing about the report,
        # so it's never stored as unreadable; raised rather than returned as
        # None so the caller can re-accept the terms only when this actually
        # happens.
        raise SessionLapsed(f"HTTP {resp.status_code} for {filing['report_url']}")
    if resp.status_code != 200:
        # Any other redirect or non-200 success (a moved or withdrawn
        # report): a failed fetch, costing no terms round trip.
        logger.warning("Senate eFD answered HTTP %s for %s", resp.status_code, filing["report_url"])
        return None
    if not is_report_page(doc):
        # A 200 page that is neither the terms page nor a report (no
        # numbered Parts): a block, maintenance or error page served in the
        # report's place — the source failing, as a non-PDF body is at the
        # House, not a report the parser can't read. A changed layout looks
        # the same and is caught the same way: every report fails to load,
        # and the stored-report probe (which asks is_report_page too) fails
        # the phase.
        logger.warning("Senate eFD answered %s with a page that is not a report", filing["report_url"])
        return None
    try:
        holdings = parse_assets_table(doc)
    except Exception:
        logger.exception("Failed to parse Senate annual report %s", filing["report_url"])
        if not crashed_before(db, _CACHE_TIER, cache_key):
            # May be transient: nothing stored, retried next run (see
            # AnnualReport.final).
            return AnnualReport(None, None, UNREADABLE_UNRECOGNIZED, final=False)
        # The same crash two runs running: deterministic — cached below.
        holdings = None
    report = AnnualReport(None, holdings, None if holdings is not None else UNREADABLE_UNRECOGNIZED)

    # Unreadable results are cached too: a filed report never changes, and
    # PARSER_VERSION in the key is what retries them after a parser fix.
    api_cache_set(db, _CACHE_TIER, cache_key, report_to_cache(report), normal_ttl_hours=_FILING_MAX_AGE_HOURS)
    return report
