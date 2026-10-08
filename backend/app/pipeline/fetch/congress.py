"""Fetch modules for Congress.gov and Senate.gov APIs."""

import logging
import re
from collections.abc import Callable
from datetime import datetime

import httpx
from lxml import etree
from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.analyze.bill_stage import became_law_action
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S, fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

CONGRESS_API_BASE = "https://api.congress.gov/v3"


def congress_gov_bill_url(congress: int, url_type: str, number: str | int) -> str:
    """A bill's public congress.gov page. `url_type` is congress.gov's own
    ("house-bill", "senate-joint-resolution"...); the congress is written as
    an ordinal ("119th-congress"), and a wrong suffix 404s."""
    from app.ordinals import ordinal

    return f"https://www.congress.gov/bill/{ordinal(congress)}-congress/{url_type}/{number}"

_rate_limiter = RateLimiter(settings.CONGRESS_RPS)

# Roll-call-number probe search, shared by the Senate and House "recent
# roll calls" fetchers below: scattered candidates get a longer timeout
# (fewer, further-apart requests) than the sequential one-by-one search
# that follows once a valid upper bound is found.
_ROLL_CALL_PROBE_TIMEOUT_S = 15.0

# Bumped whenever parse_senate_vote_xml / parse_house_vote_xml start
# returning a new field, so the cached parsed roll calls (72h TTL, see
# cache.py) are re-fetched and re-parsed instead of serving dicts that lack
# it. v2 (2026-09): the chamber's own result ("result" / "rejected").
ROLL_CALL_PARSE_VERSION = 2
_ROLL_CALL_NARROW_SEARCH_TIMEOUT_S = 10.0
_ROLL_CALL_NARROW_SEARCH_WINDOW = 50


async def _find_highest_roll_call(
    client: httpx.AsyncClient,
    url_for_roll: Callable[[int], str],
    probe_candidates: list[int],
    root_tag: str,
) -> int | None:
    """Binary-ish search for the highest valid roll-call number on a
    legislative chamber's site: probe a scattered list of candidates to
    find any valid upper bound, then walk forward one at a time from
    there to find the true highest. `url_for_roll` builds the
    chamber-specific URL for a given roll number.

    A roll exists only when its body is the vote document (`root_tag`, the
    chamber's XML root element), not merely when the status is 200: the
    House Clerk answers a roll that doesn't exist yet with 200 and
    `<xml>Error sanitizing file ...</xml>`, which once walked the search
    past the real last roll, so every roll fetched was a non-vote and the
    House's recent votes were saved empty.

    None when no probe got an answer at all (every request raised or the
    server erred) — the site could not be read, which is not the same as
    a session with no votes yet (0).
    """
    marker = f"<{root_tag}"

    def _is_vote(resp: httpx.Response) -> bool:
        return resp.status_code == 200 and marker in resp.text

    highest_valid = 0
    answered = False
    for probe in probe_candidates:
        await _rate_limiter.acquire()
        try:
            resp = await client.get(url_for_roll(probe), timeout=_ROLL_CALL_PROBE_TIMEOUT_S)
        except Exception:
            continue
        if resp.status_code < 500:
            answered = True
        if _is_vote(resp):
            highest_valid = probe
            break  # Found a valid upper bound

    if highest_valid == 0:
        return 0 if answered else None

    check = highest_valid + 1
    while check <= highest_valid + _ROLL_CALL_NARROW_SEARCH_WINDOW:
        await _rate_limiter.acquire()
        try:
            resp = await client.get(url_for_roll(check), timeout=_ROLL_CALL_NARROW_SEARCH_TIMEOUT_S)
        except Exception:
            break
        if not _is_vote(resp):
            break
        highest_valid = check
        check += 1

    return highest_valid


def congress_first_year(congress: int) -> int:
    """First calendar year of the given Congress (the 1st Congress convened 1789)."""
    return 1789 + (congress - 1) * 2


def congress_for_year(year: int) -> int:
    """Inverse of congress_first_year: the Congress in session during `year`.

    A new Congress convenes on Jan 3 of each odd year, so within the ~2 days
    before that in an odd January this is off by one — immaterial for the
    staleness guard it backs (advisory ops alert), but do not use it as a
    scored input without accounting for the convening date.
    """
    return 1 + (year - 1789) // 2


def congress_of_date(date_str: str) -> int | None:
    """The Congress in session on a YYYY-MM-DD date. A new Congress convenes
    on January 3 of each odd year (20th Amendment), so Jan 1-2 of an odd
    year still belong to the previous one. None for an unparseable date."""
    from datetime import date

    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        return None
    congress = congress_for_year(d.year)
    if d.year % 2 == 1 and (d.month, d.day) < (1, 3):
        congress -= 1
    return congress


