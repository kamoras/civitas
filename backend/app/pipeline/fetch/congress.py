"""Fetch modules for Congress.gov and Senate.gov APIs."""

import logging

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
        congresses: Congress numbers to search (default: current + two previous).
        max_bills: Maximum number of bills to return.

    Returns:
        List of bill reference dicts with keys: congress, type, number, name.
    """
    if congresses is None:
        current = settings.CURRENT_CONGRESS
        congresses = [current, current - 1, current - 2]

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
                is_enacted = "became public law" in latest_action.lower()
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


def _root_text(root, xpath: str) -> str:
    """Extract text from the first element matching an XPath."""
    els = root.xpath(xpath)
    if els and hasattr(els[0], "text"):
        return (els[0].text or "").strip()
    return ""


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

    return {
        "congress": congress,
        "session": session,
        "rollNumber": roll_number,
        "voteTitle": vote_title,
        "voteDate": vote_date,
        "question": question,
        "documentTitle": document_title or vote_title,
        "documentName": document_name,
        "members": members,
    }


async def fetch_recent_roll_calls(
    client: httpx.AsyncClient,
    db: Session,
    congress: int = 119,
    session_number: int = 1,
    count: int = 15,
) -> list[dict]:
    """Fetch the last `count` Senate roll calls from the current session.

    Probes Senate.gov starting from a high roll number, working backward
    until we find valid votes, then fetches `count` of them.

    Returns list of parsed roll call dicts (newest first).
    """
    cache_key = f"recent-rollcalls-{congress}-{session_number}-{count}"
    cached = api_cache_get(db, "congress", cache_key)
    if cached is not None:
        return cached

    logger.info(
        "Discovering recent Senate roll calls (congress=%d, session=%d)...",
        congress, session_number,
    )

    # Binary-ish search: start high (500), find the highest valid roll call
    import asyncio

    highest_valid = 0

    # Probe at various points to find the range
    for probe in [500, 300, 200, 150, 100, 75, 50, 25, 10]:
        padded = str(probe).zfill(5)
        url = (
            f"https://www.senate.gov/legislative/LIS/roll_call_votes/"
            f"vote{congress}{session_number}/"
            f"vote_{congress}_{session_number}_{padded}.xml"
        )
        await _rate_limiter.acquire()
        try:
            resp = await client.get(url, timeout=15.0)
            if resp.status_code == 200:
                highest_valid = max(highest_valid, probe)
                break  # Found a valid upper bound
        except Exception:
            continue

    if highest_valid == 0:
        logger.warning("No recent roll calls found for congress %d session %d", congress, session_number)
        return []

    # Now search upward from the probe hit to find the actual highest
    check = highest_valid + 1
    while check <= highest_valid + 50:
        padded = str(check).zfill(5)
        url = (
            f"https://www.senate.gov/legislative/LIS/roll_call_votes/"
            f"vote{congress}{session_number}/"
            f"vote_{congress}_{session_number}_{padded}.xml"
        )
        await _rate_limiter.acquire()
        try:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 200:
                highest_valid = check
                check += 1
            else:
                break
        except Exception:
            break

    logger.info("Highest roll call found: %d", highest_valid)

    # Fetch the last `count` roll calls
    results: list[dict] = []
    for roll in range(highest_valid, max(0, highest_valid - count - 5), -1):
        if len(results) >= count:
            break

        roll_data = await fetch_roll_call_vote(
            client, db, congress, session_number, roll
        )
        if roll_data:
            results.append(roll_data)

    logger.info("Fetched %d recent roll calls", len(results))
    api_cache_set(db, "congress", cache_key, results)
    return results


import re as _re

_platform_rate_limiter = RateLimiter(0.5)  # max 0.5 req/s to senator websites

_STRIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "noscript", "svg", "form"}


def _extract_body_text(raw_html: str) -> str:
    """Extract meaningful text from HTML, stripping navigation chrome.

    Uses lxml to remove nav/header/footer/aside/script/style elements,
    then extracts text from <main> or <article> if present, falling back
    to <body>.  This avoids polluting platform text with menu items and
    sidebar links.
    """
    try:
        doc = etree.HTML(raw_html)
    except Exception:
        plain = _re.sub(r"<[^>]+>", " ", raw_html)
        return _re.sub(r"\s+", " ", plain).strip()

    for tag in _STRIP_TAGS:
        for el in doc.iter(tag):
            el.getparent().remove(el)

    # Prefer <main> or <article> content over the full <body>
    content_root = None
    for selector in ("main", "article"):
        found = doc.iter(selector)
        candidate = next(found, None)
        if candidate is not None:
            text = " ".join(candidate.itertext())
            if len(text.strip()) > 200:
                content_root = candidate
                break

    if content_root is None:
        body = doc.find(".//body")
        content_root = body if body is not None else doc

    plain = " ".join(content_root.itertext())
    return _re.sub(r"\s+", " ", plain).strip()


async def fetch_senator_platform_text(
    client: httpx.AsyncClient,
    db: Session,
    senator_id: str,
    senator_name: str,
    official_website_url: str,
) -> str:
    """Fetch a senator's stated platform/policy positions from their official website.

    Tries the following in order:
    1. officialWebsiteUrl/issues/
    2. officialWebsiteUrl/priorities/
    3. officialWebsiteUrl (homepage)
    4. Wikipedia "Political positions of {Name}" page

    Returns cleaned plain text truncated to 3000 characters, or empty string if all fail.
    """
    cache_key = f"platform-text-{senator_id}-v2"
    cached = api_cache_get(db, "platform", cache_key)
    if cached is not None:
        return cached

    text = ""

    # Build candidate URLs to try
    candidate_urls: list[str] = []
    if official_website_url:
        candidate_urls += [
            f"{official_website_url}/issues/",
            f"{official_website_url}/priorities/",
            official_website_url,
        ]

    # Wikipedia fallback — "Political positions of First Last"
    wiki_name = senator_name.replace(" ", "_")
    candidate_urls.append(
        f"https://en.wikipedia.org/wiki/Political_positions_of_{wiki_name}"
    )

    for url in candidate_urls:
        try:
            await _platform_rate_limiter.acquire()
            resp = await client.get(
                url,
                timeout=15.0,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ModernPunk/1.0)"},
            )
            if resp.status_code != 200:
                continue

            plain = _extract_body_text(resp.text)

            if len(plain) > 200:
                text = plain[:3000]
                break
        except Exception as e:
            logger.debug("Platform fetch failed for %s at %s: %s", senator_name, url, e)
            continue

    api_cache_set(db, "platform", cache_key, text)
    if text:
        logger.debug("Platform text fetched for %s (%d chars)", senator_name, len(text))
    else:
        logger.debug("No platform text found for %s", senator_name)
    return text
