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
from app.pipeline.fetch.state_candidates_common import (
    runoff_threshold,
    clean_display_name,
    federal_only,
    federal_record,
    is_not_a_person,
    normalize_party,
    parse_office,
    parse_state_leg_office,
    parse_statewide_office,
    pick_nominees,
)
from app.pipeline.fetch.state_candidates_tabular import DEFAULT_SETTLE_DAYS, _settled
from app.pipeline.fetch.election_results import (
    ContestCount,
    StateCount,
    UntrustedCount,
    is_special_contest,
    parse_utc,
    pick_general,
)
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
    state_offices: bool = False,
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
        # The type pre-filter is dropped entirely for a state reading
        # its own offices: it exists only to narrow the federal case
        # cheaply, and Arkansas's "Federal" value would discard every
        # state contest before the gates ever saw one. The gates below
        # are the real source of truth either way, as this module has
        # always said.
        if (not state_offices and contest_type_filter is not None
                and c.get("contestTypeCode") != contest_type_filter):
            continue
        # parse_office() is always the real source of truth for "is this
        # federal" -- contest_type_filter (where a state has one) is only
        # a cheap pre-filter, never a substitute for it: a typo'd/stale
        # contest_type_filter value would otherwise silently exclude
        # every real contest with no signal anything broke. North
        # Dakota's real contests all share one contestTypeCode ("SW")
        # regardless of office, so it configures no contest_type_filter
        # at all and this check does the whole job alone.
        if not _understood(c["contestName"], state_offices):
            continue
        federal[cid_key] = c
    if not federal:
        return {}, {}

    results_url = f"{base_url}/Contest/GetContestResults?cId={cid}&electionID={election_id}"
    # Scoping the results request is likewise dropped: North Dakota's
    # "SW" narrows to its 18 statewide contests and would leave its 100
    # legislative ones with no votes at all. Unscoped is 1.9 MB in under
    # a second, measured live, which is cheap for a nightly job.
    if results_scope is not None and not state_offices:
        results_url += f"&contestType={results_scope}"
    results = await fetch_json_with_retry(client, _rate_limiter, results_url, f"{state} federal results {year}")
    if not isinstance(results, dict):
        return None
    return federal, ((results.get("response") or {}).get("contests")) or {}


def _understood(name: str, state_offices: bool) -> bool:
    """Whether any gate claims this contest. Kept beside the search-list
    filter so a contest is never fetched and then silently dropped."""
    if parse_office(name) is not None:
        return True
    if not state_offices:
        return False
    return (parse_statewide_office(name) is not None
            or parse_state_leg_office(name) is not None)


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
    state_offices = bool(source.get("statewide_offices"))
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

    threshold = runoff_threshold(source)
    settle_days = source.get("settle_days", DEFAULT_SETTLE_DAYS)
    primary, runoff = await _discover_elections(client, state, base_url, cid, primary_re, runoff_re, year)
    if primary is None:
        return []  # not published yet this cycle — healthy unknown

    # The primary has settled and its runoff has not. Every office the
    # runoff decides is missing from a read of the primary alone, and under
    # the state-office opt-in the sync would take that list for the whole
    # ballot (deleting what it does not name). The primary's federal rows
    # stand -- a runoff-owed federal seat names nobody from it anyway --
    # and the state offices are marked incomplete (federal_only), as
    # Alabama withholds them while a runoff is owed.
    runoff_pending = (
        state_offices and runoff is not None and _settled(primary["date"], settle_days)
        and not _settled(runoff["date"], settle_days)
    )

    by_seat: dict[tuple, list[tuple[str, float]]] = {}
    short: set = set()  # state-office contests the primary left to a runoff
    runoff_read = False
    # Runoff processed second so its answer for a seat overrides the primary's.
    for election, stage_threshold in ((primary, threshold), (runoff, None)):
        if election is None or not _settled(election["date"], settle_days):
            continue  # no stage yet, or this stage's count isn't settled
        runoff_read = runoff_read or election is runoff
        fetched = await _federal_contests_and_results(
            client, state, base_url, cid, election["id"], contest_type_filter,
            results_scope, year, state_offices,
        )
        if fetched is None:
            return None
        federal, result_contests = fetched

        for contest_id, contest in federal.items():
            name = contest["contestName"]
            office_district = parse_office(name)
            seat = None
            if office_district is not None:
                office, district = office_district
                federal_race = True
            else:
                federal_race = False
                statewide = parse_statewide_office(name)
                if statewide is not None:
                    office, district = statewide
                else:
                    parsed_seat = parse_state_leg_office(name)
                    if parsed_seat is None:
                        continue
                    office, district, seat = parsed_seat
            party = normalize_party(name)
            contest_result = result_contests.get(contest_id)
            if party is None or contest_result is None:
                continue
            choice_names = contest.get("choices") or {}
            # Every choice's votes count toward the total (an unresolvable
            # name still counted a real vote), but only a resolvable name
            # can be confirmed the winner below -- a candidate the search
            # list doesn't know about should shrink everyone else's
            # percentage, never be silently excluded from both sides.
            # A federal nominee is cut to a surname for FEC matching; a
            # state-office nominee has no FEC row and keeps what the
            # state printed.
            # A federal name is kept whole here and reduced when the record
            # is built (federal_record), so the printed name survives.
            reduce = (lambda n: n) if federal_race else clean_display_name
            choices = [
                (reduce((choice_names.get(ch.get("choiceID")) or {}).get("name") or ""),
                 ch.get("totalVotes"))
                for ch in contest_result.get("choices") or []
            ]
            # This vendor states the seat count as a FIELD rather than in
            # the label, which is how North Dakota's two-members-per-
            # district House is read correctly. Honoured only where the
            # state runs one-nominee party primaries, same guard the
            # tabular adapter applies to "Vote for up to N".
            vote_for = contest.get("voteFor")
            advance = (
                vote_for if (isinstance(vote_for, int) and 1 <= vote_for <= 10
                             and not federal_race)
                else 1
            )
            key = (office, district, party, seat)
            won = pick_nominees(choices, stage_threshold, advance)
            if won:
                by_seat[key] = [(n, pct) for n, pct in won if n]
            elif stage_threshold is not None and not federal_race and pick_nominees(choices, None, advance):
                short.add(key)

    records = []
    for (o, d, p, st_seat), winners in by_seat.items():
        for name, _pct in winners:
            if o in ("S", "H"):
                record = federal_record(o, d, p, name)
                if record is None:
                    continue
            else:
                record = {"office": o, "district": d, "party": p, "last_name": name}
            if st_seat is not None:
                record["seat"] = st_seat
            records.append(record)
    # A primary contest fell short of the threshold and no settled runoff
    # was read -- the runoff election is not even listed yet. Same outcome
    # as runoff_pending: without it the primary alone, missing every office
    # still owed a runoff, would be published as the ballot and frozen
    # (Alabama's _runoff_owed). Only for a state that has a runoff stage.
    runoff_owed = state_offices and runoff_re is not None and bool(short) and not runoff_read
    if runoff_pending or runoff_owed:
        logger.info("%s: the %d runoff has not settled yet -- state offices incomplete", state, year)
        return federal_only(records)
    return records


