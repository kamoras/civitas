"""California's certified Statement of Vote for a general election, read
for the certified-results sync (live_results/certified.py).

The Secretary of State publishes the Statement of Vote only once she has
certified the election, on a page of its own under "prior elections"
named by the election's date ("general-election-nov-5-2024/statement-vote",
verified 2026-11-04; the 2026 general's page did not exist the day after
it). The page links one workbook per office, and the Secretary's signed
certificate ("15-sov-certificate.pdf"), whose "this 13 day of December,
2024" is the certification date read here.

Each workbook is one sheet of county rows under each contest's candidate
names (several lines in one cell, "*" marking the incumbent) and their
party codes, closed by a totals row: "District Totals" under each "1st
Congressional District" heading in the House workbook, "State Totals" in
the Senate's. The Senate's full-term and partial-term contests are two
workbooks ("19-uss-ft", "22-uss-pt"); the partial term is the special
election. Write-ins are not in these sheets, so a contest's total is its
listed candidates' sum.
"""

import io
import logging
import re
from datetime import date, datetime
from urllib.parse import urljoin

import httpx
import pdfplumber

from app.pipeline.fetch.http_utils import FETCH_HEADERS
from app.pipeline.fetch.state_candidates_common import normalize_party
from app.pipeline.fetch.state_candidates_tabular import xlsx_sheet_rows
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

PAGE_URL = "https://www.sos.ca.gov/elections/prior-elections/statewide-election-results/{slug}/statement-vote"
# Which workbook holds which contest, by the end of its file name.
_WORKBOOKS = {
    "us-rep-congress.xlsx": ("H", False),
    "uss-ft.xlsx": ("S", False),
    "uss-pt.xlsx": ("S", True),
}
_CERTIFICATE_RE = re.compile(r'href="([^"]*-sov-certificate\.pdf)"')
_CERTIFIED_ON_RE = re.compile(r"this\s+(\d{1,2})(?:st|nd|rd|th)?\s+day\s+of\s+([A-Za-z]+),?\s+(\d{4})")
_DISTRICT_RE = re.compile(r"^(\d+)(?:st|nd|rd|th)\s+Congressional\s+District$")
_TOTALS = ("District Totals", "State Totals")


def page_slug(election_day: date) -> str:
    """"general-election-nov-5-2024": the page's name, from the date."""
    return f"general-election-{election_day.strftime('%b').lower()}-{election_day.day}-{election_day.year}"


def certified_on(certificate_text: str) -> date | None:
    found = _CERTIFIED_ON_RE.search(" ".join(certificate_text.split()))
    if not found:
        return None
    try:
        return datetime.strptime(f"{found.group(1)} {found.group(2)} {found.group(3)}", "%d %B %Y").date()
    except ValueError:
        return None


def workbook_contests(rows: list[list[str]], office: str, special: bool) -> list | None:
    """The contests in one workbook's rows, or None when a block's names,
    parties and totals don't line up."""
    from app.pipeline.fetch.election_results import ContestCount

    contests = []
    district: int | None = None
    names: list[str] | None = None
    parties: list[str] | None = None
    for row in rows:
        head = (row[0] if row else "").strip()
        cells = [c.strip() for c in row[1:]] if row else []
        if found := _DISTRICT_RE.match(head):
            district, names, parties = int(found.group(1)), None, None
            continue
        if not head and any(cells):
            if names is None:
                names = [" ".join(c.replace("*", "").split()) for c in cells if c]
            elif parties is None:
                parties = [c for c in cells if c]
            continue
        if head in _TOTALS and names is not None:
            figures = [c.replace(",", "") for c in cells if c]
            if parties is None or not (len(names) == len(parties) == len(figures)) \
                    or not all(f.isdigit() for f in figures):
                logger.warning("CA Statement of Vote: a %s block's names and totals don't line up", office)
                return None
            if office == "H" and district is None:
                return None
            candidates = [(n, normalize_party(p, ballot_list=True), int(v)) for n, p, v in zip(names, parties, figures)]
            contests.append(ContestCount(office=office, district=district if office == "H" else None,
                                         candidates=candidates, total_votes=sum(v for _, _, v in candidates),
                                         is_special=special))
            names = parties = None
    return contests


async def _get(client: httpx.AsyncClient, url: str):
    await _rate_limiter.acquire()
    return await client.get(url, headers=FETCH_HEADERS, timeout=90.0, follow_redirects=True)


async def fetch_certified_general(client: httpx.AsyncClient, election_day: date, state: str, source: dict):
    """California's certified federal count for `election_day`'s general,
    or None while no Statement of Vote is published for it."""
    from app.pipeline.fetch.election_results import FeedUnreadable, StateCount

    page_url = PAGE_URL.format(slug=page_slug(election_day))
    try:
        page = await _get(client, page_url)
    except httpx.HTTPError as exc:
        raise FeedUnreadable(f"{state} Statement of Vote page could not be read") from exc
    if page.status_code == 404:
        return None
    if page.status_code != 200:
        raise FeedUnreadable(f"{state} Statement of Vote page answered {page.status_code}")
    certificate = _CERTIFICATE_RE.search(page.text)
    if certificate is None:
        return None
    try:
        cert = await _get(client, urljoin(page_url, certificate.group(1)))
        cert.raise_for_status()
        with pdfplumber.open(io.BytesIO(cert.content)) as pdf:
            on = certified_on(" ".join(p.extract_text() or "" for p in pdf.pages))
    except (httpx.HTTPError, ValueError) as exc:
        raise FeedUnreadable(f"{state} Statement of Vote certificate could not be read") from exc
    if on is None:
        raise FeedUnreadable(f"{state} Statement of Vote certificate states no date this reader can read")
    contests = []
    for href in dict.fromkeys(re.findall(r'href="([^"]*\.xlsx)"', page.text)):
        kind = next((v for k, v in _WORKBOOKS.items() if href.endswith(k)), None)
        if kind is None:
            continue
        try:
            book = await _get(client, urljoin(page_url, href))
            book.raise_for_status()
        except httpx.HTTPError as exc:
            raise FeedUnreadable(f"{state} Statement of Vote workbook {href} could not be read") from exc
        rows = xlsx_sheet_rows(book.content)
        found = workbook_contests(rows, *kind) if rows else None
        if found is None:
            raise FeedUnreadable(f"{state} Statement of Vote workbook {href} could not be read whole")
        contests.extend(found)
    if not contests:
        raise FeedUnreadable(f"{state} Statement of Vote held no federal contest this reader could read")
    return StateCount(source_name=source.get("source_name") or "California Secretary of State", page_url=page_url,
                      official=True, unit_label="counties", contests=contests, certified_on=on)
