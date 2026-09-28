"""Fetch modules for the FEC (Federal Election Commission) API."""

from datetime import date, timedelta
import logging
import re
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.congress_legislators import (
    fetch_bioguide_to_fec_ids,
    select_all_fec_ids_for_office,
)
from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S, fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

FEC_API_BASE = "https://api.open.fec.gov/v1"
MAX_RETRIES = 3
RETRY_BACKOFF_S = 2.0

_rate_limiter = RateLimiter(settings.FEC_RPS)

# Set to True once we detect the by_contributor endpoint is broken for this run,
# so we skip all 3 URL variants for every remaining senator instead of retrying.
# Reset at the start of each pipeline run via reset_run_state() — otherwise a
# single transient outage would latch this on for the life of the (long-lived)
# server process and permanently skip the endpoint until restart.
_by_contributor_broken = False


def reset_run_state() -> None:
    """Clear per-run FEC circuit-breaker state. Call at pipeline start."""
    global _by_contributor_broken
    _by_contributor_broken = False


async def _fetch_with_retry(
    client: httpx.AsyncClient, url: str, retries: int = MAX_RETRIES
) -> dict | None:
    """Fetch an FEC API URL with rate limiting, retries, and API key injection.

    Thin wrapper over the shared http_utils.fetch_with_retry: FEC keeps its
    stricter 2x rate-limit backoff and treats 4xx as terminal (retry_on_4xx
    False), and passes the api-key-bearing URL via request_url so the key is
    never part of the logged `url`.
    """
    separator = "&" if "?" in url else "?"
    full_url = f"{url}{separator}api_key={settings.DATA_GOV_API_KEY}"
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url,
        retries=retries,
        backoff_s=RETRY_BACKOFF_S,
        rate_limit_backoff_multiplier=2.0,  # FEC rate limits are tighter
        retry_on_4xx=False,
        timeout=DEFAULT_FETCH_TIMEOUT_S,
        log_label="FEC API",
        request_url=full_url,
    )
    return resp.json() if resp is not None else None


async def _candidate_latest_election(
    client: httpx.AsyncClient, db: Session, candidate_id: str,
) -> int | None:
    """The latest election year on an FEC candidate id's profile (0 when the
    profile lists none), or None when the id doesn't resolve at all.

    Checks the bare /candidate/{id}/ profile endpoint, not /totals/ — a
    real but financially inactive candidate can have zero totals rows,
    which would make /totals/ a false "doesn't exist". Only a resolved id is
    cached: a miss can't be told apart from _fetch_with_retry exhausting its
    retries on a transient FEC outage, and caching that would blacklist a
    valid id over a one-time network blip. The resolved result uses the
    normal cache TTL, not forever — an id's election years grow when the
    member files for a new cycle.
    """
    cache_key = f"candidate-profile-{candidate_id}"
    cached = api_cache_get(db, "fec", cache_key)
    if cached is not None:
        return int(cached.get("latest_election") or 0)

    data = await _fetch_with_retry(client, f"{FEC_API_BASE}/candidate/{candidate_id}/")
    results = (data or {}).get("results") or []
    if not results:
        return None
    profile = results[0]
    years = [int(y) for y in (profile.get("election_years") or profile.get("cycles") or []) if y]
    latest = max(years) if years else 0
    api_cache_set(db, "fec", cache_key, {"latest_election": latest})
    return latest


def _fec_first_name(c_name: str) -> str:
    """FEC's `name` field is formatted "LAST, FIRST MIDDLE ..." — returns
    just the first-name token, or "" if the field has no comma at all
    (unexpected format; safely matches nothing rather than guessing)."""
    _, sep, rest = c_name.partition(",")
    if not sep:
        return ""
    parts = rest.strip().split()
    return parts[0] if parts else ""