def expected_current_congress(now=None) -> int:
    """The Congress in office given the wall clock — noon ET on Jan 3 of an
    odd year starts the next one (app.time_utils.congress_in_session), not
    Jan 1 or midnight.

    Readers: ops_alerts.check_current_congress_staleness (whether a pinned
    CURRENT_CONGRESS has fallen behind), and three surfaces that want the
    Congress in office rather than the scored one — api/bills.py's bill
    record route (refusing a Congress that hasn't convened),
    api/action.py's related-bill links (the Congress a bill with none
    recorded is linked under) and congress_activity.py's Congress-record
    sync. Their switch moved from a calendar-year rule to
    noon ET on Jan 3 with the rest; that is benign: for the hours between
    midnight and noon on Jan 3 they treat the outgoing Congress as current,
    which it is. The scored windows read settings.CURRENT_CONGRESS instead
    (app.config.scoring_congress), so an archived-DB re-run stays
    reproducible when the operator pins it.
    """
    from app.time_utils import congress_in_session

    return congress_in_session(now)

async def fetch_significant_bills(
    client: httpx.AsyncClient,
    db: Session,
    congresses: list[int] | None = None,
    max_bills: int = 40,
) -> list[dict]:
    """Dynamically discover significant bills from Congress.gov.

    Searches for enacted legislation and bills with significant Senate activity
    from recent congresses. No hardcoded bill lists — all discovery is dynamic.

    Args:
        client: HTTP client.
        db: Database session for caching.
        congresses: Congress numbers to search (default: current congress only —
            "current term" is defined as the current congress; see AGENTS.md).
        max_bills: Maximum number of bills to return.

    Returns:
        List of bill reference dicts with keys: congress, type, number, name.
    """
    if congresses is None:
        congresses = [settings.CURRENT_CONGRESS]

    cache_key = f"significant-bills-{'_'.join(str(c) for c in congresses)}-{max_bills}"
    cached = api_cache_get(db, "congress", cache_key)
    if cached is not None:
        return cached

    logger.info("Discovering significant bills from congresses %s...", congresses)

    seen: set[str] = set()
    bills: list[dict] = []

    for congress in congresses:
        if len(bills) >= max_bills:
            break

        # Fetch enacted laws (most significant legislation)
        for bill_type in ("hr", "s"):
            if len(bills) >= max_bills:
                break

            data = await _fetch_with_retry(
                client,
                f"{CONGRESS_API_BASE}/bill/{congress}/{bill_type}"
                f"?sort=updateDate+desc&limit=250",
            )
            if not data or not data.get("bills"):
                continue

            for b in data["bills"]:
                if len(bills) >= max_bills:
                    break

                bill_number = b.get("number")
                title = b.get("title", "")
                latest_action = (b.get("latestAction") or {}).get("text", "")

                # Prioritize bills that became law or had Senate votes
                is_enacted = became_law_action(latest_action)
                has_senate_action = any(
                    kw in latest_action.lower()
                    for kw in ("passed senate", "senate agreed", "signed by president")
                )

                if not (is_enacted or has_senate_action):
                    continue

                bill_key = f"{congress}-{bill_type}-{bill_number}"
                if bill_key in seen:
                    continue
                seen.add(bill_key)

                # Clean up the title (often very long with "An Act to...")
                name = title
                if " - " in name:
                    # Short title is usually after the dash
                    name = name.split(" - ", 1)[1]
                if len(name) > 100:
                    name = name[:97] + "..."

                bills.append({
                    "congress": congress,
                    "type": bill_type,
                    "number": int(bill_number),
                    "name": name,
                })

    logger.info("Discovered %d significant bills", len(bills))
    api_cache_set(db, "congress", cache_key, bills)
    return bills


async def _fetch_with_retry(client: httpx.AsyncClient, url: str) -> dict | None:
    """Fetch a Congress.gov API URL with rate limiting, retries, and API key injection.

    The credential-bearing URL is built separately and passed via
    `request_url`, so `url` — the value fetch_with_retry logs on every
    request/retry/failure — never carries the API key in the first place
    (httpx's `params=` kwarg replaces rather than merges an existing query
    string, so it can't be used here without dropping this URL's other
    params; see fetch_with_retry's docstring).
    """
    full_url = str(
        httpx.URL(url).copy_merge_params(
            {"api_key": settings.DATA_GOV_API_KEY, "format": "json"}
        )
    )
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url,
        request_url=full_url, log_label="Congress API",
    )
    return resp.json() if resp is not None else None


async def fetch_senators(
    client: httpx.AsyncClient, db: Session
) -> list[dict]:
    """Fetch all current senators from Congress.gov."""
    cached = api_cache_get(db, "congress", "senators-list")
    if cached is not None:
        return cached

    logger.info("Fetching senators from Congress.gov...")
    all_members: list[dict] = []
    offset = 0
    limit = 250

    # The Congress.gov API ignores the chamber= query param on /member, so we
    # fetch all currentMembers and filter client-side.
    while True:
        data = await _fetch_with_retry(
            client,
            f"{CONGRESS_API_BASE}/member?currentMember=true&limit={limit}&offset={offset}",
        )
        if not data or not data.get("members"):
            break
        all_members.extend(data["members"])
        if len(data["members"]) < limit:
            break
        offset += limit

    # Filter to senators only using the terms embedded in the member listing
    members = []
    for m in all_members:
        terms_obj = m.get("terms") or {}
        terms_list = terms_obj.get("item", []) if isinstance(terms_obj, dict) else []
        if any(t.get("chamber") == "Senate" for t in terms_list):
            members.append(m)

    logger.info("Fetched %d senators (from %d total members)", len(members), len(all_members))
    api_cache_set(db, "congress", "senators-list", members)
    return members