# --- Live general-election counts (fetch/election_results.py) -------------

def _party(choice_party: str, meta_party: str, parties: dict) -> str | None:
    """A choice's party. Arkansas's results carry a numeric partyID to look
    up in the election's party table; North Dakota's table is null
    (`partyInfo: None`, verified 2026-09-28) and the search list's choice
    carries the code itself ("REP"), with the results' partyID blank."""
    for pid in (str(choice_party or "").strip(), str(meta_party or "").strip()):
        if not pid:
            continue
        name = (parties.get(pid) or {}).get("partyName") or pid
        party = normalize_party(name)
        if party is not None:
            return party
    return None


def general_contests(search: dict, results: dict, parties: dict) -> list[ContestCount]:
    """Every federal contest, joined across the search list (names) and
    the results (votes, precincts), with party read per choice through the
    election's own party table — a general-election contest's name carries
    no party the way a primary's does."""
    named = ((search.get("response") or {}).get("contests")) or {}
    counted = ((results.get("response") or {}).get("contests")) or {}
    out = []
    for contest_id, contest in named.items():
        name = contest.get("contestName") or ""
        parsed = parse_office(name)
        result = counted.get(contest_id)
        if parsed is None or not isinstance(result, dict):
            continue
        choice_names = contest.get("choices") or {}
        candidates = []
        for ch in result.get("choices") or []:
            meta = choice_names.get(ch.get("choiceID")) or {}
            votes = ch.get("totalVotes")
            label = clean_display_name(meta.get("name") or "")
            # North Dakota lists a "write-in" row with isWriteIn false; its
            # votes stay in the contest total, it just isn't a candidate.
            if meta.get("isWriteIn") or not label or is_not_a_person(label) or not isinstance(votes, int):
                continue
            candidates.append((label, _party(ch.get("partyID"), meta.get("partyID"), parties), votes))
        total = result.get("totalVotes")
        reporting, precincts = result.get("precinctsReporting"), result.get("totalPrecincts")
        out.append(ContestCount(
            office=parsed[0], district=parsed[1], candidates=candidates,
            total_votes=total if isinstance(total, int) else None,
            reporting_units=reporting if isinstance(reporting, int) else None,
            total_units=precincts if isinstance(precincts, int) and precincts > 0 else None,
            is_special=is_special_contest(name),
        ))
    return out