async def find_candidate(
    client: httpx.AsyncClient, db: Session, name: str, state: str,
    office: str = "S", district: str | None = None, bioguide_id: str | None = None,
) -> dict | None:
    """Search for a candidate in FEC data.

    Args:
        name: Candidate name
        state: Two-letter state code
        office: "S" for Senate, "H" for House
        district: Two-digit district code (House only)
        bioguide_id: When provided, checked FIRST against the
            congress-legislators bioguide->FEC crosswalk (see
            congress_legislators.py) — an authoritative ID match with no
            name-matching guesswork at all, immune to the next nickname
            or legal-name variant nobody's added to the fallback table
            below yet. A crosswalk entry is verified against a live FEC
            lookup before being trusted (2026-08-26 audit: three sitting
            members showed $0 raised because the crosswalk carried a
            second, stale/invalid id for the same chamber and the
            unverified first match happened to be the bad one) — if the
            first office-matching id doesn't resolve, the next one is
            tried, in crosswalk order. The name-based search only runs
            when nothing in the crosswalk verifies (no bioguide_id given,
            the member isn't in that crosswalk — e.g. a brand-new
            special-election winner not yet added upstream — or every
            crosswalk id for this office turns out invalid).

    Returns:
        Best matching candidate record, or None.
    """
    district_suffix = f"-{district}" if district else ""
    cache_key = f"candidate-search-{re.sub(r'\\s+', '_', name)}-{state}-{office}{district_suffix}"
    cached = api_cache_get(db, "fec", cache_key)
    if cached is not None:
        return cached

    if bioguide_id:
        crosswalk = await fetch_bioguide_to_fec_ids(client, db)
        fec_ids = crosswalk.get(bioguide_id)
        # A member can hold several valid ids for the same chamber — one per
        # campaign registration — and the crosswalk's order is not recency.
        # John McGuire (VA-5) has H2VA07196 for a 2022 VA-7 run he lost and
        # H0VA07133 for the 2024 win and his 2026 race (FEC bulk cn22/cn26,
        # checked 2026-09); taking the first id that resolves scored him on
        # the 2022 committee. Among ids that resolve, the one with the latest
        # election is the current campaign; ties keep crosswalk order.
        resolved: list[tuple[int, str]] = []
        for fec_id in select_all_fec_ids_for_office(fec_ids, office) if fec_ids else []:
            latest = await _candidate_latest_election(client, db, fec_id)
            if latest is None:
                logger.warning(
                    "Bioguide->FEC crosswalk id %s for %s (%s) does not resolve on FEC",
                    fec_id, bioguide_id, office,
                )
                continue
            resolved.append((latest, fec_id))
        if resolved:
            match = {"candidate_id": max(resolved, key=lambda r: r[0])[1]}
            api_cache_set(db, "fec", cache_key, match)
            return match

    name_parts = name.split()
    last_name = name_parts[-1] if name_parts else name

    base_query = f"{FEC_API_BASE}/candidates/search/?name={quote(last_name)}&state={state}&office={office}&per_page=20"
    query = base_query + (f"&district={district}" if district else "")

    data = await _fetch_with_retry(client, query)
    results = (data or {}).get("results") or []

    # FEC's `district` on a candidate record can lag a member's current
    # Congress.gov district after redistricting — the candidate ID keeps
    # whatever district they first filed under, and the searchable
    # `district` field doesn't always get updated for long-tenured
    # incumbents. A district-constrained search then finds nothing even
    # though the candidate exists (2026-07 audit: a sitting representative
    # searched under their current district came back empty, while the
    # same name+state+office query with no district filter found them
    # immediately — FEC had them on file under a different district
    # number). Retry without the district constraint; require a genuine
    # name match on this pass (no falling back to the first hit) since
    # nothing here disambiguates candidates the way district normally does.
    if not results and district:
        data = await _fetch_with_retry(client, base_query)
        fallback_results = (data or {}).get("results") or []
        results = [
            c for c in fallback_results
            if all(part.upper() in (c.get("name") or "").upper() for part in name_parts)
        ]
        if results:
            logger.info(
                "FEC candidate for %s (%s, %s) found without district filter "
                "(district=%s on file: %s) — likely a post-redistricting mismatch",
                name, state, office, district, results[0].get("district"),
            )

    if not results:
        logger.warning("No FEC candidate found for %s (%s, %s)", name, state, office)
        api_cache_set(db, "fec", cache_key, None)
        return None

    match = None
    for c in results:
        c_name = (c.get("name") or "").upper()
        if all(part.upper() in c_name for part in name_parts):
            match = c
            break

    if match is None:
        # 2026-07 fix: the strict all-parts check above requires every
        # token of our stored name — including middle initials with their
        # punctuation — to literally appear in FEC's name string, but FEC
        # files under the legal format: "James E. Risch" never matches
        # FEC's unpunctuated "RISCH, JAMES E". Audited live against the
        # FEC API: 28 of 100 sitting senators had zero donor/committee
        # data from this class of false negative, flooring their funding
        # scores at score_calculator's neutral-50 default — not
        # "genuinely unmeasurable," just never fetched.
        #
        # Fall back to last-name + EXACT first-name — still rejects a
        # same-surname different person (e.g. "Darline Graham" vs. FEC's
        # "GRAHAM, LINDSEY O"), which is the actual misattribution risk
        # this function guards against; the middle name is what stops
        # mattering. Deliberately NO nickname aliasing here ("Bill" ->
        # WILLIAM): nickname resolution is the bioguide crosswalk's job
        # (checked first, covers every sitting member — see
        # congress_legislators.py), and a hand-maintained alias table
        # would silently miss the next new nickname anyway. A member both
        # missing from the crosswalk AND FEC-filed under a different
        # first name than we store gets no FEC data until the crosswalk
        # picks them up — correct behavior, not a gap: no guessed
        # attribution is better than a plausible-but-unverified one.
        our_first, our_last = name_parts[0].upper(), name_parts[-1].upper()
        for c in results:
            c_name = (c.get("name") or "").upper()
            if our_last in c_name and our_first == _fec_first_name(c_name):
                match = c
                break

    if match is None:
        # Don't fall back to the top same-surname/state/office hit — that
        # silently attributes an unrelated candidate's committee (e.g. a
        # long-tenured incumbent) to whoever we searched for. No genuine
        # match means no FEC data, same as the no-results case above.
        logger.warning(
            "No genuine FEC name match for %s (%s, %s); top hit was %s",
            name, state, office, results[0].get("name"),
        )
        api_cache_set(db, "fec", cache_key, None)
        return None

    logger.debug(
        "FEC candidate match for %s: %s (%s)",
        name, match.get("name"), match.get("candidate_id"),
    )
    api_cache_set(db, "fec", cache_key, match)
    return match