async def fetch_representatives(
    client: httpx.AsyncClient, db: Session
) -> list[dict]:
    """Fetch all current House representatives from Congress.gov."""
    cached = api_cache_get(db, "congress", "representatives-list")
    if cached is not None:
        return cached

    logger.info("Fetching representatives from Congress.gov...")
    all_members: list[dict] = []
    offset = 0
    limit = 250

    while True:
        data = await _fetch_with_retry(
            client,
            f"{CONGRESS_API_BASE}/member?currentMember=true&limit={limit}&offset={offset}",
        )
        if not data or not data.get("members"):
            break
        all_members.extend(data["members"])
        if len(data["members"]) < limit:
            break
        offset += limit

    members = []
    for m in all_members:
        terms_obj = m.get("terms") or {}
        terms_list = terms_obj.get("item", []) if isinstance(terms_obj, dict) else []
        if not terms_list:
            continue
        most_recent = max(terms_list, key=lambda t: t.get("startYear", 0))
        if most_recent.get("chamber") == "House of Representatives":
            members.append(m)

    logger.info("Fetched %d representatives (from %d total members)", len(members), len(all_members))
    api_cache_set(db, "congress", "representatives-list", members)
    return members


async def fetch_member_detail(
    client: httpx.AsyncClient, db: Session, bioguide_id: str
) -> dict | None:
    """Fetch detailed member info including terms and sponsored legislation."""
    cache_key = f"member-detail-{bioguide_id}"
    cached = api_cache_get(db, "congress", cache_key)
    if cached is not None:
        return cached

    data = await _fetch_with_retry(
        client, f"{CONGRESS_API_BASE}/member/{bioguide_id}"
    )
    if data and data.get("member"):
        api_cache_set(db, "congress", cache_key, data["member"])
        return data["member"]
    return None


def _recent_congresses_only(bills: list[dict]) -> list[dict]:
    """Keep only bills from the current congress ("current term"; see AGENTS.md).

    Scoring (LE volume, promise derivation, key-vote selection) operates
    on the recent window; feeding career-length sponsorship lists into
    the analyze phase is what blew pipeline run 69 up to 24h (58,435
    bills for 100 senators, ~8h analyze, plus thousands of unbounded
    official-title fetches). The full fetched list stays in ApiCache
    verbatim; this bound applies at the point of use.
    """
    min_congress = settings.CURRENT_CONGRESS
    return [b for b in bills if (b.get("congress") or 0) >= min_congress]


async def fetch_member_sponsored(
    client: httpx.AsyncClient, db: Session, bioguide_id: str
) -> list[dict] | None:
    """Fetch a member's sponsored legislation with pagination.

    Returns only bills from the current congress; see _recent_congresses_only
    for why. Returns None when the request fails and no earlier response is
    cached: an empty list means the member sponsored nothing, and a failed
    request used to return one too — scoring the member as "0 substantive
    bills, confirmed inactivity" for that run.
    """
    cache_key = f"member-sponsored-v2-{bioguide_id}"
    cached = api_cache_get(db, "congress", cache_key)
    if cached is not None:
        return _recent_congresses_only(cached)

    all_results: list[dict] = []
    offset = 0
    page_size = 250
    max_pages = 4

    for _ in range(max_pages):
        data = await _fetch_with_retry(
            client,
            f"{CONGRESS_API_BASE}/member/{bioguide_id}"
            f"/sponsored-legislation?limit={page_size}&offset={offset}",
        )
        if data is None:
            # Past its TTL, the last good response is still a better answer
            # than none — sponsorship changes slowly.
            stale = api_cache_get(db, "congress", cache_key, max_age_hours=_STALE_SPONSORED_MAX_HOURS)
            if stale:
                logger.warning("Sponsored legislation for %s failed — using the cached list", bioguide_id)
                return _recent_congresses_only(stale)
            logger.warning("Sponsored legislation for %s failed and nothing is cached", bioguide_id)
            return None
        page = data.get("sponsoredLegislation", [])
        all_results.extend(page)
        if len(page) < page_size:
            break
        offset += page_size

    api_cache_set(db, "congress", cache_key, all_results)
    return _recent_congresses_only(all_results)


# How old a cached sponsored-legislation list may be and still stand in for a
# failed request: one congress.
_STALE_SPONSORED_MAX_HOURS = 24 * 365 * 2


async def fetch_bill(
    client: httpx.AsyncClient,
    db: Session,
    congress: int,
    bill_type: str,
    bill_number: int,
) -> dict | None:
    """Fetch bill details."""
    cache_key = f"bill-{congress}-{bill_type}-{bill_number}"
    cached = api_cache_get(db, "congress", cache_key)
    if cached is not None:
        return cached

    data = await _fetch_with_retry(
        client,
        f"{CONGRESS_API_BASE}/bill/{congress}/{bill_type}/{bill_number}",
    )
    if data and data.get("bill"):
        api_cache_set(db, "congress", cache_key, data["bill"])
        return data["bill"]
    return None


