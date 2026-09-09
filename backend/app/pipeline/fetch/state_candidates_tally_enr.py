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
   names every contest and candidate for that election -- this endpoint's
   own contestType param is accepted but silently ignored (verified live
   for both states: identical response with or without it), so it is
   never sent here regardless of config. Arkansas's own real contests
   carry a `contestTypeCode` of "Federal" for federal races specifically;
   `contest_type_filter: "Federal"` in config uses that as a cheap
   client-side pre-filter. But parse_office() against each contest's own
   contestName is ALWAYS applied too, for every state -- it is the real
   source of truth for "is this federal", never bypassed just because a
   contest_type_filter happens to be configured (a stale/typo'd filter
   value must not be able to silently exclude every real contest with no
   signal anything broke). North Dakota's real contests are all typed
   "SW" (statewide) regardless of office -- a real ballot measure,
   Secretary of State, and Representative in Congress race all share that
   one code -- so North Dakota configures no `contest_type_filter` at
   all, and parse_office() alone does the whole job, correctly refusing
   "Secretary of State"/"Attorney General"/ballot measures the same way
   it refuses any other state's non-federal races.
3. GET {base_url}/Contest/GetContestResults?cId={cid}&electionID={id}
   &contestType={results_scope, if configured} carries the actual vote
   totals, keyed by the SAME contest/choice ids the search list uses.
   Unlike GetContestSearchList, this endpoint's contestType param DOES
   change what comes back, and DOES matter for real efficiency: verified
   live that North Dakota's bare, unscoped call returns type `_ALL_`
   (every county/city contest too, ~1500 total, ~2MB) where
   `&contestType=SW` narrows it to the real 18 statewide-office ones
   (~400KB) -- the federal contest DATA inside is byte-identical either
   way, so this is purely about not making the vendor (and this module)
   do ~80x the unneeded work every run, not a correctness question.
   `results_scope` is therefore a SEPARATE config key from
   `contest_type_filter` (defaulting to it when absent, which is correct
   for Arkansas since its "Federal" value serves both roles identically)
   -- the two diverge for North Dakota specifically, where "SW" is the
   right REQUEST scope but cannot serve as the federal-detection filter
   (it doesn't distinguish federal races at all, per point 2 above).

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
    election_id: str, contest_type_filter: str | None, results_scope: str | None, year: int,
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
        if contest_type_filter is not None and c.get("contestTypeCode") != contest_type_filter:
            continue
        # parse_office() is always the real source of truth for "is this
        # federal" -- contest_type_filter (where a state has one) is only
        # a cheap pre-filter, never a substitute for it: a typo'd/stale
        # contest_type_filter value would otherwise silently exclude
        # every real contest with no signal anything broke. North
        # Dakota's real contests all share one contestTypeCode ("SW")
        # regardless of office, so it configures no contest_type_filter
        # at all and this check does the whole job alone.
        if parse_office(c["contestName"]) is None:
            continue
        federal[cid_key] = c
    if not federal:
        return {}, {}

    results_url = f"{base_url}/Contest/GetContestResults?cId={cid}&electionID={election_id}"
    if results_scope is not None:
        results_url += f"&contestType={results_scope}"
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
    # The value to scope the GetContestResults request with is usually the
    # SAME as contest_type_filter (Arkansas's "Federal" narrows both the
    # client-side federal check AND the request), but not always: North
    # Dakota's real contestTypeCode ("SW") scopes the request down from
    # ~1500 contests (every office AND ballot measure statewide) to the 18
    # real statewide-office ones, verified live -- but "SW" does NOT
    # distinguish federal races from Secretary of State/Attorney General/
    # etc, so it can't double as contest_type_filter the way Arkansas's
    # value does. results_scope defaults to contest_type_filter (the
    # common case) and is only set separately when a state's two roles
    # genuinely diverge.
    results_scope = source.get("results_scope", contest_type_filter)

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
            client, state, base_url, cid, election["id"], contest_type_filter, results_scope, year,
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
