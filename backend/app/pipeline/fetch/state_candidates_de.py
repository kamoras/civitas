"""Delaware's election-night count, from the Department of Elections'
results page (elections.delaware.gov), read 2026-11-10.

The results index (`page_url`) links each election's page by its id
("/results/enr/GE2026.html", found by `link_regex` with the year filled
in). That page is a shell its script fills from JSON files named by the
same id; this reads the statewide one,

    {base_url}Election_StatewideResults_ID_GE2026.json

one row per candidate per contest, with the contest's election districts
(Delaware's precincts) reported and in total:

    {"Contest Title": "Representative In Congress",
     "Candidate Name": "SARAH MCBRIDE", "Party Name": "Democratic Party",
     "Total Votes": "243,467", "Precincts Reported": 530,
     "Total Precincts": 530, "Election Date": "2026-11-03",
     "Results Type": "UNOFFICIAL RESULTS",
     "ReportTime": "2026-11-04T03:03:18.364-05:00", ...}

Every row's Election Date must be the day asked for, else the file holds
another election (UntrustedCount). The Results Type is read as the
official flag ("OFFICIAL RESULTS"); it still read UNOFFICIAL on
2026-11-10, a week after the Superior Court's county canvass, so the
deadline rule (certification_deadlines.json: the 2nd day after the
election) is what dates a certified count. ReportTime is when the count
last moved. The reporting figure is election districts reported, worded as
precincts with results: the page's own note says the absentee tabulation
statuses "do not mean all ballots are counted".

Earlier generals' files are not kept (GE2024's answered 404 on
2026-11-10), so the readiness self-check replays the bundled recording
(data/live_results_recordings/DE.json).
"""

import logging
import re
from datetime import date, datetime, timezone

import httpx

from app.pipeline.fetch.election_results import (
    ContestCount,
    FeedUnreadable,
    StateCount,
    UntrustedCount,
    is_general_day,
    is_special_contest,
)
from app.pipeline.fetch.http_utils import FETCH_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import clean_display_name, is_not_a_person, normalize_party, parse_office
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)


def _int(raw) -> int | None:
    raw = str(raw if raw is not None else "").replace(",", "").strip()
    return int(raw) if raw.isdigit() else None


def parse_statewide(rows: list, election_day: date, state: str = "DE") -> tuple[list[ContestCount], bool]:
    """(the federal contests, whether every row says official) of one
    statewide results file."""
    contests: dict[str, ContestCount] = {}
    official = bool(rows)
    for row in rows:
        if not isinstance(row, dict):
            raise FeedUnreadable(f"{state} results file has a row that isn't an object")
        if str(row.get("Election Date") or "") != election_day.isoformat():
            raise UntrustedCount(f"{state} results file is dated {row.get('Election Date')!r}, not {election_day}")
        official = official and str(row.get("Results Type") or "").strip().upper() == "OFFICIAL RESULTS"
        label = str(row.get("Contest Title") or "")
        parsed = parse_office(label)
        if parsed is None:
            continue
        votes = _int(row.get("Total Votes"))
        if votes is None:
            raise FeedUnreadable(f"{state} results row has votes {row.get('Total Votes')!r}")
        contest = contests.get(label)
        if contest is None:
            contest = contests[label] = ContestCount(
                office=parsed[0], district=parsed[1], candidates=[], total_votes=0,
                reporting_units=_int(row.get("Precincts Reported")),
                total_units=_int(row.get("Total Precincts")) or None,
                is_special=is_special_contest(label),
            )
        contest.total_votes += votes
        name = clean_display_name(str(row.get("Candidate Name") or ""))
        if name and not is_not_a_person(name):
            contest.candidates.append((name, normalize_party(str(row.get("Party Name") or ""), ballot_list=True), votes))
    return list(contests.values()), official


def _report_time(rows: list) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(rows[0].get("ReportTime")))
    except (IndexError, AttributeError, TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        return None
    return stamp.astimezone(timezone.utc).replace(tzinfo=None)


async def _get(client, url: str, label: str):
    return await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=60.0, log_label=label, headers=FETCH_HEADERS,
        expected_statuses=(404,),
    )


async def fetch_general_results(client: httpx.AsyncClient, election_day: date, state: str, source: dict):
    """Delaware's count for the general on `election_day`, or None when
    the day is no general or the results index links no page for its year."""
    if not is_general_day(election_day):
        return None
    page_url, pattern, base = source.get("page_url"), source.get("link_regex"), source.get("base_url")
    if not page_url or not pattern or not base:
        raise FeedUnreadable(f"{state} de_enr source needs page_url, link_regex and base_url")
    page = await _get(client, page_url, f"{state} results index")
    if page is None or page.status_code != 200:
        raise FeedUnreadable(f"{state} results index could not be read")
    found = re.search(pattern.replace("{year}", str(election_day.year)), page.text)
    if not found:
        return None
    url = f"{base.rstrip('/')}/Election_StatewideResults_ID_{found.group(1)}.json"
    resp = await _get(client, url, f"{state} statewide results")
    if resp is None or resp.status_code != 200:
        raise FeedUnreadable(f"{state} statewide results could not be read")
    try:
        rows = resp.json()
    except ValueError as exc:
        raise FeedUnreadable(f"{state} statewide results are not JSON") from exc
    if not isinstance(rows, list):
        raise FeedUnreadable(f"{state} statewide results are not a list")
    contests, official = parse_statewide(rows, election_day, state)
    return StateCount(
        source_name=source.get("source_name") or "Delaware Department of Elections (election results)",
        page_url=page_url, official=official, unit_label="precincts", units_complete=False,
        contests=contests, source_updated=_report_time(rows),
    )