class CongressUnavailable(RuntimeError):
    """Congress.gov could not be read for something a score depends on (a
    bill's actions decide its stage and whether it became law). Raised, not
    cached as [], so the run keeps the stored record instead of saving a
    bill as never acted on."""


async def fetch_bill_actions(
    client: httpx.AsyncClient,
    db: Session,
    congress: int,
    bill_type: str,
    bill_number: int,
) -> list[dict]:
    """Fetch roll call votes for a specific bill."""
    cache_key = f"bill-actions-{congress}-{bill_type}-{bill_number}"
    cached = api_cache_get(db, "congress", cache_key)
    if cached is not None:
        return cached

    data = await _fetch_with_retry(
        client,
        f"{CONGRESS_API_BASE}/bill/{congress}/{bill_type}/{bill_number}/actions?limit=100",
    )
    if data is None:
        raise CongressUnavailable(f"actions of {bill_type.upper()}.{bill_number} ({congress})")
    raw = data.get("actions", [])
    # Congress.gov v3 may return {"count": N, "item": [...]} instead of a list
    results = raw.get("item", []) if isinstance(raw, dict) else (raw or [])
    api_cache_set(db, "congress", cache_key, results)
    return results


# A bill's cosponsor list changes slowly once it is a few weeks old, and
# both chambers now read every current-Congress sponsored bill's (about
# 19,000 in the 119th): at a week, a night refreshes about 2,700 of them,
# where the 72-hour default would refresh about 6,400.
COSPONSORS_CACHE_HOURS = 24 * 7


async def fetch_bill_cosponsors(
    client: httpx.AsyncClient,
    db: Session,
    congress: int,
    bill_type: str,
    bill_number: int,
) -> list[dict]:
    """Fetch cosponsors for a bill (includes bioguideId, party, state)."""
    cache_key = f"bill-cosponsors-{congress}-{bill_type}-{bill_number}"
    cached = api_cache_get(db, "congress", cache_key, max_age_hours=COSPONSORS_CACHE_HOURS)
    if cached is not None:
        return cached

    data = await _fetch_with_retry(
        client,
        f"{CONGRESS_API_BASE}/bill/{congress}/{bill_type}/{bill_number}/cosponsors?limit=250",
    )
    if data is None:
        return []  # not cached: this run goes without, the next asks again
    raw = data.get("cosponsors", [])
    results = raw.get("item", []) if isinstance(raw, dict) else (raw or [])
    api_cache_set(db, "congress", cache_key, results, normal_ttl_hours=COSPONSORS_CACHE_HOURS)
    return results


async def fetch_bill_summaries(
    client: httpx.AsyncClient,
    db: Session,
    congress: int,
    bill_type: str,
    bill_number: int,
) -> list[dict]:
    """Fetch bill summary text."""
    cache_key = f"bill-summaries-{congress}-{bill_type}-{bill_number}"
    cached = api_cache_get(db, "congress", cache_key)
    if cached is not None:
        return cached

    data = await _fetch_with_retry(
        client,
        f"{CONGRESS_API_BASE}/bill/{congress}/{bill_type}/{bill_number}/summaries",
    )
    if data is None:
        return []  # not cached: this run classifies from the title, the next asks again
    raw = data.get("summaries", [])
    results = raw.get("item", []) if isinstance(raw, dict) else (raw or [])
    api_cache_set(db, "congress", cache_key, results)
    return results


# Every measure type Congress.gov lists, in its URL spelling.
BILL_TYPES = ("hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres")
# A congress's bill list grows by a few hundred a week; a week-old copy only
# lacks the newest bills, which nobody has voted on yet.
CONGRESS_BILL_TITLES_CACHE_HOURS = 24 * 7


async def _list_bill_titles(
    client: httpx.AsyncClient, congress: int, bill_type: str,
) -> tuple[dict[str, str], int, int | None] | None:
    """One pass over a bill type's listing: ({site bill id: title}, how many
    distinct bills it listed, the listing's own count), or None when a page
    fails. A newly introduced bill can be listed before it has a title, so
    completeness is judged on bills listed, not bills titled."""
    found: dict[str, str] = {}
    listed: set[str] = set()
    expected: int | None = None
    offset = 0
    while True:
        data = await _fetch_with_retry(
            client,
            f"{CONGRESS_API_BASE}/bill/{congress}/{bill_type}?limit=250&offset={offset}",
        )
        if data is None:
            return None
        expected = (data.get("pagination") or {}).get("count", expected)
        page = data.get("bills") or []
        for b in page:
            if b.get("number"):
                listed.add(b["number"])
                if b.get("title"):
                    found[f"{bill_type.upper()}.{b['number']}"] = b["title"]
        if len(page) < 250:
            return found, len(listed), expected
        offset += 250


