"""Fetch modules for the FEC (Federal Election Commission) API."""

import asyncio
import logging
import re
from datetime import datetime
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

FEC_API_BASE = "https://api.open.fec.gov/v1"
MAX_RETRIES = 3
RETRY_BACKOFF_S = 2.0

_rate_limiter = RateLimiter(settings.FEC_RPS)

# Set to True once we detect the by_contributor endpoint is broken for this run,
# so we skip all 3 URL variants for every remaining senator instead of retrying.
_by_contributor_broken = False


async def _fetch_with_retry(
    client: httpx.AsyncClient, url: str, retries: int = MAX_RETRIES
) -> dict | None:
    """Fetch an FEC API URL with rate limiting, retries, and API key injection."""
    await _rate_limiter.acquire()
    separator = "&" if "?" in url else "?"
    full_url = f"{url}{separator}api_key={settings.DATA_GOV_API_KEY}"

    for attempt in range(1, retries + 1):
        try:
            logger.debug("FEC API: %s (attempt %d)", url, attempt)
            resp = await client.get(full_url, timeout=30.0)

            if resp.status_code == 429:
                wait = RETRY_BACKOFF_S * attempt * 2  # FEC rate limits are tighter
                logger.warning("FEC rate limited, waiting %.1fs...", wait)
                await asyncio.sleep(wait)
                continue

            if resp.status_code >= 400:
                err = httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}: {resp.reason_phrase}",
                    request=resp.request,
                    response=resp,
                )
                # 4xx errors (except 429) are client errors — retrying won't help
                if 400 <= resp.status_code < 500:
                    logger.error("FEC API client error (no retry): %s — %s", url, err)
                    return None
                raise err

            return resp.json()
        except httpx.HTTPStatusError as e:
            if attempt == retries:
                logger.error(
                    "FEC API failed after %d attempts: %s — %s",
                    retries, url, str(e),
                )
                return None
            await asyncio.sleep(RETRY_BACKOFF_S * attempt)
        except Exception as e:
            if attempt == retries:
                logger.error(
                    "FEC API failed after %d attempts: %s — %s",
                    retries, url, str(e),
                )
                return None
            await asyncio.sleep(RETRY_BACKOFF_S * attempt)

    return None


async def find_candidate(
    client: httpx.AsyncClient, db: Session, name: str, state: str,
    office: str = "S", district: str | None = None,
) -> dict | None:
    """Search for a candidate in FEC data.

    Args:
        name: Candidate name
        state: Two-letter state code
        office: "S" for Senate, "H" for House
        district: Two-digit district code (House only)

    Returns:
        Best matching candidate record, or None.
    """
    district_suffix = f"-{district}" if district else ""
    cache_key = f"candidate-search-{re.sub(r'\\s+', '_', name)}-{state}-{office}{district_suffix}"
    cached = api_cache_get(db, "fec", cache_key)
    if cached is not None:
        return cached

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
        match = results[0]

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
    current_year = datetime.utcnow().year
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
    current_year = datetime.utcnow().year
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