CANDIDATES_PER_PAGE = 100


async def fetch_all_candidates(
    client: httpx.AsyncClient, db: Session, cycle: int, office: str,
) -> list[dict]:
    """Fetch every candidate actually on the ballot for `office` ("H" or
    "S") in `cycle`'s election.

    Unlike find_candidate (resolves ONE incumbent's own record by
    name+state+office), this pages through /v1/candidates/ in bulk — the
    roster source for the midterm-elections feature, where every declared
    challenger and primary candidate matters, not just sitting members.

    Filters on `election_year={cycle}`, NOT `cycle={cycle}`: FEC's `cycle`
    param matches any two-year period in a candidate's `cycles` array —
    every filing period their committee was active in — so for Senate it
    also returns sitting senators whose next race is 2/4 years away (their
    committees keep filing between races) and stale prior-cycle candidates
    whose committees are winding down. `election_year` matches the actual
    ballot year. This is the same cycle-vs-election-year distinction
    financials_election_year below documents for the totals endpoint;
    _sync_roster additionally re-validates each record's own
    candidate_election_year/election_years, so a wrong record can't mint a
    race for a state with no election that year (2026-07 review F1).

    At per_page=100 this is a modest number of requests at FEC's
    0.25 req/s rate limit, not a per-race lookup. Each page is cached
    independently so a re-run within the TTL window only re-fetches pages
    that changed.
    """
    all_candidates: list[dict] = []
    page = 1
    while True:
        cache_key = f"candidates-roster-ey{cycle}-{office}-page{page}"
        data = api_cache_get(db, "fec", cache_key)
        if data is None:
            url = (
                f"{FEC_API_BASE}/candidates/?election_year={cycle}&office={office}"
                f"&per_page={CANDIDATES_PER_PAGE}&page={page}"
            )
            data = await _fetch_with_retry(client, url)
            api_cache_set(db, "fec", cache_key, data)

        if not data:
            break
        results = data.get("results") or []
        all_candidates.extend(results)

        total_pages = (data.get("pagination") or {}).get("pages", page)
        if page >= total_pages or not results:
            break
        page += 1

    logger.info("Fetched %d %s candidates for cycle %d", len(all_candidates), office, cycle)
    return all_candidates


def financials_election_year(row: dict) -> int | None:
    """The confirmed election year a candidate totals row belongs to.

    Deliberately does NOT fall back to the row's raw `cycle` value when
    `candidate_election_year` is absent. `cycle` only identifies which
    2-year FILING PERIOD a row covers, not whether an election actually
    happened in it — a 6-year-term Senator's committee keeps filing (and
    often keeps receiving small residual contributions) in cycles years
    away from their next race. Falling back to `cycle` conflated "most
    recent filing period" with "most recent election": a dormant
    off-cycle row with near-zero receipts and no real
    `candidate_election_year` would outrank the actual election that
    raised millions, purely because its raw cycle number was numerically
    larger (2026-07 audit — a senator re-elected in 2024 with $5.8M
    raised showed totalRaised near $50K, sourced from an off-cycle
    filing-period row masquerading as "the most recent election").
    """
    return row.get("candidate_election_year")


def _is_confirmed_past_or_current_election(row: dict, current_year: int) -> bool:
    """True if a row's election year has actually occurred (or is in progress).

    A `candidate_election_year` in the future (relative to today) cannot
    be "the most recent election" — no election has been held there yet.
    This guards against the same class of bug as the `cycle` fallback
    removal above, in case FEC ever populates `candidate_election_year`
    itself with a forward-looking "next scheduled election" value for a
    currently-serving, not-yet-up-for-reelection member.
    """
    year = financials_election_year(row)
    return year is not None and year <= current_year


def _sort_financials_recent_first(results: list[dict]) -> list[dict]:
    """Order candidate totals rows most-recent-first.

    The API's `sort=-cycle` is a no-op for election-full rows (they return
    `cycle: null`), so row order is not guaranteed — the 2026-07 audit
    found one senator whose `[:2]` window was his 1984 and 2014 races
    while his most recent (and largest) race was dropped. Sort explicitly
    by confirmed election year; rows with no confirmed (past/current)
    election year sort last rather than being treated as "most recent"
    (see financials_election_year / _is_confirmed_past_or_current_election).
    """
    current_year = utcnow().year
    return sorted(
        results,
        key=lambda c: (
            c.get("candidate_election_year")
            if _is_confirmed_past_or_current_election(c, current_year)
            else -1
        ),
        reverse=True,
    )


def general_election_day(year: int) -> date:
    """The federal general election date for `year`: the Tuesday after the
    first Monday in November (2 U.S.C. §7) — a statutory fact, not a
    calibration."""
    nov1 = date(year, 11, 1)
    first_monday = nov1 + timedelta(days=(0 - nov1.weekday()) % 7)
    return first_monday + timedelta(days=1)


