"""Florida's election-night count, from the Division of Elections' own
results file (Florida Election Watch, floridaelectionwatch.gov).

Florida's primary adapter (the tabular strategy) already reads the same
file for nominees; this is its live reader, asked for every candidate's
votes. Two plain GETs and a third for the reporting status, read live
2026-11-06:

1. The Downloads page (`downloads_url`) links the current election's
   tab-delimited file, named by the election's date
   (`.../enightfilespublic/20261103_ElecResultsFL.txt?v=...`). The file is
   found by that date. The page links only the current election; the
   Division keeps every earlier one at the same name (2020's, 2022's and
   2024's generals and 2026's primary all answered), so for a date the page
   doesn't link, the same name with that date is asked directly, and a 404
   there means the Division holds no file for that day.
2. The file: one row per candidate per county, with the header
   ElectionDate, PartyCode, PartyName, RaceCode, RaceName, CountyCode,
   CountyName, Juris1num, Juris2num, Precincts, PrecinctsReporting,
   CanNameLast, CanNameFirst, CanNameMiddle, CanVotes. Every row's
   ElectionDate ("11/03/2026") must be the day asked for, or nothing is
   stored. The federal contests are labelled the way parse_office reads
   ("United States Senator", "Representative in Congress, District 11");
   a candidate's votes are summed over the counties. Write-in candidates
   (party WRI) are listed under their own names and keep their votes.
3. County Reporting Status (`status_url`): one row per county, with five
   flags the Division sets as each part of the county's count finishes
   (precincts, vote-by-mail, early voting, provisional ballots, and the
   overseas ballots that may arrive up to ten days after the election).
   A county counts as fully reported only when all five are set, so a
   contest's reporting figure is "N of M counties fully reported" over the
   counties its rows name. The page's heading names the election's date;
   a page for another election, or one that can't be read, leaves the
   figure out rather than borrowing it, and the count is kept.

The heading says "Unofficial Election Night Results", and the file carries
no flag for certification, so the count is never official here. Florida's
Elections Canvassing Commission certifies on the 14th day after the
election (certification_deadlines.json), which is what the deadline rule
reads.

A seat whose race Florida doesn't print is not in the file: a candidate
the Division lists as Unopposed is elected when qualifying closes (Maxwell
Frost, FL-10, in 2026), so that district has no contest here.
"""

import csv
import io
import logging
import re
from collections import defaultdict
from datetime import date

import httpx

from app.pipeline.fetch.election_results import (
    ContestCount,
    FeedUnreadable,
    StateCount,
    UntrustedCount,
    is_special_contest,
)
from app.pipeline.fetch.http_utils import FETCH_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import clean_display_name, normalize_party, parse_office
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

_FLAG_RE = re.compile(r"<span\s+class=[\"']?([YN])[\"']?\s*>", re.IGNORECASE)
_COUNTY_RE = re.compile(r"<td\s+class=[\"']?linkCounty[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
# The heading over every page: "2026 General Election<br /> November 3, 2026".
_HEADING_DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),\s+(\d{4})"
)
_MONTHS = {m: i for i, m in enumerate(
    ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
     "November", "December"), start=1)}
# The five completion flags a county row carries (see the module docstring).
_FLAGS_PER_COUNTY = 5
_COLUMNS = ("ElectionDate", "PartyCode", "RaceName", "CountyName", "Juris1num", "CanNameLast",
            "CanNameFirst", "CanNameMiddle", "CanVotes")


def _file_name(day: date) -> str:
    return f"{day.strftime('%Y%m%d')}_ElecResultsFL.txt"


async def _get(client: httpx.AsyncClient, url: str, label: str, *, missing_ok: bool = False):
    return await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=60.0, log_label=label, headers=FETCH_HEADERS,
        expected_statuses=(404,) if missing_ok else (),
    )


def _file_link(page: str, day: date, file_base: str) -> str | None:
    """The file the Downloads page links for `day`, as linked (its `?v=`
    kept: the Division changes it as it republishes)."""
    name = re.escape(_file_name(day))
    found = re.search(rf"href=[\"']({re.escape(file_base)}{name}(?:\?[^\"']*)?)[\"']", page)
    return found.group(1) if found else None


def _row_date(raw: str) -> date | None:
    try:
        month, day, year = (int(p) for p in raw.strip().split("/"))
        return date(year, month, day)
    except (ValueError, TypeError):
        return None


