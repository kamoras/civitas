"""Hawaii's election-night count, from the Office of Elections' summary
file (elections.hawaii.gov), read 2026-11-10.

The results page (`page_url`) links each election's folder of reports;
the general's summary file is found there by `link_regex` with the year
filled in ("2026 General/summary.txt" on the Office's own host; the 2024
general's has moved to files.hawaii.gov, where the page still links it).
Beside it in the same folder, histatewide.pdf is the same count as the
statewide summary report, whose first page names the election and its
date and carries the report's status:

    GENERAL ELECTION 2026 - State of Hawaii - Statewide Page 1
    November 3, 2026
    Printed on: 11/04/2026 at 09:43:14 pm
    SUMMARY REPORT
    REPORT 4

The 2026 general read "REPORT 4" on 2026-11-10; the 2026 primary's and
the 2024 general's final reports read "FINAL" and "Final". The date line
must be the election day asked for, else the folder holds another
election (UntrustedCount), and "FINAL" is read as the count's official
flag.

The summary file has one row per candidate: a "#Format" line, then a
header row whose names are prefixed with "#" ("#Contest ID", "Contest
Title", "Total Precincts", "Counted Precincts", "Candidate Name", "Total
Votes"). The 2026 general's is UTF-8 and tab-separated; the 2024
general's UTF-16 and comma-separated, so both are read from the file's
own byte-order mark and header. A name is printed with its party first,
"(D) CASE, Ed"; "(N)" is nonpartisan. Blank and over votes are columns of
their own and not counted in the total.

The reporting figure is counted precincts of total precincts, worded as
precincts with results: nothing in the file says a counted precinct's
mail ballots are all in (the 2026 general read 112 of 223 for the 1st
district a week after the election, every precinct in the final reports).

No certification deadline is read: Hawaii's law has the Chief Election
Officer certify once the time for an election contest has run, and names
no day (NASS, Hawaii Statutes 11-155; 11-156).
"""

import csv
import io
import logging
import re
from datetime import date, datetime

import httpx
import pdfplumber

from app.pipeline.fetch.election_results import (
    ContestCount,
    FeedUnreadable,
    StateCount,
    UntrustedCount,
    is_general_day,
    is_special_contest,
)
from app.pipeline.fetch.http_utils import FETCH_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import first_last, is_not_a_person, normalize_party, parse_office
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)
_NAME_RE = re.compile(r"^\(\s*(?P<party>[A-Z]+)\s*\)\s*(?P<name>.+)$")
_COLUMNS = ("Contest Title", "Total Precincts", "Counted Precincts", "Candidate Name", "Total Votes")


def _int(raw) -> int | None:
    raw = str(raw if raw is not None else "").replace(",", "").strip()
    return int(raw) if raw.isdigit() else None


def _text(content: bytes) -> str:
    if content[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return content.decode("utf-16")
    return content.decode("utf-8-sig")


def parse_summary(content: bytes, state: str = "HI") -> list[ContestCount]:
    """The federal contests of one summary file, in its row order. Raises
    FeedUnreadable when the file has no header row with the columns above."""
    lines = _text(content).splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("#Contest ID")), None)
    if start is None:
        raise FeedUnreadable(f"{state} summary file has no header row")
    delimiter = "\t" if "\t" in lines[start] else ","
    rows = list(csv.reader(io.StringIO("\n".join(lines[start:])), delimiter=delimiter))
    header = [c.lstrip("#").strip() for c in rows[0]]
    if any(c not in header for c in _COLUMNS):
        raise FeedUnreadable(f"{state} summary file has columns {header!r}")
    contests: dict[str, ContestCount] = {}
    for cells in rows[1:]:
        row = dict(zip(header, (c.strip() for c in cells)))
        label = row.get("Contest Title") or ""
        parsed = parse_office(label)
        if parsed is None:
            continue
        votes = _int(row.get("Total Votes"))
        named = _NAME_RE.match(row.get("Candidate Name") or "")
        if votes is None or not named:
            raise FeedUnreadable(f"{state} summary file row isn't its layout: {cells!r}")
        contest = contests.get(label)
        if contest is None:
            contest = contests[label] = ContestCount(
                office=parsed[0], district=parsed[1], candidates=[],
                reporting_units=_int(row.get("Counted Precincts")),
                total_units=_int(row.get("Total Precincts")) or None,
                is_special=is_special_contest(label),
            )
        name = named.group("name")
        if is_not_a_person(name):
            continue
        contest.candidates.append((first_last(name), normalize_party(named.group("party"), ballot_list=True), votes))
    return list(contests.values())


def report_status(pdf: bytes) -> tuple[date | None, bool]:
    """(the election's date, whether the report is final) from the
    statewide summary report's first page."""
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        text = (doc.pages[0].extract_text() or "") if doc.pages else ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    held = None
    for line in lines[:4]:
        try:
            held = datetime.strptime(line, "%B %d, %Y").date()
            break
        except ValueError:
            continue
    final = False
    if "SUMMARY REPORT" in lines:
        after = lines.index("SUMMARY REPORT") + 1
        final = after < len(lines) and lines[after].casefold() == "final"
    return held, final


async def _get(client, url: str, label: str):
    return await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=60.0, log_label=label, headers=FETCH_HEADERS,
        expected_statuses=(404,),
    )


async def fetch_general_results(client: httpx.AsyncClient, election_day: date, state: str, source: dict):
    """Hawaii's count for the general on `election_day`, or None when the
    day is no general or the results page links no summary for its year."""
    if not is_general_day(election_day):
        return None
    page_url, pattern = source.get("page_url"), source.get("link_regex")
    if not page_url or not pattern:
        raise FeedUnreadable(f"{state} hi_summary source needs page_url and link_regex")
    page = await _get(client, page_url, f"{state} results page")
    if page is None or page.status_code != 200:
        raise FeedUnreadable(f"{state} results page could not be read")
    found = re.search(pattern.replace("{year}", str(election_day.year)), page.text)
    if not found:
        return None
    summary_url = found.group(1)
    summary = await _get(client, summary_url, f"{state} results summary")
    report = await _get(client, summary_url.rsplit("/", 1)[0] + "/histatewide.pdf", f"{state} statewide report")
    if summary is None or summary.status_code != 200 or report is None or report.status_code != 200:
        raise FeedUnreadable(f"{state} results files could not be read")
    try:
        held, final = report_status(report.content)
    except Exception as exc:  # pdfminer raises its own classes on a damaged file
        raise FeedUnreadable(f"{state} statewide report could not be read: {exc}") from exc
    if held != election_day:
        raise UntrustedCount(f"{state} statewide report is dated {held}, not {election_day}")
    return StateCount(
        source_name=source.get("source_name") or "Hawaii Office of Elections (election results)",
        page_url=page_url, official=final, unit_label="precincts", units_complete=False,
        contests=parse_summary(summary.content, state),
    )
