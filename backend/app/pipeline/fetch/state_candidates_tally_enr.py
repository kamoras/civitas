""""Tally ENR" (totalresults.com) election-night-reporting vendor,
confirmed on TWO states so far -- Arkansas (the original single-state
build, generalized here after a second state showed up on the exact same
API shape, the same "second state justifies generalizing" rule this
system's TotalVote/Clarity strategies already followed) and North Dakota
(found live 2026-09-09 at a different domain, api.resultsnd.sos.nd.gov,
cid=north-dakota -- same GetElectionList/GetContestSearchList/
GetContestResults endpoint names, same field names, confirmed by direct
comparison of real live responses from both states). No login, no
session, no bot-detection friction on either state's deployment: a plain
unauthenticated GET succeeds identically to a real browser session.

THREE calls per state, base_url and cid both read from config (never
hardcoded -- a state's own client id is the only thing that varies):

1. GET {base_url}/Election/GetElectionList?cid={cid} lists every election
   back to 2000 with a name and a date -- the primary (and, for a state
   that HAS one, a runoff) for the target year are found by matching
   `electionName` against `primary_name_regex`/`runoff_name_regex` from
   config (case-insensitive, state's own real wording: Arkansas's legal
   term is "Preferential Primary"/"Primary Runoff"; North Dakota's is the
   bare "Primary Election", and North Dakota's real 2026 election list
   carries only ONE entry for the year -- no separate runoff stage exists
   in its own calendar at all, consistent with nominating by plurality)
   whose `electionDate` falls in the target year -- never a hardcoded
   election id.
2. GET {base_url}/Contest/GetContestSearchList?cid={cid}&electionID={id}
   names every contest and candidate for that election. Arkansas's own
   real contests carry a `contestTypeCode` of "Federal" for federal races
   specifically, so `contest_type_filter: "Federal"` in config narrows to
   those before any other processing (a real ~550-contest statewide
   response, cheap to pre-filter). North Dakota's real contests are ALL
   typed "SW" (statewide) regardless of office -- a real ballot measure,
   Secretary of State, and Representative in Congress race all share that
   one code -- so North Dakota's config omits `contest_type_filter`
   entirely, and every contest is instead individually checked by the
   shared parse_office() against its own contestName, which already
   correctly refuses "Secretary of State"/"Attorney General"/ballot
   measures the same way it refuses any other state's non-federal races.
3. GET {base_url}/Contest/GetContestResults?cId={cid}&electionID={id}
   &contestType={type or omitted} carries the actual vote totals, keyed
   by the SAME contest/choice ids the search list uses. Arkansas's own
   contestType filter on this endpoint DOES work (an off-year runoff with
   zero federal races returns an empty federal contest list, not an
   error) -- North Dakota's equivalent call, `contestType=SW`, returns
   every statewide contest's results including ballot measures and
   non-federal offices' locations/precinct breakdowns (~400KB real
   response), which the shared per-contest office/party filtering below
   already discards without needing the request itself narrowed further.

Party is a literal suffix/prefix baked into the contest name on both
real states ("REP U.S. Senate" in Arkansas, "Representative in Congress
Democratic-NPL" in North Dakota) -- normalize_party() on the contest
name recognises both real wordings unchanged, no per-state branch
needed. Only a CONTESTED race gets its own contest entry at all on
either vendor -- an unopposed seat simply has no contest, left to the
ordinary FEC-filer fallback.

Runoff handling is real where a state has one: both the primary AND
runoff election ids are fetched every run when `runoff_name_regex` is
configured, and a runoff stage's result for a seat+party REPLACES the
primary's, the same override rule every other runoff-threshold state on
this system already uses. Verified live against Arkansas's real 2026
cycle: the runoff election id carries zero federal contests (every
federal race cleared 50% in the first round that cycle), proving this
path safely no-ops rather than just being written and untested. A state
with no `runoff_name_regex` configured (North Dakota) never looks for a
second stage at all.

Neither vendor's payload carries a certification/official flag anywhere
in the responses captured live for either state -- the same shape as
Tennessee and Florida in this codebase -- so a nominee is confirmed only
once `settle_days` has passed since that STAGE's own `electionDate`
(reusing `_settled` from state_candidates_tabular.py rather than
re-deriving the same freshness rule a third time). Without this, a
provisional election-night lead -- before absentee/late precincts are
counted -- could be confirmed as final.
"""

import logging
import re

import httpx

from app.pipeline.fetch.http_utils import fetch_json_with_retry
from app.pipeline.fetch.state_candidates_common import normalize_party, parse_office, pick_nominee, surname
from app.pipeline.fetch.state_candidates_tabular import DEFAULT_SETTLE_DAYS, _settled
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)