def _is_completed_election(row: dict, today: date) -> bool:
    """True once the row's general election has actually been held."""
    year = financials_election_year(row)
    if year is None:
        return False
    return year < today.year or (year == today.year and today > general_election_day(year))


# Years a member of each chamber serves per election won. Used to reject a
# completed election too old to be the one that seated them.
_TERM_YEARS = {"S": 6, "H": 2}


def seat_winning_floor(office: str | None, today: date) -> int | None:
    """Earliest election year that could have won the seat held right now.

    A House member serving today was elected at most one 2-year term ago;
    a senator at most one 6-year term ago. An older completed election is
    a DIFFERENT campaign — usually one they lost before winning the seat
    they now hold.
    """
    term = _TERM_YEARS.get(office or "")
    if term is None:
        return None
    newest_cycle = today.year if today.year % 2 == 0 else today.year + 1
    return newest_cycle - term


def select_recent_elections(
    financials: list[dict], n: int = 1, office: str | None = None,
) -> list[dict]:
    """One totals row per election, most recent ``n`` COMPLETED elections first.

    Funding dimensions are windowed to the candidate's most recent election
    (their current mandate's campaign) rather than "current congress only"
    like the vote/bill dimensions — Senators legitimately raise little money
    in the non-election years of a 6-year term, so a 2-year funding window
    would go near-empty for reasons unrelated to coasting. See AGENTS.md
    "current term" for the full rationale.

    /candidate/{id}/totals returns overlapping rows for the same election:
    an election-full aggregate (cycle: null) plus per-two-year-cycle rows,
    and sometimes exact duplicates. Taking ``financials[:2]`` as "the two
    most recent elections" therefore summed the same money twice AND
    dropped the previous race for 184 of 521 cached candidates (2026-07
    audit — e.g. one senator's window was her $1.2M 2030 partial counted
    twice while her $52M 2024 race fell out entirely). Keep the
    largest-receipts row per election year: the election-full aggregate
    supersedes its own partial cycle rows.

    Only elections that have been HELD count (general election day has
    passed — _is_completed_election). The campaign that won a member their
    current seat is the one they're serving under; a re-election campaign
    still in progress is their NEXT mandate's, and scoring it mixed
    members on complete races with members on half-finished ones —
    through most of an election year every House member and a third of the
    Senate were scored on an in-progress cycle whose money arrives on a
    different schedule (late small-dollar surges) from a finished one.
    A candidate with no completed election yet (an appointed senator
    before their first race) falls back to the in-progress one — it's the
    only campaign they have. See financials_election_year for why an
    off-cycle dormant row must not outrank a real election.

    `office` ("S"/"H") bounds how far back a completed election may be and
    still be the one that seated them. Without it, "most recent completed"
    silently reaches back to an OLD LOSING RUN: measured against live FEC
    data for 25 current House members, Clay Fuller (GA-14, seated by a
    2026 special, years_in_office=0) has rows for 2026 ($1.8M, in
    progress) and 2020 ($0.4M) — and would have been scored on the 2020
    campaign, which did not win him anything. Omitting `office` keeps the
    unbounded behaviour, so a caller that cannot say which chamber never
    loses data over this.
    """
    today = utcnow().date()
    completed: dict[int, dict] = {}
    in_progress: dict[int, dict] = {}
    for row in financials:
        year = financials_election_year(row)
        if year is None or year > today.year:
            continue
        bucket = completed if _is_completed_election(row, today) else in_progress
        best = bucket.get(year)
        if best is None or (row.get("receipts") or 0) > (best.get("receipts") or 0):
            bucket[year] = row
    floor = seat_winning_floor(office, today)
    if floor is not None:
        completed = {y: r for y, r in completed.items() if y >= floor}
    by_year = completed or in_progress
    if not by_year:
        # No row carries a confirmed election year (not seen in real FEC
        # data) — fall back to the caller's ordering rather than dropping
        # everything.
        return financials[:n]
    return [by_year[y] for y in sorted(by_year, reverse=True)[:n]]


# Two-year filing periods in one full election period, by office. FEC's
# election-full totals (what select_recent_elections picks) cover the whole
# period — six years for the Senate, two for the House — so the itemized
# receipt detail compared against them must cover the same span.
_ELECTION_PERIOD_CYCLES = {"S": 3, "H": 1}


def election_period_cycles(election_year: int, office: str) -> list[int]:
    """The two-year transaction periods making up one election period for
    `office` ("S"/"H", FEC's own office codes), newest first."""
    n = _ELECTION_PERIOD_CYCLES.get(office, 1)
    return [election_year - 2 * i for i in range(n)]


def compute_recent_election_cycles(financials: list[dict], office: str) -> list[int]:
    """The receipt-query cycle window for a candidate's most recent election.

    Covers the election's full period so top-donor/industry-breakdown
    detail matches the totals it's compared against (a 2026-07 audit found
    detail drawn from the committee's whole career against windowed
    totals). The previous fixed two-cycle window was wrong both ways: it
    dropped the first two years of a six-year Senate period, and pulled the
    House member's PREVIOUS election into the detail. Shared by
    senate_pipeline.py ("S") and house_pipeline.py ("H"). Passes `office` on
    so the detail window is bounded exactly like the totals
    (normalize_finance) — otherwise an old losing run would supply the
    donor detail for a member whose totals come from the current campaign.
    """
    cycles: list[int] = []
    for c in select_recent_elections(financials, office=office):
        election_year = financials_election_year(c)
        if election_year:
            cycles.extend(election_period_cycles(int(election_year), office))
    return cycles


