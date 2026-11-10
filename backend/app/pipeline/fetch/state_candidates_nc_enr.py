"""North Carolina's election-night count, from the State Board of
Elections' results app (er.ncsbe.gov), read 2026-11-10.

The app's own page loads three files, which this reads the same way:

  {base_url}elections.txt
      every election it holds, by date ("edt": "11/03/2026")
  {base_url}{yyyymmdd}/data/county.txt
      one row per county and "cid" "0" for the state, whose title names
      the election, its date and the count's status:
      "11/03/2026 UNOFFICIAL GENERAL ELECTION RESULTS - STATEWIDE"
      (the 2024 general's read "OFFICIAL" once the State Board canvassed)
  {base_url}{yyyymmdd}/data/results_0.txt
      the statewide total of every choice in every contest:
      {"cnm": "US HOUSE OF REPRESENTATIVES DISTRICT 01 (VOTE FOR 1)",
       "bnm": "Don Davis", "pty": "DEM", "vct": "156572",
       "prt": "290", "ptl": "290", ...}

A date the election list doesn't hold is no election (None). The
statewide title must carry the date asked for and name a general
election, else the folder holds something else (UntrustedCount);
"OFFICIAL" at its head is read as the official flag. "Write-In
(Miscellaneous)" is no candidate; the contest's total counts it.

The reporting figure is the contest's precincts reported ("prt") of
precincts ("ptl"), worded as precincts with results: a precinct reports
on its election-day count, and absentee and provisional ballots are added
for days after (every 2026 contest read all its precincts in on
2026-11-10, while counties were still canvassing). The results file's
Last-Modified header is when the count last moved. The State Board
certifies at its canvass, on the Tuesday three weeks after the election
(certification_deadlines.json), and its certified results are read by
state_candidates_nc_certified.py.
"""

import logging
import re
from datetime import date, timezone
from email.utils import parsedate_to_datetime

import httpx

from app.pipeline.fetch.election_results import (
    ContestCount,
    FeedUnreadable,
    StateCount,
    UntrustedCount,
    is_special_contest,
)
from app.pipeline.fetch.http_utils import FETCH_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import clean_display_name, is_not_a_person, normalize_party, parse_office
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)
_TITLE_RE = re.compile(r"^(?P<date>\d{2}/\d{2}/\d{4})\s+(?P<status>UNOFFICIAL|OFFICIAL)\s+(?P<kind>.+?)\s+RESULTS\b")
_VOTE_FOR_RE = re.compile(r"\s*\(VOTE FOR \d+\)\s*$", re.IGNORECASE)


def _int(raw) -> int | None:
    raw = str(raw if raw is not None else "").replace(",", "").strip()
    return int(raw) if raw.isdigit() else None


def parse_results(rows: list, state: str = "NC") -> list[ContestCount]:
    """The federal contests of one statewide results file, in its order.
    Raises FeedUnreadable on a row that isn't the shape above."""
    contests: dict[str, ContestCount] = {}
    totals: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise FeedUnreadable(f"{state} results file has a row that isn't an object")
        label = _VOTE_FOR_RE.sub("", str(row.get("cnm") or "")).strip()
        parsed = parse_office(label)
        if parsed is None:
            continue
        votes = _int(row.get("vct"))
        if votes is None:
            raise FeedUnreadable(f"{state} results row has votes {row.get('vct')!r}")
        contest = contests.get(label)
        if contest is None:
            contest = contests[label] = ContestCount(
                office=parsed[0], district=parsed[1], candidates=[],
                reporting_units=_int(row.get("prt")), total_units=_int(row.get("ptl")) or None,
                is_special=is_special_contest(label),
            )
        totals[label] = totals.get(label, 0) + votes
        name = str(row.get("bnm") or "").strip()
        if not name or is_not_a_person(name):
            continue
        contest.candidates.append((clean_display_name(name), normalize_party(row.get("pty") or "", ballot_list=True), votes))
    for label, contest in contests.items():
        contest.total_votes = totals[label]
    return list(contests.values())


async def _json(client, url: str, label: str):
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=60.0, log_label=label, headers=FETCH_HEADERS,
    )
    if resp is None or resp.status_code != 200:
        raise FeedUnreadable(f"{label} could not be read")
    try:
        return resp, resp.json()
    except ValueError as exc:
        raise FeedUnreadable(f"{label} is not JSON") from exc


async def fetch_general_results(client: httpx.AsyncClient, election_day: date, state: str, source: dict):
    """North Carolina's count for the general on `election_day`, or None
    when the results app holds no election on that date."""
    base = source.get("base_url")
    if not base:
        raise FeedUnreadable(f"{state} nc_enr source needs base_url")
    base = base.rstrip("/") + "/"
    _, listed = await _json(client, base + "elections.txt", f"{state} results election list")
    day = election_day.strftime("%m/%d/%Y")
    if not isinstance(listed, list) or not any(isinstance(e, dict) and e.get("edt") == day for e in listed):
        return None
    folder = f"{base}{election_day.strftime('%Y%m%d')}/data/"
    _, counties = await _json(client, folder + "county.txt", f"{state} results status")
    statewide = next((c for c in counties if isinstance(c, dict) and str(c.get("cid")) == "0"), None) \
        if isinstance(counties, list) else None
    title = _TITLE_RE.match(str((statewide or {}).get("tle") or ""))
    if title is None:
        raise FeedUnreadable(f"{state} results status has no statewide title")
    if title.group("date") != day or "GENERAL" not in title.group("kind"):
        raise UntrustedCount(f"{state} results for {day} are titled {statewide.get('tle')!r}")
    resp, rows = await _json(client, folder + "results_0.txt", f"{state} statewide results")
    if not isinstance(rows, list):
        raise FeedUnreadable(f"{state} statewide results are not a list")
    try:
        updated = parsedate_to_datetime(resp.headers.get("Last-Modified") or "").astimezone(timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        updated = None
    return StateCount(
        source_name=source.get("source_name") or "North Carolina State Board of Elections (election results)",
        page_url=source.get("page_url") or base, official=title.group("status") == "OFFICIAL",
        unit_label="precincts", units_complete=False, contests=parse_results(rows, state), source_updated=updated,
    )
