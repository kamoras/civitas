"""Arkansas's confirmed-general-candidate strategy — the Secretary of
State's own election-night-reporting vendor ("Tally ENR" / totalresults.
com), reached with no login, no session and no bot-detection friction at
all (a real browser session and a plain httpx GET both succeed identically
— verified live 2026-09-04). Not found on any other state probed (guessed
subdomains for 15 other uncovered states all failed DNS resolution), so
this is written as a single-state module for now, the same way KY/IN/AL/
MS each started.

THREE calls, nothing cycle-specific hardcoded:

1. GET .../Election/GetElectionList?cid=arkansas lists every election back
   to 2000 with a name and a date — the primary and (if one exists) runoff
   for the target year are found by matching `electionName` (case-
   insensitive substring: "Preferential Primary" is Arkansas's own legal
   term for its first-round primary; "Primary Runoff" for the second
   round) against entries whose `electionDate` falls in the target year,
   never a hardcoded election id.
2. GET .../Contest/GetContestSearchList?cid=arkansas&electionID={id} names
   every contest and candidate for that election — a contestType query
   param is accepted but silently ignored (verified live: the response is
   identical with or without it), so the ~550-contest statewide response
   is filtered client-side on each contest's own `contestTypeCode ==
   "Federal"` field instead.
3. GET .../Contest/GetContestResults?cId=arkansas&electionID={id}&contestType=Federal
   carries the actual vote totals, keyed by the SAME contest/choice ids
   the search list uses — this endpoint's own contestType filter DOES
   work (verified: an off-year runoff with zero federal races on it
   returns an empty federal contest list, not an error).

Arkansas prints party as a PREFIX on the contest name itself ("REP U.S.
Senate", "DEM U.S. Congress District 02") rather than a candidate-level
column, and only ever publishes a CONTESTED race as its own contest — an
unopposed seat (Arkansas's real 2026 CD1, CD3, and the CD4 Republican side
were all unopposed) simply has no contest entry at all, so it keeps the
ordinary FEC-filer fallback rather than needing anything from here.

Runoff handling is real: both the primary AND runoff election ids are
fetched every run, and a runoff stage's result for a seat+party REPLACES
the primary's — the same override rule every other runoff-threshold state
on this system already uses. Verified live against the real 2026 cycle:
the runoff election id carries zero federal contests (every federal race
cleared 50% in the first round that cycle), so this path is proven to
safely no-op, not just written and untested.
"""

import logging
import re

import httpx

from app.pipeline.fetch.http_utils import BROWSER_JSON_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import normalize_party, parse_office, pick_nominee, surname
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

BASE = "https://enr-results-api.totalresults.com"
CID = "arkansas"

_PRIMARY_NAME_RE = re.compile(r"preferential primary", re.IGNORECASE)
_RUNOFF_NAME_RE = re.compile(r"primary runoff", re.IGNORECASE)

_HEADERS = BROWSER_JSON_HEADERS
_rate_limiter = RateLimiter(rps=1.0)


async def _get_json(client: httpx.AsyncClient, url: str, label: str) -> dict | list | None:
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=30.0, log_label=label, headers=_HEADERS,
    )
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError:
        logger.warning("%s did not return valid JSON", label)
        return None


async def _discover_election_ids(client: httpx.AsyncClient, year: int) -> tuple[str | None, str | None]:
    """(primary_id, runoff_id) for `year`, either None if that stage
    isn't in the list yet — never a guessed/hardcoded id."""
    elections = await _get_json(client, f"{BASE}/Election/GetElectionList?cid={CID}", f"AR election list {year}")
    if not isinstance(elections, list):
        return None, None
    primary_id = runoff_id = None
    for e in elections:
        date = str(e.get("electionDate") or "")
        name = e.get("electionName") or ""
        eid = e.get("electionID")
        if not date.startswith(str(year)) or not eid:
            continue
        if primary_id is None and _PRIMARY_NAME_RE.search(name):
            primary_id = eid
        elif runoff_id is None and _RUNOFF_NAME_RE.search(name):
            runoff_id = eid
    return primary_id, runoff_id


async def _federal_contests_and_results(
    client: httpx.AsyncClient, election_id: str, year: int,
) -> tuple[dict, dict] | None:
    """(federal contests by id from the search list, their vote totals
    from the results endpoint) for one election, or None on a real fetch
    failure. `{}` for both is a healthy "this stage decided nothing
    federal" (the normal shape of a runoff stage in a cycle that needed
    none)."""
    search = await _get_json(
        client, f"{BASE}/Contest/GetContestSearchList?cid={CID}&electionID={election_id}",
        f"AR contest names {year}",
    )
    if search is None:
        return None
    contests = ((search.get("response") or {}).get("contests")) or {}
    federal = {
        cid: c for cid, c in contests.items()
        if c.get("contestTypeCode") == "Federal" and c.get("contestName")
    }
    if not federal:
        return {}, {}

    results = await _get_json(
        client, f"{BASE}/Contest/GetContestResults?cId={CID}&electionID={election_id}&contestType=Federal",
        f"AR federal results {year}",
    )
    if results is None:
        return None
    return federal, ((results.get("response") or {}).get("contests")) or {}


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state unused, this strategy is AR-only by construction
) -> list[dict] | None:
    threshold = source.get("runoff_threshold_pct")
    primary_id, runoff_id = await _discover_election_ids(client, year)
    if primary_id is None:
        return []  # not published yet this cycle — healthy unknown

    by_seat: dict[tuple[str, int | None, str], tuple[str, float]] = {}
    # Runoff processed second so its answer for a seat overrides the primary's.
    for election_id, stage_threshold in ((primary_id, threshold), (runoff_id, None)):
        if election_id is None:
            continue
        fetched = await _federal_contests_and_results(client, election_id, year)
        if fetched is None:
            return None
        federal, result_contests = fetched

        for contest_id, contest in federal.items():
            office_district = parse_office(contest["contestName"])
            party = normalize_party(contest["contestName"])
            contest_result = result_contests.get(contest_id)
            if office_district is None or party is None or contest_result is None:
                continue
            office, district = office_district
            choice_names = contest.get("choices") or {}
            choices = [
                (surname(choice_names[ch["choiceID"]]["name"]), ch.get("totalVotes"))
                for ch in contest_result.get("choices") or []
                if ch.get("choiceID") in choice_names
            ]
            choices = [(n, v) for n, v in choices if n]
            seat = (office, district, party)
            won = pick_nominee(choices, runoff_threshold_pct=stage_threshold)
            if won:
                by_seat[seat] = won
            elif stage_threshold is not None:
                # Below-majority in the PRIMARY: leave the seat unresolved
                # so a same-cycle runoff stage (processed next) can still
                # answer it, rather than a stale entry blocking it forever.
                by_seat.pop(seat, None)

    return [
        {"office": o, "district": d, "party": p, "last_name": name}
        for (o, d, p), (name, _pct) in by_seat.items()
    ]