async def _discover_elections(
    client: httpx.AsyncClient, state: str, base_url: str, cid: str,
    primary_re: re.Pattern, runoff_re: re.Pattern | None, year: int,
) -> tuple[dict | None, dict | None]:
    """(primary, runoff) for `year`, each {"id", "date"} or None if that
    stage isn't in the list yet (or, for a state with no runoff config,
    never looked for at all) -- never a guessed/hardcoded id."""
    elections = await fetch_json_with_retry(
        client, _rate_limiter, f"{base_url}/Election/GetElectionList?cid={cid}", f"{state} election list {year}",
    )
    if not isinstance(elections, list):
        return None, None
    primary = runoff = None
    for e in elections:
        date = str(e.get("electionDate") or "")
        name = e.get("electionName") or ""
        eid = e.get("electionID")
        if not date.startswith(str(year)) or not eid:
            continue
        if primary is None and primary_re.search(name):
            primary = {"id": eid, "date": date}
        elif runoff_re is not None and runoff is None and runoff_re.search(name):
            runoff = {"id": eid, "date": date}
    return primary, runoff


async def _federal_contests_and_results(
    client: httpx.AsyncClient, state: str, base_url: str, cid: str,
    election_id: str, contest_type_filter: str | None, year: int,
) -> tuple[dict, dict] | None:
    """(federal contests by id from the search list, their vote totals
    from the results endpoint) for one election, or None on a real fetch
    failure. `{}` for both is a healthy "this stage decided nothing
    federal" (the normal shape of a runoff stage in a cycle that needed
    none)."""
    search = await fetch_json_with_retry(
        client, _rate_limiter, f"{base_url}/Contest/GetContestSearchList?cid={cid}&electionID={election_id}",
        f"{state} contest names {year}",
    )
    if not isinstance(search, dict):
        return None
    contests = ((search.get("response") or {}).get("contests")) or {}
    federal = {}
    for cid_key, c in contests.items():
        if not c.get("contestName"):
            continue
        if contest_type_filter is not None:
            if c.get("contestTypeCode") != contest_type_filter:
                continue
        elif parse_office(c["contestName"]) is None:
            # No contestTypeCode distinguishes federal races on this
            # vendor deployment (North Dakota's are all "SW") -- fall
            # back to the shared office parser alone, which already
            # refuses non-federal labels the same way every other
            # module does.
            continue
        federal[cid_key] = c
    if not federal:
        return {}, {}

    results_url = f"{base_url}/Contest/GetContestResults?cId={cid}&electionID={election_id}"
    if contest_type_filter is not None:
        results_url += f"&contestType={contest_type_filter}"
    results = await fetch_json_with_retry(client, _rate_limiter, results_url, f"{state} federal results {year}")
    if not isinstance(results, dict):
        return None
    return federal, ((results.get("response") or {}).get("contests")) or {}


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    base_url = source.get("base_url")
    cid = source.get("cid")
    primary_name_regex = source.get("primary_name_regex")
    if not base_url or not cid or not primary_name_regex:
        logger.warning("%s tally_enr config is missing base_url/cid/primary_name_regex", state)
        return None
    primary_re = re.compile(primary_name_regex, re.IGNORECASE)
    runoff_name_regex = source.get("runoff_name_regex")
    runoff_re = re.compile(runoff_name_regex, re.IGNORECASE) if runoff_name_regex else None
    contest_type_filter = source.get("contest_type_filter")

    threshold = source.get("runoff_threshold_pct")
    settle_days = source.get("settle_days", DEFAULT_SETTLE_DAYS)
    primary, runoff = await _discover_elections(client, state, base_url, cid, primary_re, runoff_re, year)
    if primary is None:
        return []  # not published yet this cycle — healthy unknown

    by_seat: dict[tuple[str, int | None, str], tuple[str, float]] = {}
    # Runoff processed second so its answer for a seat overrides the primary's.
    for election, stage_threshold in ((primary, threshold), (runoff, None)):
        if election is None or not _settled(election["date"], settle_days):
            continue  # no stage yet, or this stage's count isn't settled
        fetched = await _federal_contests_and_results(
            client, state, base_url, cid, election["id"], contest_type_filter, year,
        )
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
            # Every choice's votes count toward the total (an unresolvable
            # name still counted a real vote), but only a resolvable name
            # can be confirmed the winner below -- a candidate the search
            # list doesn't know about should shrink everyone else's
            # percentage, never be silently excluded from both sides.
            choices = [
                (surname((choice_names.get(ch.get("choiceID")) or {}).get("name") or ""), ch.get("totalVotes"))
                for ch in contest_result.get("choices") or []
            ]
            seat = (office, district, party)
            won = pick_nominee(choices, runoff_threshold_pct=stage_threshold)
            if won and won[0]:
                by_seat[seat] = won

    return [
        {"office": o, "district": d, "party": p, "last_name": name}
        for (o, d, p), (name, _pct) in by_seat.items()
    ]