async def fetch_congress_bill_titles(
    client: httpx.AsyncClient, db: Session, congress: int,
) -> dict[str, str] | None:
    """{site bill id ("HR.1492"): its current title} for every bill and
    resolution of a congress — about 65 paged list requests for a whole
    congress, cached a week. The pool lobbied_bills_for compares a filing's
    wording against, so a named number is kept only when its own bill fits
    the wording at least as well as any other bill does.

    None when any type's listing fails or comes back short of its own
    count: a partial pool could lack exactly the sibling bill that should
    win a comparison, so it is never returned or cached.
    """
    cache_key = f"congress-bill-titles-v1-{congress}"
    cached = api_cache_get(db, "congress", cache_key, max_age_hours=CONGRESS_BILL_TITLES_CACHE_HOURS)
    if cached is not None:
        return cached

    titles: dict[str, str] = {}
    for bill_type in BILL_TYPES:
        # The listing is ordered by last update, so a bill acted on during
        # the crawl moves to a page already read and the one beside it is
        # skipped. The listing states its own total; a short crawl is
        # retried once, then the whole pool is refused.
        for _attempt in range(2):
            listed = await _list_bill_titles(client, congress, bill_type)
            if listed is None:
                return None
            found, count, expected = listed
            if expected is None or count >= expected:
                break
        else:
            logger.warning(
                "Congress %d %s listing came back short twice (%d of %s)",
                congress, bill_type, count, expected,
            )
            return None
        titles.update(found)
    api_cache_set(
        db, "congress", cache_key, titles, normal_ttl_hours=CONGRESS_BILL_TITLES_CACHE_HOURS,
    )
    return titles


async def fetch_bill_titles(
    client: httpx.AsyncClient,
    db: Session,
    congress: int,
    bill_type: str,
    bill_number: int,
) -> list[dict]:
    """Fetch all titles for a bill, including the official descriptive title.

    Congress.gov bills have multiple title types: display title (short name
    like 'Jaime's Law'), official title ('A bill to prevent the purchase of
    ammunition by prohibited purchasers'), and short titles as introduced/
    passed.  The official title (titleTypeCode 6) provides the most
    descriptive text for semantic classification.
    """
    return await fetch_bill_titles_or_none(client, db, congress, bill_type, bill_number) or []


async def fetch_bill_titles_or_none(
    client: httpx.AsyncClient,
    db: Session,
    congress: int,
    bill_type: str,
    bill_number: int,
) -> list[dict] | None:
    """fetch_bill_titles, telling a failed fetch (None, not cached) from a
    bill with no titles or no such bill (a 404 is a real answer: []).

    The distinction matters to the LDA bill matcher, which reads "the
    previous congress had no H.R. 82" as permission: a timeout must not.
    fetch_bill_titles used to cache a failure as [] for the cache lifetime.
    """
    cache_key = f"bill-titles-{congress}-{bill_type}-{bill_number}"
    cached = api_cache_get(db, "congress", cache_key)
    if cached is not None:
        return cached
    # A 404 is stored as a marker: api_cache_set treats an empty payload as
    # a likely failure and keeps it only a few hours, while the marker lasts
    # the normal cache lifetime before "no such bill" is checked again.
    if api_cache_get(db, "congress", f"{cache_key}-absent") is not None:
        return []

    url = f"{CONGRESS_API_BASE}/bill/{congress}/{bill_type}/{bill_number}/titles"
    full_url = str(
        httpx.URL(url).copy_merge_params({"api_key": settings.DATA_GOV_API_KEY, "format": "json"})
    )
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url,
        request_url=full_url, expected_statuses=(404,), log_label="Congress API",
    )
    if resp is None:
        return None
    if resp.status_code == 404:
        api_cache_set(db, "congress", f"{cache_key}-absent", {"absent": True})
        return []
    else:
        try:
            raw = resp.json().get("titles", [])
        except ValueError:
            return None
        results = raw.get("item", []) if isinstance(raw, dict) else (raw or [])
    api_cache_set(db, "congress", cache_key, results)
    return results


def extract_official_title(titles: list[dict]) -> str:
    """Extract the official descriptive title from fetch_bill_titles's result.

    The official title (titleTypeCode 6) is the full legislative description
    (e.g., 'A bill to prevent the purchase of ammunition by prohibited
    purchasers') as opposed to the short display title (e.g., 'Jaime's Law').
    This provides the embedding classifier with semantically rich text for
    bills that have uninformative short names. Falls back to a titleType
    string match if no row has the numeric code (seen in a handful of older
    bills). Shared by senate_pipeline.py and house_pipeline.py.
    """
    for t in titles:
        if t.get("titleTypeCode") in (6, "6"):
            return t.get("title", "")
    for t in titles:
        title_type = (t.get("titleType") or "").lower()
        if "official" in title_type:
            return t.get("title", "")
    return ""


