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
from dataclasses import asdict

import httpx
from lxml import html as lxml_html
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.fd_common import HoldingRow, parse_holding_value, senate_category, ticker_for
from app.pipeline.fetch.senate_ptr import ANNUAL_REPORT_TYPE, _request_with_retry, search_filings

logger = logging.getLogger(__name__)

_CACHE_TIER = "senate_fd"
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


async def search_annual_filings(since_date: str) -> list[dict]:
    return await search_filings(since_date, ANNUAL_REPORT_TYPE)


def _cell_main_text(cell) -> str:
    """The asset's name — its <strong> — without the muted sub-lines
    (location, account type, filer comment) the cell nests under it."""
    strong = cell.find(".//strong")
    if strong is not None:
        return " ".join((strong.text_content() or "").split())
    return " ".join((cell.text or "").split())


def parse_assets_table(page_html: str) -> list[HoldingRow] | None:
    """Parse Part 3 (Assets) of an electronic annual report page.

    Returns None when the page has no Part 3 assets table (not an
    electronic annual report), [] when the section exists and lists none.
    """
    doc = lxml_html.fromstring(page_html)
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
            continue
        type_cell = cells[c_type]
        asset_type = " ".join((type_cell.text or "").split())
        subtype = " ".join(" ".join(div.text_content() for div in type_cell.xpath("./div")).split())
        owner = _OWNER_VALUES.get(" ".join(cells[c_owner].text_content().split()).lower(), "self")
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


async def fetch_and_parse_annual(
    client: httpx.AsyncClient, db: Session, filing: dict,
) -> list[HoldingRow] | None:
    """Fetch one annual report page and parse its assets.

    None when the report couldn't be read (fetch failure, paper filing,
    unexpected layout); an empty list when it was read and lists no assets.
    `client` must already carry an accepted-terms session
    (senate_ptr.accept_terms).
    """
    if filing.get("is_paper"):
        return None
    filing_id = filing["report_url"].rstrip("/").rsplit("/", 1)[-1]
    cache_key = f"annual-parsed-{filing_id}"
    cached = api_cache_get(db, _CACHE_TIER, cache_key, max_age_hours=_FILING_MAX_AGE_HOURS)
    if cached is not None and cached.get("holdings") is not None:
        return [HoldingRow(**row) for row in cached["holdings"]]

    resp = await _request_with_retry(client, "GET", filing["report_url"])
    if resp is None:
        return None
    try:
        holdings = parse_assets_table(resp.text)
    except Exception as e:
        logger.error("Failed to parse Senate annual report %s: %s", filing["report_url"], e)
        return None
    if holdings is None:
        return None

    api_cache_set(
        db, _CACHE_TIER, cache_key, {"holdings": [asdict(h) for h in holdings]},
        normal_ttl_hours=_FILING_MAX_AGE_HOURS,
    )
    return holdings