def parse_results(text: str, day: date, state: str):
    """The file's federal contests, each with the counties its rows name.
    Returns ([(ContestCount, counties)], [(unplaced ContestCount,
    counties)]). Raises UntrustedCount when a row is dated another day, and
    FeedUnreadable when the columns aren't the ones described above."""
    reader = csv.DictReader(io.StringIO(text.replace("\r\n", "\n")), delimiter="\t")
    if not reader.fieldnames or any(c not in reader.fieldnames for c in _COLUMNS):
        raise FeedUnreadable(f"{state} results file has columns {reader.fieldnames!r}")
    # contest label -> candidate key -> [name, party, votes]; and its counties.
    votes: dict[str, dict[tuple, list]] = defaultdict(dict)
    counties: dict[str, set[str]] = defaultdict(set)
    districts: dict[str, str] = {}
    for row in reader:
        dated = _row_date(row.get("ElectionDate") or "")
        if dated != day:
            raise UntrustedCount(f"{state} results file has a row dated {row.get('ElectionDate')!r}, not {day}")
        label = (row.get("RaceName") or "").strip()
        name = clean_display_name(" ".join(
            p.strip() for p in (row.get("CanNameFirst"), row.get("CanNameMiddle"), row.get("CanNameLast"))
            if p and p.strip()
        ))
        try:
            count = int((row.get("CanVotes") or "").strip())
        except ValueError as exc:
            raise FeedUnreadable(f"{state} results file has votes {row.get('CanVotes')!r}") from exc
        code = (row.get("PartyCode") or "").strip()
        key = (name, code)
        entry = votes[label].setdefault(key, [name, normalize_party(code, ballot_list=True), 0])
        entry[2] += count
        counties[label].add((row.get("CountyName") or "").strip())
        districts.setdefault(label, (row.get("Juris1num") or "").strip())
    placed, unplaced = [], []
    for label, by_candidate in votes.items():
        # A label that names no federal seat (a state office, or a federal
        # contest under a label parse_office doesn't know) goes to
        # `unplaced` (office ""), for sync.place_by_candidates.
        office, district = parse_office(label) or ("", None)
        if office not in ("S", "H"):
            office, district = "", None
        if office == "H" and district is None and districts[label].isdigit():
            district = int(districts[label])
        contest = ContestCount(
            office=office, district=district,
            candidates=[(n, p, v) for n, p, v in by_candidate.values()],
            is_special=is_special_contest(label),
        )
        (placed if office else unplaced).append((contest, counties[label]))
    return placed, unplaced


def parse_status(page: str, day: date) -> dict[str, bool] | None:
    """{county name: fully reported} from County Reporting Status, or None
    when its heading names another day or its rows aren't the shape above."""
    heading = _HEADING_DATE_RE.search(page)
    if heading is None:
        return None
    month, dom, year = heading.groups()
    if date(int(year), _MONTHS[month], int(dom)) != day:
        return None
    rows = re.split(r"<tr\b", page)
    out: dict[str, bool] = {}
    for row in rows:
        cell = _COUNTY_RE.search(row)
        if cell is None:
            continue
        name = re.sub(r"\s+", " ", _TAG_RE.sub(" ", cell.group(1))).strip()
        flags = _FLAG_RE.findall(row)
        if not flags:
            continue  # the statewide TOTAL row carries no flags
        if not name or len(flags) != _FLAGS_PER_COUNTY:
            return None
        out[name] = all(f.upper() == "Y" for f in flags)
    return out or None


async def fetch_general_results(client: httpx.AsyncClient, election_day: date, state: str, source: dict):
    """Florida's count for `election_day`, or None when the Division holds
    no results file for that day."""
    downloads_url, file_base, status_url = (
        source.get("downloads_url"), source.get("file_base"), source.get("status_url"))
    if not downloads_url or not file_base:
        raise FeedUnreadable(f"{state} fl_enight source needs downloads_url and file_base")
    page = await _get(client, downloads_url, f"{state} results downloads")
    if page is None:
        raise FeedUnreadable(f"{state} results downloads page could not be read")
    url = _file_link(page.text, election_day, file_base) or f"{file_base}{_file_name(election_day)}"
    resp = await _get(client, url, f"{state} results file", missing_ok=True)
    if resp is None:
        raise FeedUnreadable(f"{state} results file could not be read")
    if resp.status_code == 404:
        return None
    placed, unplaced = parse_results(resp.text, election_day, state)
    status = None
    if status_url:
        status_page = await _get(client, status_url, f"{state} county reporting status")
        status = parse_status(status_page.text, election_day) if status_page is not None else None
        if status is None:
            logger.warning("%s county reporting status unread or for another day; count kept without it", state)
    for contest, names in placed + unplaced:
        if status is not None and names <= status.keys():
            contest.reporting_units = sum(1 for n in names if status[n])
            contest.total_units = len(names)
    return StateCount(
        source_name=source.get("source_name") or "Florida Division of Elections (election-night results)",
        page_url=source.get("page_url") or downloads_url,
        official=False, unit_label="counties", units_complete=True,
        contests=[c for c, _ in placed], unplaced=[c for c, _ in unplaced],
    )