async def fetch_roll_call_vote(
    client: httpx.AsyncClient,
    db: Session,
    congress: int,
    session_number: int,
    roll_call_number: int,
    max_age_hours: int | None = None,
) -> dict | None:
    """Fetch Senate roll call vote details from senate.gov XML feed.

    Congress.gov API doesn't have Senate roll call votes -- only senate.gov does.

    `max_age_hours` overrides the default cache TTL — the nightly scoring
    caller wants the long default (a completed vote never changes), but
    the near-real-time early-signal poller (early_signal.py) needs a much
    shorter one (e.g. 1h) to actually notice a vote within the hour it
    happened, not up to PIPELINE_CACHE_TTL_HOURS (72h) later. Passed
    through to api_cache_set as normal_ttl_hours too — per that
    function's own docstring, a mismatched pair silently defeats the
    short-TTL safety net for empty results.
    """
    cache_key = f"rollcall-senate-{congress}-{session_number}-{roll_call_number}-v{ROLL_CALL_PARSE_VERSION}"
    cached = api_cache_get(db, "congress", cache_key, max_age_hours=max_age_hours)
    if cached is not None:
        return cached

    await _rate_limiter.acquire()
    padded_roll = str(roll_call_number).zfill(5)
    url = (
        f"https://www.senate.gov/legislative/LIS/roll_call_votes/"
        f"vote{congress}{session_number}/"
        f"vote_{congress}_{session_number}_{padded_roll}.xml"
    )

    try:
        logger.debug("Senate.gov vote: %s", url)
        resp = await client.get(url, timeout=DEFAULT_FETCH_TIMEOUT_S)
        if resp.status_code != 200:
            logger.warning(
                "Senate roll call not found: %d-%d-%d (%d)",
                congress, session_number, roll_call_number, resp.status_code,
            )
            return None

        xml_text = resp.text
        result = parse_senate_vote_xml(
            xml_text, congress, session_number, roll_call_number
        )
        if result:
            api_cache_set(db, "congress", cache_key, result, normal_ttl_hours=max_age_hours)
        return result
    except Exception as e:
        logger.error(
            "Failed to fetch Senate roll call %d-%d-%d: %s",
            congress, session_number, roll_call_number, str(e),
        )
        return None


def _root_text(root, xpath: str) -> str:
    """Extract text from the first element matching an XPath."""
    els = root.xpath(xpath)
    if els and hasattr(els[0], "text"):
        return (els[0].text or "").strip()
    return ""


# The chambers' own result vocabulary, as printed in Senate.gov's
# <vote_result> and clerk.house.gov's <vote-result>. This is parsing a
# documented data format (principle 1's data-format exception), not a
# classification: each phrase states outright which side of the question
# prevailed. Matched as a suffix because the Senate prefixes the question
# ("Cloture on the Motion to Proceed Rejected", "Bill Passed"). The
# nay-prevailed phrases are checked first because several of them end in a
# yea-prevailed phrase ("Not Agreed to", "Veto Sustained" — where the
# question was whether to override). Anything else — a House Speaker
# election's winner, a quorum call, a phrase not seen before — is unknown.
_NAY_PREVAILED_RESULTS = (
    "not agreed to", "not sustained", "not well taken", "not guilty",
    "not invoked", "veto sustained", "rejected", "failed", "defeated",
)
_YEA_PREVAILED_RESULTS = (
    "agreed to", "passed", "confirmed", "adopted", "sustained",
    "well taken", "guilty", "invoked", "veto overridden",
)
# Checked before the nay list: each ends in a nay-prevailed suffix but
# records the question carrying. "Veto Not Sustained" (an override that
# succeeded) is not the Senate's usual wording ("Veto Overridden"), but a
# misread here would exempt a leader's vote on a motion that passed.
_YEA_PREVAILED_OVERRIDES = ("veto not sustained",)


def roll_call_rejected(result: str | None) -> bool | None:
    """Whether the question voted on was rejected (the Nay side prevailed),
    read from the chamber's own result field: True if rejected, False if it
    carried, None when the result is missing or not recognized.

    Deliberately never derived from the yea/nay counts: the threshold is
    not a simple majority of those voting (cloture needs three-fifths of
    senators duly chosen and sworn, a veto override two-thirds, a
    suspension two-thirds), so counts alone can't say what happened.
    """
    text = " ".join((result or "").split()).lower()
    if not text:
        return None
    if text.endswith(_YEA_PREVAILED_OVERRIDES):
        return False
    if text.endswith(_NAY_PREVAILED_RESULTS):
        return True
    if text.endswith(_YEA_PREVAILED_RESULTS):
        return False
    return None


def parse_senate_vote_xml(
    xml_text: str, congress: int, session: int, roll_number: int
) -> dict | None:
    """Parse Senate.gov roll call vote XML into a structured object.

    Uses lxml.etree XPath to extract each senator's vote,
    keyed by last_name + state for matching.  Also extracts bill/resolution
    metadata from the XML so we don't need extra API calls.
    """
    try:
        root = etree.fromstring(xml_text.encode("utf-8"))
    except etree.XMLSyntaxError:
        return None

    member_elements = root.xpath("//member")
    members: list[dict] = []

    for member_el in member_elements:
        def get_text(tag: str) -> str:
            el = member_el.find(tag)
            return (el.text or "").strip() if el is not None else ""

        members.append({
            "firstName": get_text("first_name"),
            "lastName": get_text("last_name"),
            "party": get_text("party"),
            "state": get_text("state"),
            "voteCast": get_text("vote_cast"),
            "lisId": get_text("lis_member_id"),
        })

    if not members:
        return None

    # Extract bill/vote metadata
    vote_title = _root_text(root, "//vote_title")
    vote_date = _root_text(root, "//vote_date")
    question = _root_text(root, "//vote_question_text") or _root_text(root, "//question")
    document_title = _root_text(root, "//document/document_title")
    document_name = _root_text(root, "//document/document_name")
    # <vote_result> is the bare outcome ("Cloture on the Motion to Proceed
    # Rejected"); <vote_result_text> repeats it with the tally and the
    # threshold appended ("... Rejected (49-45, 3/5 majority required)"),
    # which is the fallback when the bare field is absent.
    result_text = _root_text(root, "//vote_result_text")
    result = _root_text(root, "//vote_result") or re.sub(
        r"\s*\([^()]*\)\s*$", "", result_text,
    )

    return {
        "congress": congress,
        "session": session,
        "rollNumber": roll_number,
        "voteTitle": vote_title,
        "voteDate": vote_date,
        "question": question,
        "documentTitle": document_title or vote_title,
        "documentName": document_name,
        "result": result,
        "resultText": result_text,
        "majorityRequirement": _root_text(root, "//majority_requirement"),
        "rejected": roll_call_rejected(result),
        "members": members,
    }


