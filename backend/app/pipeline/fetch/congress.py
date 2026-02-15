"""Fetch modules for Congress.gov and Senate.gov APIs."""

import logging
from urllib.parse import quote, urlencode

import httpx
from lxml import etree
from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

CONGRESS_API_BASE = "https://api.congress.gov/v3"
MAX_RETRIES = 3
RETRY_BACKOFF_S = 2.0

_rate_limiter = RateLimiter(settings.CONGRESS_RPS)

# Key bills to analyze (curated list of significant legislation)
KEY_BILLS = [
    {"congress": 117, "type": "hr", "number": 5376, "name": "Inflation Reduction Act"},
    {"congress": 117, "type": "hr", "number": 3684, "name": "Infrastructure Investment and Jobs Act"},
    {"congress": 118, "type": "s", "number": 2281, "name": "National Defense Authorization Act FY2024"},
    {"congress": 117, "type": "hr", "number": 3, "name": "Elijah E. Cummings Lower Drug Costs Now Act"},
    {"congress": 118, "type": "hr", "number": 2670, "name": "National Defense Authorization Act FY2024"},
    {"congress": 117, "type": "s", "number": 2093, "name": "CHIPS and Science Act"},
    {"congress": 118, "type": "s", "number": 2073, "name": "Bipartisan Safer Communities Act"},
    {"congress": 117, "type": "hr", "number": 1319, "name": "American Rescue Plan Act"},
    {"congress": 118, "type": "hr", "number": 7024, "name": "Tax Relief for American Families and Workers Act"},
    {"congress": 117, "type": "s", "number": 3580, "name": "Consolidated Appropriations Act 2022"},
    {"congress": 118, "type": "s", "number": 3853, "name": "FAA Reauthorization Act"},
    {"congress": 117, "type": "hr", "number": 2471, "name": "Consolidated Appropriations Act 2022"},
    {"congress": 118, "type": "s", "number": 1, "name": "For the People Act"},
    {"congress": 117, "type": "s", "number": 1, "name": "For the People Act"},
    {"congress": 117, "type": "hr", "number": 1, "name": "For the People Act"},
    {"congress": 118, "type": "s", "number": 686, "name": "RESTRICT Act"},
    {"congress": 117, "type": "s", "number": 2938, "name": "Bipartisan Safer Communities Act"},
    {"congress": 118, "type": "hr", "number": 3746, "name": "Fiscal Responsibility Act"},
    {"congress": 119, "type": "s", "number": 5, "name": "Laken Riley Act"},
    {"congress": 118, "type": "hr", "number": 8580, "name": "Continuing Appropriations and Extensions Act 2025"},
]


async def _fetch_with_retry(
    client: httpx.AsyncClient, url: str, retries: int = MAX_RETRIES
) -> dict | None:
    """Fetch a Congress.gov API URL with rate limiting, retries, and API key injection."""
    await _rate_limiter.acquire()
    separator = "&" if "?" in url else "?"
    full_url = f"{url}{separator}api_key={settings.DATA_GOV_API_KEY}&format=json"

    for attempt in range(1, retries + 1):
        try:
            logger.debug("Congress API: %s (attempt %d)", url, attempt)
            resp = await client.get(full_url, timeout=30.0)

            if resp.status_code == 429:
                wait = RETRY_BACKOFF_S * attempt
                logger.warning("Rate limited, waiting %.1fs...", wait)
                import asyncio
                await asyncio.sleep(wait)
                continue

            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}: {resp.reason_phrase}",
                    request=resp.request,
                    response=resp,
                )

            return resp.json()
        except Exception as e:
            if attempt == retries:
                logger.error(
                    "Congress API failed after %d attempts: %s — %s",
                    retries, url, str(e),
                )
                return None
            import asyncio
            await asyncio.sleep(RETRY_BACKOFF_S * attempt)

    return None


async def fetch_senators(
    client: httpx.AsyncClient, db: Session
) -> list[dict]:
    """Fetch all current senators from Congress.gov."""
    cached = api_cache_get(db, "congress", "senators-list")
    if cached is not None:
        return cached

    logger.info("Fetching senators from Congress.gov...")
    members: list[dict] = []
    offset = 0
    limit = 250

    while True:
        data = await _fetch_with_retry(
            client,
            f"{CONGRESS_API_BASE}/member?currentMember=true&chamber=Senate&limit={limit}&offset={offset}",
        )
        if not data or not data.get("members"):
            break
        members.extend(data["members"])
        if len(data["members"]) < limit:
            break
        offset += limit

    logger.info("Fetched %d senators", len(members))
    api_cache_set(db, "congress", "senators-list", members)
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


async def fetch_member_sponsored(
    client: httpx.AsyncClient, db: Session, bioguide_id: str
) -> list[dict]:
    """Fetch a member's sponsored legislation."""
    cache_key = f"member-sponsored-{bioguide_id}"
    cached = api_cache_get(db, "congress", cache_key)
    if cached is not None:
        return cached

    data = await _fetch_with_retry(
        client,
        f"{CONGRESS_API_BASE}/member/{bioguide_id}/sponsored-legislation?limit=50",
    )
    results = (data or {}).get("sponsoredLegislation", [])
    api_cache_set(db, "congress", cache_key, results)
    return results


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
    results = (data or {}).get("actions", [])
    api_cache_set(db, "congress", cache_key, results)
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
    results = (data or {}).get("summaries", [])
    api_cache_set(db, "congress", cache_key, results)
    return results


async def fetch_roll_call_vote(
    client: httpx.AsyncClient,
    db: Session,
    congress: int,
    session_number: int,
    roll_call_number: int,
) -> dict | None:
    """Fetch Senate roll call vote details from senate.gov XML feed.

    Congress.gov API doesn't have Senate roll call votes -- only senate.gov does.
    """
    cache_key = f"rollcall-senate-{congress}-{session_number}-{roll_call_number}"
    cached = api_cache_get(db, "congress", cache_key)
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
        resp = await client.get(url, timeout=30.0)
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
            api_cache_set(db, "congress", cache_key, result)
        return result
    except Exception as e:
        logger.error(
            "Failed to fetch Senate roll call %d-%d-%d: %s",
            congress, session_number, roll_call_number, str(e),
        )
        return None


def parse_senate_vote_xml(
    xml_text: str, congress: int, session: int, roll_number: int
) -> dict | None:
    """Parse Senate.gov roll call vote XML into a structured object.

    Uses lxml.etree XPath to extract each senator's vote,
    keyed by last_name + state for matching.
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

    return {
        "congress": congress,
        "session": session,
        "rollNumber": roll_number,
        "members": members,
    }
