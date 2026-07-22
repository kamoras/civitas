"""Fetch modules for the FEC (Federal Election Commission) API."""

import logging
import re
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.congress_legislators import (
    fetch_bioguide_to_fec_ids,
    select_fec_id_for_office,
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


# Common English nicknames that don't share a derivable spelling with
# their legal first name (Bill/William, Jack/John, Al/Alexander) — FEC
# candidate records are filed under the legal name, so a strict
# character-level match on our stored (commonly-used) name never finds
# these. Scoped to nicknames actually seen among sitting members of
# Congress (2026-07 audit) rather than an exhaustive general-purpose
# list — add to it as new cases surface.
_NICKNAME_TO_LEGAL_FIRST_NAMES: dict[str, frozenset[str]] = {
    "AL": frozenset({"ALEXANDER", "ALBERT", "ALAN", "ALLEN", "ALFRED"}),
    "BILL": frozenset({"WILLIAM"}),
    "BILLY": frozenset({"WILLIAM"}),
    "BOB": frozenset({"ROBERT"}),
    "BOBBY": frozenset({"ROBERT"}),
    "CHUCK": frozenset({"CHARLES"}),
    "DAN": frozenset({"DANIEL"}),
    "DANNY": frozenset({"DANIEL"}),
    "DAVE": frozenset({"DAVID"}),
    "DICK": frozenset({"RICHARD"}),
    "DOUG": frozenset({"DOUGLAS"}),
    "ED": frozenset({"EDWARD", "EDWIN"}),
    "GREG": frozenset({"GREGORY"}),
    "JACK": frozenset({"JOHN"}),
    "JIM": frozenset({"JAMES"}),
    "JIMMY": frozenset({"JAMES"}),
    "JOE": frozenset({"JOSEPH"}),
    "KEN": frozenset({"KENNETH"}),
    "LARRY": frozenset({"LAWRENCE"}),
    "MATT": frozenset({"MATTHEW"}),
    "MIKE": frozenset({"MICHAEL"}),
    "NED": frozenset({"EDWARD"}),
    "PEGGY": frozenset({"MARGARET"}),
    "PETE": frozenset({"PETER"}),
    "RON": frozenset({"RONALD"}),
    "STEVE": frozenset({"STEVEN", "STEPHEN"}),
    "TED": frozenset({"THEODORE", "EDWARD"}),
    "TOM": frozenset({"THOMAS"}),
    "TOMMY": frozenset({"THOMAS"}),
}


def _fec_first_name(c_name: str) -> str:
    """FEC's `name` field is formatted "LAST, FIRST MIDDLE ..." — returns
    just the first-name token, or "" if the field has no comma at all
    (unexpected format; safely matches nothing rather than guessing)."""
    _, sep, rest = c_name.partition(",")
    if not sep:
        return ""
    parts = rest.strip().split()
    return parts[0] if parts else ""


def _first_names_plausibly_match(ours: str, fec_first: str) -> bool:
    """True if `ours` (the first name we searched with) and `fec_first`
    (FEC's filed first name) are the same real first name, allowing for a
    known nickname/legal-name pair — never a fuzzy/partial match, since a
    false positive here is exactly the misattribution risk find_candidate
    guards against."""
    if ours == fec_first:
        return True
    return fec_first in _NICKNAME_TO_LEGAL_FIRST_NAMES.get(ours, frozenset())


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
            below yet. The name-based search only runs when this misses
            (no bioguide_id given, or the member isn't in that crosswalk
            — e.g. a brand-new special-election winner not yet added
            upstream).

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
        fec_id = select_fec_id_for_office(fec_ids, office) if fec_ids else None
        if fec_id:
            match = {"candidate_id": fec_id}
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
        # token of our stored name — including middle initials and the
        # exact first-name spelling — to literally appear in FEC's name
        # string, but FEC files under the legal name/format, which
        # routinely differs from what a member actually goes by: "Bill"
        # Cassidy is FEC's "CASSIDY, WILLIAM M."; "Chuck" Grassley is
        # "GRASSLEY, CHARLES E"; "James E. Risch" never matches FEC's
        # unpunctuated "RISCH, JAMES E". Audited live against the FEC API:
        # 28 of 100 sitting senators had zero donor/committee data purely
        # from this false negative, which then floors their funding-
        # independence/diversity scores at score_calculator's neutral-50
        # default — not "genuinely unmeasurable," just never fetched, and
        # that neutral default can outrank a peer's real (often lower)
        # funding-independence number.
        #
        # Fall back to just last-name + first-name (exact, or a known
        # nickname/legal-name pair) — still rejects a same-surname
        # different person (e.g. "Darline Graham" vs. FEC's "GRAHAM,
        # LINDSEY O" — different first names, no alias, no match) since
        # that's the actual misattribution risk this function guards
        # against, not the middle name or a nickname spelling.
        our_first, our_last = name_parts[0].upper(), name_parts[-1].upper()
        for c in results:
            c_name = (c.get("name") or "").upper()
            if our_last in c_name and _first_names_plausibly_match(our_first, _fec_first_name(c_name)):
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


def select_recent_elections(financials: list[dict], n: int = 1) -> list[dict]:
    """One totals row per election, most recent ``n`` elections first.

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

    Only rows with a confirmed past/current election year are eligible —
    see financials_election_year for why an off-cycle dormant row must
    not outrank the real most recent election.
    """
    current_year = utcnow().year
    by_year: dict[int, dict] = {}
    for row in financials:
        if not _is_confirmed_past_or_current_election(row, current_year):
            continue
        year = financials_election_year(row)
        best = by_year.get(year)
        if best is None or (row.get("receipts") or 0) > (best.get("receipts") or 0):
            by_year[year] = row
    if not by_year:
        # No row carries a confirmed election year (not seen in real FEC
        # data) — fall back to the caller's ordering rather than dropping
        # everything.
        return financials[:n]
    return [by_year[y] for y in sorted(by_year, reverse=True)[:n]]


def compute_recent_election_cycles(financials: list[dict]) -> list[int]:
    """The receipt-query cycle window for a candidate's most recent election.

    Includes the preceding 2-year cycle since both independent expenditures
    and a campaign's own receipts accrue across the full election period,
    not just the election year. Without this, top-donor/industry-breakdown
    detail was drawn from the committee's entire career while the totals it's
    compared against were windowed to the recent election (2026-07 audit
    finding). Shared by senate_pipeline.py and house_pipeline.py's funding
    fetch phases — both window committee-receipt queries to this same range.
    """
    cycles: list[int] = []
    for c in select_recent_elections(financials):
        election_year = financials_election_year(c)
        if election_year:
            cycles.extend([int(election_year), int(election_year) - 2])
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


async def fetch_committee_type(
    client: httpx.AsyncClient, db: Session, committee_id: str,
) -> str | None:
    """A PAC's FEC committee_type code, for computing its per-election
    contribution cap (see score_calculator._funding_independence_core):
    "Q" = PAC-Qualified (multicandidate, $5,000/election cap), "N" =
    PAC-Nonqualified (capped at the same per-election limit as an
    individual). Returns None if the committee isn't found or has no
    committee_type on record.
    """
    cache_key = f"committee-type-v1-{committee_id}"
    cached = api_cache_get(db, "fec", cache_key, max_age_hours=COMMITTEE_TYPE_CACHE_TTL_HOURS)
    if cached is not None:
        return cached.get("committee_type")

    data = await _fetch_with_retry(client, f"{FEC_API_BASE}/committee/{committee_id}/")
    results = (data or {}).get("results", [])
    committee_type = results[0].get("committee_type") if results else None
    api_cache_set(db, "fec", cache_key, {"committee_type": committee_type})
    return committee_type


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


async def fetch_outside_spending(
    client: httpx.AsyncClient, db: Session, candidate_id: str,
    cycles: list[int] | None = None,
) -> dict:
    """Fetch independent expenditures (super PAC outside spending) supporting a candidate.

    Returns totals for expenditures supporting the candidate (not opposing).
    These are not controlled by the candidate but signal industry alignment
    and are a key signal for senior legislators who rely on outside support.

    Uses the aggregate endpoint /schedules/schedule_e/totals/by_candidate/,
    which returns complete per-cycle totals in one call. The raw
    /schedules/schedule_e/ list is paginated and summing a single page
    truncates the total at the 50 largest expenditures.

    Args:
        cycles: Election cycles to include. When given, should match the
            cycles used for receipt totals so outside spending covers the
            same window. When omitted, the two most recent cycles with
            supporting expenditures are used.
    """
    cycle_tag = "-".join(str(c) for c in sorted(cycles)) if cycles else "recent"
    cache_key = f"outside-spending-v2-{candidate_id}-{cycle_tag}"
    cached = api_cache_get(db, "fec", cache_key)
    if cached is not None:
        return cached

    data = await _fetch_with_retry(
        client,
        f"{FEC_API_BASE}/schedules/schedule_e/totals/by_candidate/"
        f"?candidate_id={candidate_id}&per_page=100",
    )

    results = (data or {}).get("results", [])
    support = [r for r in results if r.get("support_oppose_indicator") == "S"]
    if cycles:
        wanted = {c for c in cycles if c}
        support = [r for r in support if r.get("cycle") in wanted]
    else:
        recent = sorted(
            {r.get("cycle") for r in support if r.get("cycle")}, reverse=True
        )[:2]
        support = [r for r in support if r.get("cycle") in set(recent)]

    total_for = sum(r.get("total", 0) or 0 for r in support)

    result = {"totalFor": round(total_for, 2), "count": len(support)}
    api_cache_set(db, "fec", cache_key, result)
    return result