def _is_preview_id(eid) -> bool:
    """The vendor's own mark for a preview copy of an election: its id with
    "_Preview" on the end. North Dakota's config lists them
    (previewElections); the vendor's current front end, which Arkansas
    serves at arkansas.tally-enr.com and which reads no such config, tells
    one by this suffix alone (getProductionElectionIdFromId in its bundle,
    read 2026-10-08). So a state with no client_config is still guarded:
    a preview is never the count, whatever it is named or dated."""
    return str(eid).lower().endswith("_preview")


async def _refuse_preview(client: httpx.AsyncClient, state: str, source: dict, eid: str) -> bool:
    """The state's own front-end config names its demo mode and the
    election ids it serves as previews; a count from either is a test.
    Returns False when the config couldn't be fetched (the read is simply
    unavailable this pass, not refused).

    A preview is its own pseudo-id ("346_Preview"), listed beside the real
    election and left there after it goes live: North Dakota's config still
    lists 346_Preview while 346, its 2026 primary, is the default and
    official (verified 2026-09-28). So only the id itself being listed
    marks a preview — refusing `eid` because "`eid`_Preview" is listed
    would refuse the real count all night. A preview id that answers with
    another election's data is caught by _check_answer."""
    config_url = source.get("client_config")
    if not config_url:
        return True
    config = await fetch_json_with_retry(client, _rate_limiter, config_url, f"{state} results site config")
    if not isinstance(config, dict):
        return False
    if str(config.get("clientEnvDemo")).lower() == "true":
        raise UntrustedCount(f"{state} results site is in demo mode")
    previews = {str(p) for p in config.get("previewElections") or []}
    if eid in previews:
        raise UntrustedCount(f"{state} election {eid} is a preview")
    return True


def _check_answer(body: dict, eid: str, state: str) -> None:
    """Tally answers an unknown id with a 200 placeholder, and a preview id
    with a DIFFERENT election's data (348_Preview returned 346's, verified
    2026-09-28) — so every response must name the election asked for."""
    if str(body.get("electionID") or "") != str(eid) or parse_utc(body.get("lastUpdated")) is None:
        raise UntrustedCount(f"{state} answered for election {body.get('electionID')!r}, not {eid}")


async def fetch_general_results(
    client: httpx.AsyncClient, election_day, state: str, source: dict,
) -> StateCount | None:
    base_url, cid = source.get("base_url"), source.get("cid")
    if not base_url or not cid:
        return None
    day = election_day.isoformat()
    elections = await fetch_json_with_retry(
        client, _rate_limiter, f"{base_url}/Election/GetElectionList?cid={cid}", f"{state} election list",
    )
    if not isinstance(elections, list):
        return None
    election = pick_general([
        (e.get("electionName") or "", e) for e in elections
        if str(e.get("electionDate") or "").startswith(day) and e.get("electionID")
        and not _is_preview_id(e["electionID"])
    ], state)
    if election is None:
        return None
    eid = str(election["electionID"])
    if not await _refuse_preview(client, state, source, eid):
        return None
    search = await fetch_json_with_retry(
        client, _rate_limiter, f"{base_url}/Contest/GetContestSearchList?cid={cid}&electionID={eid}",
        f"{state} contest names",
    )
    info = await fetch_json_with_retry(
        client, _rate_limiter, f"{base_url}/Election/GetElectionInfo?cid={cid}&electionID={eid}",
        f"{state} election info",
    )
    if not isinstance(search, dict) or not isinstance(info, dict):
        return None
    # Scope the results request to the federal contests' own type code when
    # they share one (Arkansas's general files them "FED"; its primary said
    # "Federal"): the unscoped call is every city and county contest too.
    types = {
        c.get("contestTypeCode")
        for c in (((search.get("response") or {}).get("contests")) or {}).values()
        if parse_office(c.get("contestName") or "") is not None
    }
    url = f"{base_url}/Contest/GetContestResults?cId={cid}&electionID={eid}"
    if len(types) == 1 and next(iter(types)):
        url += f"&contestType={next(iter(types))}"
    results = await fetch_json_with_retry(client, _rate_limiter, url, f"{state} results")
    if not isinstance(results, dict):
        return None
    for body in (search, info, results):
        _check_answer(body, eid, state)
    # The three calls are one read only if they describe one publication.
    # The state republishes every few minutes on election night, and a
    # republish landing between them pairs one version's contest and
    # choice ids with another's counts -- a choice id that moved would put
    # one candidate's votes on another's name. Refused whole, like any
    # other answer that is not the count asked for; the next pass reads a
    # single version again.
    versions = {str(body.get("versionID") or "") for body in (search, info, results)}
    if len(versions) != 1:
        raise UntrustedCount(f"{state} election {eid} changed version mid-read ({sorted(versions)})")
    parties = ((info.get("response") or {}).get("parties")) or {}
    return StateCount(
        source_name=source.get("source_name") or f"{state} election results",
        page_url=source.get("results_page"),
        official=bool(results.get("isOfficial")),
        contests=general_contests(search, results, parties),
        source_updated=parse_utc(results.get("lastUpdated")),
        source_version=str(results.get("versionID") or "") or None,
    )