async def fetch_candidate_financials(
    client: httpx.AsyncClient, db: Session, candidate_id: str
) -> list[dict]:
    """Fetch candidate financial totals, most recent election period first."""
    cache_key = f"candidate-financials-{candidate_id}"
    cached = api_cache_get(db, "fec", cache_key)
    if cached is not None:
        # Sort cached entries too — entries cached before 2026-07 were
        # stored in whatever order the API returned.
        return _sort_financials_recent_first(cached)

    data = await _fetch_with_retry(
        client,
        f"{FEC_API_BASE}/candidate/{candidate_id}/totals/?sort=-cycle&per_page=4",
    )
    results = _sort_financials_recent_first((data or {}).get("results", []))
    api_cache_set(db, "fec", cache_key, results)
    return results


async def fetch_candidate_committees(
    client: httpx.AsyncClient, db: Session, candidate_id: str
) -> list[dict]:
    """Fetch the candidate's principal campaign committee."""
    cache_key = f"candidate-committees-{candidate_id}"
    cached = api_cache_get(db, "fec", cache_key)
    if cached is not None:
        return cached

    data = await _fetch_with_retry(
        client,
        f"{FEC_API_BASE}/candidate/{candidate_id}/committees/?designation=P&per_page=5",
    )
    results = (data or {}).get("results", [])
    api_cache_set(db, "fec", cache_key, results)
    return results


def _cycle_query(cycles: list[int] | None) -> str:
    """FEC's Schedule A cycle filter — repeat the param for OR semantics."""
    if not cycles:
        return ""
    return "".join(f"&two_year_transaction_period={c}" for c in sorted(set(cycles)))


def _cycle_tag(cycles: list[int] | None) -> str:
    return "-".join(str(c) for c in sorted(set(cycles))) if cycles else "all"


async def fetch_committee_receipts(
    client: httpx.AsyncClient, db: Session, committee_id: str,
    cycles: list[int] | None = None,
) -> list[dict]:
    """Fetch individual contribution receipts to a committee.

    Args:
        cycles: Election cycles to include (FEC two_year_transaction_period
            values). Should match the window used for the candidate's
            receipt totals (select_recent_elections) — otherwise top-donor
            and industry-breakdown detail is drawn from the committee's
            entire career while the totals it's compared against are
            windowed to 2 recent elections (2026-07 audit finding).
            Omit to fetch unwindowed (career-lifetime) data.
    """
    cache_key = f"committee-receipts-indiv-v2-{committee_id}-{_cycle_tag(cycles)}"
    cached = api_cache_get(db, "fec", cache_key)
    if cached is not None:
        return cached

    # Get individual contributions only (for employer grouping)
    data = await _fetch_with_retry(
        client,
        f"{FEC_API_BASE}/schedules/schedule_a/?committee_id={committee_id}"
        f"&sort=-contribution_receipt_amount&per_page=100&is_individual=true"
        f"{_cycle_query(cycles)}",
    )
    results = (data or {}).get("results", [])
    api_cache_set(db, "fec", cache_key, results)
    return results


async def fetch_pac_receipts(
    client: httpx.AsyncClient, db: Session, committee_id: str,
    cycles: list[int] | None = None,
) -> list[dict]:
    """Fetch PAC/committee contributions to a candidate's campaign committee.

    These are contributions from PACs, party committees, and other committees
    directly to the senator's campaign -- the core corporate money flow.
    See fetch_committee_receipts for why `cycles` should match the window
    used for receipt totals.
    """
    cache_key = f"committee-receipts-pac-v2-{committee_id}-{_cycle_tag(cycles)}"
    cached = api_cache_get(db, "fec", cache_key)
    if cached is not None:
        return cached

    # is_individual=false returns committee-to-committee contributions (PACs)
    data = await _fetch_with_retry(
        client,
        f"{FEC_API_BASE}/schedules/schedule_a/?committee_id={committee_id}"
        f"&sort=-contribution_receipt_amount&per_page=100&is_individual=false"
        f"{_cycle_query(cycles)}",
    )
    results = (data or {}).get("results", [])
    api_cache_set(db, "fec", cache_key, results)
    return results


# TTL for cached committee-type lookups. A PAC's multicandidate status
# (committee_type "Q" vs "N") is effectively permanent — it's a qualification
# earned once (6+ months registered, 50+ contributors, contributed to 5+
# candidates) and essentially never reverts. Cached far longer than the
# default PIPELINE_CACHE_TTL_HOURS (72h) since this is looked up once per
# unique contributing PAC across ALL senators/reps, not per-candidate, and
# re-fetching it every pipeline run would be pure waste.
COMMITTEE_TYPE_CACHE_TTL_HOURS = 24 * 90


