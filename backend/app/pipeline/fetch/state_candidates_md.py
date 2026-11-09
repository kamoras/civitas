"""Maryland's election-night count, from the State Board of Elections'
results pages (elections.maryland.gov), read 2026-11-09.

The general's results index is `/elections/{year}/general_results/index.html`
(the year is the election's). It links one page per office by the office's
name ("Representative in Congress" -> gen_results_2026_4.html); the reader
takes each link whose text parse_office reads as a federal office, so no
page number is written down.

An office page carries one table per contest under the office's `h2` and,
for a districted office, the district's `h3` ("District 1"):

    Name | Party | Early Voting | Election Day | Mail-In Ballot | Provisional | Total | Percentage

with a "Totals" row and an "Other Write-Ins" row (no candidate; the
Totals row still counts it). A named write-in candidate is printed
"Edward Shlikas (Write-In)". A vote type not counted yet reads "NR" (not
reported): every 2026 contest read "NR" for provisional ballots six days
after the election.

No reporting figure is read. The page's "309 of 309 election day
precincts reported" counts election-day precincts only, while mail-in and
provisional ballots are still being counted. The page's heading says
whether the results are "Unofficial", which is read as the count's
official flag (official only when the heading opens with "Official").
The Board of State Canvassers certifies within 30 days of the election
(certification_deadlines.json), which the deadline rule reads.

Only a general election is read: its index is by year, and a special
election's results live on other pages.
"""

import logging
import re
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from app.pipeline.fetch.election_results import (
    ContestCount,
    FeedUnreadable,
    StateCount,
    is_general_day,
    is_special_contest,
)
from app.pipeline.fetch.http_utils import FETCH_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import clean_display_name, is_not_a_person, normalize_party, parse_office
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)
_HEADING_RE = re.compile(r"^\s*(Official|Unofficial)\b", re.IGNORECASE)


class _Page(HTMLParser):
    """Headings, links and table rows of one results page, in order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, object]] = []  # ("h1"|"h2"|"h3", text) | ("row", [cells]) | ("a", (href, text))
        self._tag: str | None = None
        self._text: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._href: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "a"):
            self._tag, self._text = tag, []
            self._href = dict(attrs).get("href") if tag == "a" else None
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == self._tag:
            text = re.sub(r"\s+", " ", "".join(self._text)).strip()
            self.items.append(("a", (self._href, text)) if tag == "a" else (tag, text))
            self._tag = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.items.append(("row", self._row))
            self._row = None

    def handle_data(self, data):
        if self._tag:
            self._text.append(data)
        if self._cell is not None:
            self._cell.append(data)


def _parse(html: str) -> list[tuple[str, object]]:
    page = _Page()
    page.feed(html)
    return page.items


def _votes(raw: str) -> int | None:
    raw = raw.replace(",", "").strip()
    return int(raw) if raw.isdigit() else None


def office_links(index_html: str, index_url: str) -> list[str]:
    """The index's links to federal offices' pages, by their text."""
    return list(dict.fromkeys(
        urljoin(index_url, href)
        for kind, value in _parse(index_html) if kind == "a"
        for href, text in [value]
        if href and parse_office(text) is not None
    ))


def parse_office_page(html: str, state: str = "MD") -> tuple[list[ContestCount], bool | None]:
    """(contests, official) of one office page; official is None when the
    page has no heading saying either. Raises FeedUnreadable when a table
    lacks the columns named above."""
    contests: list[ContestCount] = []
    official = None
    office = district = ""
    columns: list[str] | None = None
    contest: ContestCount | None = None
    for kind, value in _parse(html):
        if kind == "h1":
            said = _HEADING_RE.match(str(value))
            if said:
                official = said.group(1).lower() == "official"
        elif kind == "h2":
            office, district, contest = str(value), "", None
        elif kind == "h3":
            district, contest = str(value), None
        elif kind == "row":
            cells = list(value)
            if cells and cells[0] == "Name":
                columns = cells
                if "Total" not in columns or "Party" not in columns:
                    raise FeedUnreadable(f"{state} results table has columns {columns!r}")
                parsed = parse_office(f"{office} {district}".strip())
                contest = None
                if parsed is not None:
                    contest = ContestCount(office=parsed[0], district=parsed[1], candidates=[],
                                           is_special=is_special_contest(f"{office} {district}"))
                    contests.append(contest)
                continue
            if contest is None or columns is None or len(cells) != len(columns):
                continue
            name, total = cells[0], _votes(cells[columns.index("Total")])
            if total is None:
                raise FeedUnreadable(f"{state} results row has total {cells[columns.index('Total')]!r}")
            if name == "Totals":
                contest.total_votes = total
            elif not is_not_a_person(name):
                party = normalize_party(cells[columns.index("Party")], ballot_list=True)
                contest.candidates.append((clean_display_name(name), party, total))
    for c in contests:
        if c.total_votes is not None and c.total_votes < sum(v for _, _, v in c.candidates):
            c.total_votes = None
    return contests, official


async def _get(client, url: str, label: str):
    return await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=30.0, log_label=label, headers=FETCH_HEADERS,
        expected_statuses=(404,),
    )


async def fetch_general_results(client: httpx.AsyncClient, election_day: date, state: str, source: dict):
    """Maryland's count for the general on `election_day`, or None when
    the day is no general or the Board has no results index for its year."""
    if not is_general_day(election_day):
        return None
    template = source.get("index_url")
    if not template:
        raise FeedUnreadable(f"{state} md_results source needs index_url")
    index_url = template.format(year=election_day.year)
    resp = await _get(client, index_url, f"{state} results index")
    if resp is None:
        raise FeedUnreadable(f"{state} results index could not be read")
    if resp.status_code == 404:
        return None
    links = office_links(resp.text, index_url)
    if not links:
        return None
    contests, flags = [], []
    for url in links:
        page = await _get(client, url, f"{state} office results")
        if page is None or page.status_code == 404:
            raise FeedUnreadable(f"{state} office results {url} could not be read")
        found, official = parse_office_page(page.text, state)
        contests.extend(found)
        flags.append(official)
    return StateCount(
        source_name=source.get("source_name") or "Maryland State Board of Elections (election results)",
        page_url=index_url, official=bool(flags) and all(f is True for f in flags),
        unit_label="precincts", units_complete=False, contests=contests,
    )