async def fetch_recent_roll_calls(
    client: httpx.AsyncClient,
    db: Session,
    congress: int = 119,
    session_number: int = 1,
    count: int = 15,
    max_age_hours: int | None = None,
) -> list[dict] | None:
    """Fetch the last `count` Senate roll calls from the current session.

    Probes Senate.gov starting from a high roll number, working backward
    until we find valid votes, then fetches `count` of them.

    Returns list of parsed roll call dicts (newest first): [] when the
    session has no votes, None when Senate.gov could not be read (the
    caller must not take that for an empty record). Neither is cached.

    `max_age_hours` overrides the default cache TTL — see fetch_roll_call_
    vote's docstring for why the near-real-time early-signal poller needs
    a much shorter one than the nightly scoring caller. Forwarded to each
    underlying fetch_roll_call_vote call too.
    """
    cache_key = f"recent-rollcalls-{congress}-{session_number}-{count}-v{ROLL_CALL_PARSE_VERSION}"
    cached = api_cache_get(db, "congress", cache_key, max_age_hours=max_age_hours)
    if cached is not None:
        return cached

    logger.info(
        "Discovering recent Senate roll calls (congress=%d, session=%d)...",
        congress, session_number,
    )

    def _senate_roll_url(roll: int) -> str:
        padded = str(roll).zfill(5)
        return (
            f"https://www.senate.gov/legislative/LIS/roll_call_votes/"
            f"vote{congress}{session_number}/"
            f"vote_{congress}_{session_number}_{padded}.xml"
        )

    highest_valid = await _find_highest_roll_call(
        client, _senate_roll_url, [500, 300, 200, 150, 100, 75, 50, 25, 10],
        "roll_call_vote",
    )

    if highest_valid is None:
        logger.warning("Senate.gov unreachable for congress %d session %d", congress, session_number)
        return None
    if highest_valid == 0:
        logger.warning("No recent roll calls found for congress %d session %d", congress, session_number)
        return []

    logger.info("Highest roll call found: %d", highest_valid)

    # Fetch the last `count` roll calls
    results: list[dict] = []
    for roll in range(highest_valid, max(0, highest_valid - count - 5), -1):
        if len(results) >= count:
            break

        roll_data = await fetch_roll_call_vote(
            client, db, congress, session_number, roll, max_age_hours=max_age_hours
        )
        if roll_data:
            results.append(roll_data)

    logger.info("Fetched %d recent roll calls", len(results))
    if not results:
        return None  # rolls exist but none could be read
    api_cache_set(db, "congress", cache_key, results, normal_ttl_hours=max_age_hours)
    return results


async def fetch_house_roll_call_vote(
    client: httpx.AsyncClient,
    db: Session,
    year: int,
    roll_call_number: int,
    max_age_hours: int | None = None,
) -> dict | None:
    """Fetch House roll call vote from clerk.house.gov XML feed.

    `max_age_hours` overrides the default cache TTL — see
    fetch_roll_call_vote's (Senate) docstring for why the near-real-time
    early-signal poller needs a much shorter one than the nightly
    scoring caller.
    """
    cache_key = f"rollcall-house-{year}-{roll_call_number}-v{ROLL_CALL_PARSE_VERSION}"
    cached = api_cache_get(db, "congress", cache_key, max_age_hours=max_age_hours)
    if cached is not None:
        return cached

    await _rate_limiter.acquire()
    padded_roll = str(roll_call_number)
    url = f"https://clerk.house.gov/evs/{year}/roll{padded_roll}.xml"

    try:
        logger.debug("House Clerk vote: %s", url)
        resp = await client.get(url, timeout=DEFAULT_FETCH_TIMEOUT_S)
        if resp.status_code != 200:
            logger.warning(
                "House roll call not found: %d-%d (%d)",
                year, roll_call_number, resp.status_code,
            )
            return None

        result = parse_house_vote_xml(resp.text, year, roll_call_number)
        if result:
            api_cache_set(db, "congress", cache_key, result, normal_ttl_hours=max_age_hours)
        return result
    except Exception as e:
        logger.error(
            "Failed to fetch House roll call %d-%d: %s",
            year, roll_call_number, str(e),
        )
        return None