async def fetch_committee_meta(
    client: httpx.AsyncClient, db: Session, committee_id: str,
) -> dict | None:
    """One committee's FEC registration from the per-committee API, in the
    committee master's shape ({"type", "designation", "connectedOrg"}): the
    fallback for a committee the bulk master lacks, such as one registered
    since the last weekly file. committee_type "Q" = PAC-Qualified
    (multicandidate, $5,000/election cap), "N" = PAC-Nonqualified; the
    designation carries the leadership-PAC and candidate-committee codes
    (is_political_committee). The connected organization is left None: the
    API's affiliated-committee field is not the sponsor field, and the
    lobbying lookup would rather search the donor's own name than a wrong one.
    Returns None if the committee isn't found.
    """
    cache_key = f"committee-meta-v1-{committee_id}"
    cached = api_cache_get(db, "fec", cache_key, max_age_hours=COMMITTEE_TYPE_CACHE_TTL_HOURS)
    if cached is not None:
        return cached.get("meta")
    # The type-only entries this replaced are still warm (90-day TTL). One
    # answers the PAC-cap question without a request; the designation is
    # then unknown, so the leadership-PAC half of the political rule can't
    # fire for it until the entry ages out, but party and candidate
    # committees (by type) still do. Only reached when the bulk master
    # lacks the committee.
    legacy = api_cache_get(
        db, "fec", f"committee-type-v1-{committee_id}", max_age_hours=COMMITTEE_TYPE_CACHE_TTL_HOURS,
    )
    if legacy is not None and legacy.get("committee_type"):
        return {"type": legacy["committee_type"], "designation": None, "connectedOrg": None}

    data = await _fetch_with_retry(client, f"{FEC_API_BASE}/committee/{committee_id}/")
    results = (data or {}).get("results", [])
    meta = {
        "type": results[0].get("committee_type"),
        "designation": results[0].get("designation"),
        "connectedOrg": None,
    } if results else None
    if data is not None:
        # A failed fetch (None) is not cached: an outage mustn't mark a real
        # committee as unknown for 90 days.
        api_cache_set(db, "fec", cache_key, {"meta": meta},
                      normal_ttl_hours=COMMITTEE_TYPE_CACHE_TTL_HOURS)
    return meta


# ── Committee master file (bulk) ─────────────────────────────────

# The FEC's committee master file, one per two-year cycle: every registered
# committee's type, designation and connected organization. One ~2.5 MB
# download per cycle replaces thousands of per-committee API calls, which
# matters because two of its columns are needed for every contributing PAC
# and the API's hourly key limit is 1,000 calls.
COMMITTEE_MASTER_URL = "https://www.fec.gov/files/bulk-downloads/{year}/cm{yy}.zip"
# The file is regenerated weekly and a committee's registration changes
# rarely (a new committee appears, a treasurer changes), so a week is fine.
COMMITTEE_MASTER_CACHE_TTL_HOURS = 24 * 7

# Column positions in cm.txt, per the FEC's published data dictionary
# ("Committee master file description"): pipe-delimited, no header row.
_CM_ID, _CM_NAME, _CM_DESIGNATION, _CM_TYPE, _CM_ORG_TYPE, _CM_CONNECTED_ORG = 0, 1, 8, 9, 12, 13


# FEC committee types filed by an organization in its own name rather than
# by a political committee: C communication cost, E electioneering
# communication, I independent expenditure filer (FEC committee type codes).
_ORGANIZATION_FILER_TYPES = frozenset({"C", "E", "I"})


def _exact_name_key(name: str) -> str:
    """A name compared ignoring case and punctuation only. An apostrophe is
    dropped, not a word break: "AMERICA'S" is spelled "AMERICAS" too."""
    unquoted = re.sub(r"['\u2019]", "", (name or "").upper())
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", unquoted).split())


_ALIAS_RE = re.compile(r"\([^()]*\)")


def _committee_name_key(name: str) -> str:
    """A committee name compared ignoring case, punctuation and parenthesised
    aliases: registrations cite "AMERICAN BANKERS ASSOCIATION PAC" and
    "AMERICAN BANKERS ASSOCIATION PAC (BANKPAC)" for one committee."""
    return _exact_name_key(_ALIAS_RE.sub(" ", name or ""))


def parse_committee_rows(text: str) -> dict[str, dict]:
    """cm.txt -> {committee_id: {"name", "type", "designation", "sponsor"}},
    the file as registered: "sponsor" is the connected organization as
    stated, for a separate segregated fund only (see
    resolve_connected_orgs). Empty fields become None; a malformed short
    line is skipped."""
    out: dict[str, dict] = {}
    for line in text.splitlines():
        cols = line.split("|")
        if len(cols) <= _CM_CONNECTED_ORG or not cols[_CM_ID]:
            continue
        org = cols[_CM_CONNECTED_ORG].strip()
        sponsored = bool(cols[_CM_ORG_TYPE].strip()) and org.upper() not in ("", "NONE")
        out[cols[_CM_ID]] = {
            "name": cols[_CM_NAME].strip(),
            "type": cols[_CM_TYPE] or None,
            "designation": cols[_CM_DESIGNATION] or None,
            "sponsor": org if sponsored else None,
        }
    return out


