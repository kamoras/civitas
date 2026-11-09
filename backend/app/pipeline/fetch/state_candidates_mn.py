"""Minnesota's election-night count, from the Secretary of State's media
files (electionresultsfiles.sos.mn.gov), read 2026-11-09.

The same file host the tabular primary adapter reads, which answers every
request; the page that lists the files sits behind a bot manager and is
not read. The files are named by the election's date
(`/20261103/ussenate.txt`, `/20261103/ushouse.txt`), and a date with no
election answers 404 (20261104 and 20250101 did).

Each line is one candidate's statewide (or district-wide) total, no
header row, semicolon-separated, in the order the tabular adapter's
config names (state, county id, precinct id, office id, office name,
district, candidate id, candidate, suffix, incumbent, party, precincts
reporting, precincts total, votes, percent, total votes):

    MN;;;0104;U.S. Representative District 1;1;0302;Brad Finstad;;;R;750;750;162733;49.89;326179

The office names read through parse_office ("U.S. Senator",
"U.S. Representative District 1"). The aggregated "WRITE-IN" line is no
candidate; the contest's own total (the last column) still counts it.

The reporting figure is the line's precincts reporting of precincts
total, worded as precincts with results ("results from N of M"): nothing
in the file says a reporting precinct's count is complete. Nothing in it
says the count is official either; the State Canvassing Board certifies
on the third Tuesday after the election (certification_deadlines.json),
which is what the deadline rule reads.
"""

import logging
from datetime import date, timezone
from email.utils import parsedate_to_datetime

import httpx

from app.pipeline.fetch.election_results import ContestCount, FeedUnreadable, StateCount, is_special_contest
from app.pipeline.fetch.http_utils import FETCH_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import clean_display_name, is_not_a_person, normalize_party, parse_office
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

# Positions in a line (see the module docstring).
_OFFICE, _CANDIDATE, _PARTY, _REPORTING, _TOTAL_UNITS, _VOTES, _TOTAL_VOTES = 4, 7, 10, 11, 12, 13, 15
_FIELDS = 16


def _int(raw: str) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else None


def parse_media_file(text: str, state: str = "MN") -> list[ContestCount]:
    """The contests of one media file, in its line order. Raises
    FeedUnreadable on a line that isn't the layout above."""
    contests: dict[str, ContestCount] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        cells = line.split(";")
        votes = _int(cells[_VOTES]) if len(cells) == _FIELDS else None
        if votes is None:
            raise FeedUnreadable(f"{state} results file has a line that isn't its layout: {line[:80]!r}")
        label = cells[_OFFICE].strip()
        parsed = parse_office(label)
        if parsed is None:
            continue
        contest = contests.get(label)
        if contest is None:
            contest = contests[label] = ContestCount(
                office=parsed[0], district=parsed[1], candidates=[],
                total_votes=_int(cells[_TOTAL_VOTES]),
                reporting_units=_int(cells[_REPORTING]), total_units=_int(cells[_TOTAL_UNITS]) or None,
                is_special=is_special_contest(label),
            )
        name = cells[_CANDIDATE].strip()
        if not name or is_not_a_person(name):
            continue
        contest.candidates.append((clean_display_name(name), normalize_party(cells[_PARTY], ballot_list=True), votes))
    for contest in contests.values():
        if contest.total_votes is not None and contest.total_votes < sum(v for _, _, v in contest.candidates):
            contest.total_votes = None
    return list(contests.values())


async def fetch_general_results(client: httpx.AsyncClient, election_day: date, state: str, source: dict):
    """Minnesota's count for `election_day`, or None when the Secretary of
    State has no results files for that date."""
    base, files = source.get("base_url"), source.get("files") or []
    if not base or not files:
        raise FeedUnreadable(f"{state} mn_media source needs base_url and files")
    folder = f"{base.rstrip('/')}/{election_day.strftime('%Y%m%d')}/"
    contests, updated, found = [], None, False
    for name in files:
        resp = await fetch_with_retry(
            client, _rate_limiter, "GET", folder + name, timeout=30.0,
            log_label=f"{state} results {name}", headers=FETCH_HEADERS, expected_statuses=(404,),
        )
        if resp is None:
            raise FeedUnreadable(f"{state} results file {name} could not be read")
        if resp.status_code == 404:
            continue
        found = True
        contests.extend(parse_media_file(resp.text, state))
        try:
            stamp = parsedate_to_datetime(resp.headers.get("Last-Modified") or "")
        except (TypeError, ValueError):
            stamp = None
        if stamp is not None:
            stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
            updated = max(updated, stamp) if updated else stamp
    if not found:
        return None
    return StateCount(
        source_name=source.get("source_name") or "Minnesota Secretary of State (election results files)",
        page_url=source.get("page_url") or folder, official=False,
        unit_label="precincts", units_complete=False, contests=contests, source_updated=updated,
    )