def parse_house_vote_xml(
    xml_text: str, year: int, roll_number: int
) -> dict | None:
    """Parse House Clerk roll call vote XML into a structured object."""
    try:
        root = etree.fromstring(xml_text.encode("utf-8"))
    except etree.XMLSyntaxError:
        return None

    vote_metadata = root.find("vote-metadata")
    if vote_metadata is None:
        return None

    def _meta_text(tag: str) -> str:
        el = vote_metadata.find(tag)
        return (el.text or "").strip() if el is not None else ""

    congress_str = _meta_text("congress")
    session_str = _meta_text("session")
    question = _meta_text("vote-question")
    legis_num = _meta_text("legis-num")
    vote_desc = _meta_text("vote-desc")
    vote_result = _meta_text("vote-result")
    # "2/3 YEA-AND-NAY" on a suspension; a plain "YEA-AND-NAY" or
    # "RECORDED VOTE" states no fraction and needs a simple majority.
    requirement = re.match(r"\s*(\d+/\d+)", _meta_text("vote-type"))
    # e.g. "22-Jul-2026" — confirmed live against a real vote XML. Was
    # never parsed at all before (voteDate hardcoded to ""), which early-
    # signal reporting needs a real date for (ActionIssue.date). Left as
    # "" (not raised) on a format this codebase hasn't seen, matching
    # every other parse-failure in this module: log and degrade, never
    # raise mid-pipeline over one malformed field.
    action_date_raw = _meta_text("action-date")
    vote_date = ""
    if action_date_raw:
        try:
            vote_date = datetime.strptime(action_date_raw, "%d-%b-%Y").strftime("%Y-%m-%d")
        except ValueError:
            logger.warning("Unrecognized House action-date format: %r", action_date_raw)

    vote_data = root.find("vote-data")
    if vote_data is None:
        return None

    members: list[dict] = []
    for rv in vote_data.iter("recorded-vote"):
        legislator = rv.find("legislator")
        vote_el = rv.find("vote")
        if legislator is None or vote_el is None:
            continue

        members.append({
            "bioguideId": legislator.get("name-id", ""),
            "lastName": legislator.get("sort-field", ""),
            "firstName": legislator.text or "",
            "party": legislator.get("party", ""),
            "state": legislator.get("state", ""),
            "voteCast": (vote_el.text or "").strip(),
        })

    if not members:
        return None

    return {
        "year": year,
        "congress": int(congress_str) if congress_str.isdigit() else 0,
        "session": int(session_str) if session_str.isdigit() else 0,
        "rollNumber": roll_number,
        "voteTitle": vote_desc or legis_num,
        "voteDate": vote_date,
        "question": question,
        "documentTitle": vote_desc or legis_num,
        "documentName": legis_num,
        "result": vote_result,
        "rejected": roll_call_rejected(vote_result),
        "majorityRequirement": requirement.group(1) if requirement else "1/2",
        "members": members,
        "chamber": "House",
    }


async def fetch_recent_house_roll_calls(
    client: httpx.AsyncClient,
    db: Session,
    year: int = 2025,
    count: int = 15,
    max_age_hours: int | None = None,
) -> list[dict] | None:
    """Fetch the last `count` House roll calls for a given year.

    Probes clerk.house.gov starting from a high roll number, working
    backward until valid votes are found. [] when the year has no votes,
    None when the Clerk could not be read — see fetch_recent_roll_calls.

    `max_age_hours` overrides the default cache TTL — see
    fetch_recent_roll_calls's (Senate) docstring for why the near-real-time
    early-signal poller needs a much shorter one than the nightly scoring
    caller. Forwarded to each underlying fetch_house_roll_call_vote call too.
    """
    cache_key = f"recent-house-rollcalls-{year}-{count}-v{ROLL_CALL_PARSE_VERSION}"
    cached = api_cache_get(db, "congress", cache_key, max_age_hours=max_age_hours)
    if cached is not None:
        return cached

    logger.info("Discovering recent House roll calls (year=%d)...", year)

    def _house_roll_url(roll: int) -> str:
        return f"https://clerk.house.gov/evs/{year}/roll{roll}.xml"

    highest_valid = await _find_highest_roll_call(
        client, _house_roll_url, [700, 500, 400, 300, 200, 100, 50, 25, 10],
        "rollcall-vote",
    )

    if highest_valid is None:
        logger.warning("clerk.house.gov unreachable for year %d", year)
        return None
    if highest_valid == 0:
        logger.warning("No recent House roll calls found for year %d", year)
        return []

    logger.info("Highest House roll call found: %d", highest_valid)

    results: list[dict] = []
    for roll in range(highest_valid, max(0, highest_valid - count - 5), -1):
        if len(results) >= count:
            break
        roll_data = await fetch_house_roll_call_vote(
            client, db, year, roll, max_age_hours=max_age_hours
        )
        if roll_data:
            results.append(roll_data)

    logger.info("Fetched %d recent House roll calls", len(results))
    if not results:
        return None  # rolls exist but none could be read
    api_cache_set(db, "congress", cache_key, results, normal_ttl_hours=max_age_hours)
    return results