def resolve_connected_orgs(
    rows: dict[str, dict], names: dict[str, set[str]] | None = None,
) -> dict[str, dict]:
    """{committee_id: {"type", "designation", "connectedOrg"}} from
    parse_committee_rows output. `names` adds every earlier name a committee
    has registered under, since a sponsor can cite a PAC by an old one.

    The connected organization is a PAC's sponsor only for a separate
    segregated fund, which is exactly the committee the FEC gives an
    interest-group category (ORG_TP: corporation, labor, membership, trade,
    cooperative, corporation without stock). Elsewhere the same column
    holds joint-fundraising partners ("TAKE BACK THE HOUSE 2022", "TRUMP
    VICTORY") or the form's "NONE" placeholder (28,595 of the 2020-2026
    files' rows), neither of which is a lobbying client. A fund that names
    itself as its own connected organization (157 of cm26's 2,067 sponsored
    committees) names no sponsor either.

    A sponsor can be named by a name that is also a committee's. When that
    committee files for an organization itself (_ORGANIZATION_FILER_TYPES:
    the NEA's, the AFL-CIO's, the ABA's own registrations), it is the
    sponsor. When it is another PAC (a state bankers' PAC naming the
    American Bankers Association's), that PAC's sponsor is followed. Chains
    can loop (MINEPAC <-> COALPAC) or end at a committee with no sponsor,
    which leaves none. Measured over the 2020-2026 files, 41 of cm26's
    sponsors resolve to an organization this way and 18 to none (a PAC
    naming itself under another alias among them).
    """
    all_names = {cid: {row["name"]} | (names or {}).get(cid, set()) for cid, row in rows.items()}
    by_name: dict[str, list[str]] = {}
    for cid, own in all_names.items():
        for key in {_committee_name_key(n) for n in own}:
            by_name.setdefault(key, []).append(cid)

    def resolve(cid: str) -> str | None:
        org = rows[cid]["sponsor"]
        # Its current name only: a PAC once registered under its sponsor's
        # name ("PRINTING UNITED ALLIANCE") still names that sponsor.
        if org is None or _exact_name_key(org) == _exact_name_key(rows[cid]["name"]):
            return None  # names itself
        seen = {cid}
        while True:
            matches = by_name.get(_committee_name_key(org), [])
            named = [m for m in matches if m not in seen]
            if len(seen) > 1 and len(named) < len(matches):
                return None  # back to a committee already followed: a loop
            if not named:
                # Only the committee itself matches, by its name without an
                # alias: "X" for "X (XPAC)" is the organization, while
                # "X PAC (XX-PAC)" for "X PAC (X-PAC)" is itself again.
                return None if matches and _ALIAS_RE.search(org) else org
            if any(rows[m]["type"] in _ORGANIZATION_FILER_TYPES for m in named):
                return org
            seen.add(named[0])
            org = rows[named[0]]["sponsor"]
            if org is None:
                return None

    return {
        cid: {"type": row["type"], "designation": row["designation"], "connectedOrg": resolve(cid)}
        for cid, row in rows.items()
    }


def parse_committee_master(text: str) -> dict[str, dict]:
    """One cycle's cm.txt -> {committee_id: {"type", "designation",
    "connectedOrg"}}, resolved within that file (resolve_connected_orgs)."""
    return resolve_connected_orgs(parse_committee_rows(text))


async def fetch_committee_master(
    client: httpx.AsyncClient, db: Session, cycles: list[int],
) -> dict[str, dict]:
    """Committee type, designation and connected organization for every
    committee registered in any of `cycles` (even years). Later cycles win
    for a committee in several, since a registration can be amended, and
    sponsors are resolved once over all of them, so a sponsor citing a PAC
    by a name it has since changed still resolves.

    Best-effort per cycle: a failed download leaves that cycle out (logged)
    rather than failing the run, and callers fall back to the per-committee
    API for anything missing. A failure is not cached.
    """
    import io
    import zipfile

    merged: dict[str, dict] = {}
    names: dict[str, set[str]] = {}
    for cycle in sorted(set(cycles)):
        # The file as registered is cached, not the resolution: bump the
        # version whenever parse_committee_rows' output changes.
        cache_key = f"committee-master-rows-v1-{cycle}"
        cached = api_cache_get(
            db, "fec", cache_key, max_age_hours=COMMITTEE_MASTER_CACHE_TTL_HOURS,
        )
        if cached is None:
            url = COMMITTEE_MASTER_URL.format(year=cycle, yy=f"{cycle % 100:02d}")
            try:
                # fec.gov answers bulk downloads with a redirect to storage.
                resp = await client.get(url, timeout=DEFAULT_FETCH_TIMEOUT_S * 4, follow_redirects=True)
                resp.raise_for_status()
                with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                    name = next(n for n in zf.namelist() if n.lower().endswith(".txt"))
                    text = zf.read(name).decode("latin-1")
            except Exception as exc:
                logger.warning("FEC committee master %d unavailable: %s", cycle, exc)
                continue
            cached = parse_committee_rows(text)
            api_cache_set(
                db, "fec", cache_key, cached,
                normal_ttl_hours=COMMITTEE_MASTER_CACHE_TTL_HOURS,
            )
        for cid, row in cached.items():
            names.setdefault(cid, set()).add(row["name"])
        merged.update(cached)
    return resolve_connected_orgs(merged, names)


# Committee types and designations the FEC itself defines as political
# rather than as an organization's fund ("Committee type codes" and
# "Committee designation codes" in the FEC data dictionary). This is a
# documented data-format convention, tier 1 of the classification strategy
# (AGENTS.md): no name is read to decide it.
#   types: H/S/P candidate committees, X/Y/Z party committees
#   designations: A authorized by a candidate, P principal campaign
#   committee, J joint fundraiser, D leadership PAC
POLITICAL_COMMITTEE_TYPES = frozenset({"H", "S", "P", "X", "Y", "Z"})
POLITICAL_COMMITTEE_DESIGNATIONS = frozenset({"A", "P", "J", "D"})


def committee_master_cycles(today: date | None = None) -> list[int]:
    """The cycles whose committee files cover every funding window, plus
    the current cycle for committees registered since."""
    year = (today or utcnow().date()).year
    current = year + (year % 2)
    # A senator's most recent completed election can be six years back, and
    # its window spans the three cycles before it: 2026 back to 2016.
    return [current - 2 * k for k in range(2 * _ELECTION_PERIOD_CYCLES["S"])]


async def resolve_committee_meta(
    client: httpx.AsyncClient, db: Session, committee_ids: set[str], master: dict[str, dict],
) -> dict[str, dict]:
    """{committee_id: {"type", "designation", "connectedOrg"}} for the
    contributing PACs; a committee found nowhere is left out.

    The bulk master answers almost every committee; the per-committee API
    (fetch_committee_meta) is asked only for one the master lacks, such as a
    committee registered after the last weekly file, or every committee if
    the bulk files couldn't be downloaded. Either way the political-committee
    rule sees a type and designation.
    """
    metas: dict[str, dict] = {}
    for cid in committee_ids:
        meta = master.get(cid) or await fetch_committee_meta(client, db, cid)
        if meta:
            metas[cid] = meta
    return metas


# Schedule A entity types whose contributor is itself a committee: "COM"
# (committee), "PAC", "PTY" (party organization) and "CCM" (candidate
# committee). Only "COM" used to be looked up, and live Senate top-donor
# lists showed the cost: 36 of 2,136 PAC donors carried a committee type.
COMMITTEE_ENTITY_TYPES = frozenset({"COM", "PAC", "PTY", "CCM"})


def committee_id_of(receipt: dict) -> str | None:
    """The contributing committee's FEC id, when the row's contributor is a
    committee."""
    if receipt.get("entity_type") in COMMITTEE_ENTITY_TYPES and receipt.get("contributor_id"):
        return receipt["contributor_id"]
    return None


def is_political_committee(meta: dict | None) -> bool:
    """Whether the FEC's own registration says this committee is a party,
    candidate, joint-fundraising or leadership committee — money from it is
    political money, not an industry's."""
    if not meta:
        return False
    return (
        meta.get("type") in POLITICAL_COMMITTEE_TYPES
        or meta.get("designation") in POLITICAL_COMMITTEE_DESIGNATIONS
    )


async def fetch_aggregated_contributors(
    client: httpx.AsyncClient, db: Session, committee_id: str,
    cycles: list[int] | None = None,
) -> list[dict]:
    """Fetch aggregated totals by contributor for a committee.

    Uses best-effort fallbacks for FEC endpoints that don't support the
    preferred `-total` sort field (some committees return 422). The
    function will try a small set of alternative queries before giving up
    and returning an empty list — the pipeline will continue. See
    fetch_committee_receipts for why `cycles` should match the window
    used for receipt totals.
    """
    global _by_contributor_broken

    cache_key = f"aggregated-contributors-v2-{committee_id}-{_cycle_tag(cycles)}"
    cached = api_cache_get(db, "fec", cache_key)
    if cached is not None:
        return cached

    # If a previous senator already proved the endpoint is down, skip entirely.
    if _by_contributor_broken:
        logger.debug("Skipping by_contributor for %s (endpoint known broken)", committee_id)
        api_cache_set(db, "fec", cache_key, [])
        return []

    cq = _cycle_query(cycles)
    # Try preferred query first, then fall back to alternatives when a
    # 422/other failures are encountered.
    urls = [
        f"{FEC_API_BASE}/schedules/schedule_a/by_contributor/?committee_id={committee_id}&sort=-total&per_page=20{cq}",
        f"{FEC_API_BASE}/schedules/schedule_a/by_contributor/?committee_id={committee_id}&sort=-contribution_receipt_amount&per_page=20{cq}",
        f"{FEC_API_BASE}/schedules/schedule_a/by_contributor/?committee_id={committee_id}&per_page=20{cq}",
    ]

    data = None
    for idx, url in enumerate(urls):
        data = await _fetch_with_retry(client, url)
        if data is not None:
            if idx > 0:
                logger.info("FEC fallback used for %s: %s", committee_id, url)
            break

    if data is None:
        logger.warning(
            "FEC aggregated contributors failed for %s — continuing with empty result",
            committee_id,
        )
        _by_contributor_broken = True

    results = (data or {}).get("results", [])
    api_cache_set(db, "fec", cache_key, results)
    return results
