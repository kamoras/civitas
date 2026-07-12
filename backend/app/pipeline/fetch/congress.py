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
    """Keep only bills from the current and previous congress.

    Scoring (LE volume, promise derivation, key-vote selection) operates
    on the recent window; feeding career-length sponsorship lists into
    the analyze phase is what blew pipeline run 69 up to 24h (58,435
    bills for 100 senators, ~8h analyze, plus thousands of unbounded
    official-title fetches). The full fetched list stays in ApiCache
    verbatim; this bound applies at the point of use.
    """
    min_congress = settings.CURRENT_CONGRESS - 1
    return [b for b in bills if (b.get("congress") or 0) >= min_congress]


async def fetch_member_sponsored(
    client: httpx.AsyncClient, db: Session, bioguide_id: str
) -> list[dict]:
    """Fetch a member's sponsored legislation with pagination.

    Returns only bills from the current and previous congress; see
    _recent_congresses_only for why.
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
        page = (data or {}).get("sponsoredLegislation", [])
        all_results.extend(page)
        if len(page) < page_size:
            break
        offset += page_size

    api_cache_set(db, "congress", cache_key, all_results)
    return _recent_congresses_only(all_results)


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
    raw = (data or {}).get("actions", [])
    # Congress.gov v3 may return {"count": N, "item": [...]} instead of a list
    results = raw.get("item", []) if isinstance(raw, dict) else (raw or [])
    api_cache_set(db, "congress", cache_key, results)
    return results


async def fetch_bill_cosponsors(
    client: httpx.AsyncClient,
    db: Session,
    congress: int,
    bill_type: str,
    bill_number: int,
) -> list[dict]:
    """Fetch cosponsors for a bill (includes bioguideId, party, state)."""
    cache_key = f"bill-cosponsors-{congress}-{bill_type}-{bill_number}"
    cached = api_cache_get(db, "congress", cache_key)
    if cached is not None:
        return cached

    data = await _fetch_with_retry(
        client,
        f"{CONGRESS_API_BASE}/bill/{congress}/{bill_type}/{bill_number}/cosponsors?limit=250",
    )
    raw = (data or {}).get("cosponsors", [])
    results = raw.get("item", []) if isinstance(raw, dict) else (raw or [])
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
    raw = (data or {}).get("summaries", [])
    results = raw.get("item", []) if isinstance(raw, dict) else (raw or [])
    api_cache_set(db, "congress", cache_key, results)
    return results


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
    cache_key = f"bill-titles-{congress}-{bill_type}-{bill_number}"
    cached = api_cache_get(db, "congress", cache_key)
    if cached is not None:
        return cached

    data = await _fetch_with_retry(
        client,
        f"{CONGRESS_API_BASE}/bill/{congress}/{bill_type}/{bill_number}/titles",
    )
    raw = (data or {}).get("titles", [])
    results = raw.get("item", []) if isinstance(raw, dict) else (raw or [])
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


async def fetch_house_roll_call_vote(
    client: httpx.AsyncClient,
    db: Session,
    year: int,
    roll_call_number: int,
) -> dict | None:
    """Fetch House roll call vote from clerk.house.gov XML feed."""
    cache_key = f"rollcall-house-{year}-{roll_call_number}"
    cached = api_cache_get(db, "congress", cache_key)
    if cached is not None:
        return cached

    await _rate_limiter.acquire()
    padded_roll = str(roll_call_number)
    url = f"https://clerk.house.gov/evs/{year}/roll{padded_roll}.xml"

    try:
        logger.debug("House Clerk vote: %s", url)
        resp = await client.get(url, timeout=30.0)
        if resp.status_code != 200:
            logger.warning(
                "House roll call not found: %d-%d (%d)",
                year, roll_call_number, resp.status_code,
            )
            return None

        result = parse_house_vote_xml(resp.text, year, roll_call_number)
        if result:
            api_cache_set(db, "congress", cache_key, result)
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
        "voteDate": "",
        "question": question,
        "documentTitle": vote_desc or legis_num,
        "documentName": legis_num,
        "members": members,
        "chamber": "House",
    }


async def fetch_recent_house_roll_calls(
    client: httpx.AsyncClient,
    db: Session,
    year: int = 2025,
    count: int = 15,
) -> list[dict]:
    """Fetch the last `count` House roll calls for a given year.

    Probes clerk.house.gov starting from a high roll number, working
    backward until valid votes are found.
    """
    cache_key = f"recent-house-rollcalls-{year}-{count}"
    cached = api_cache_get(db, "congress", cache_key)
    if cached is not None:
        return cached

    logger.info("Discovering recent House roll calls (year=%d)...", year)


    highest_valid = 0

    for probe in [700, 500, 400, 300, 200, 100, 50, 25, 10]:
        url = f"https://clerk.house.gov/evs/{year}/roll{probe}.xml"
        await _rate_limiter.acquire()
        try:
            resp = await client.get(url, timeout=15.0)
            if resp.status_code == 200:
                highest_valid = max(highest_valid, probe)
                break
        except Exception:
            continue

    if highest_valid == 0:
        logger.warning("No recent House roll calls found for year %d", year)
        return []

    check = highest_valid + 1
    while check <= highest_valid + 50:
        url = f"https://clerk.house.gov/evs/{year}/roll{check}.xml"
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

    logger.info("Highest House roll call found: %d", highest_valid)

    results: list[dict] = []
    for roll in range(highest_valid, max(0, highest_valid - count - 5), -1):
        if len(results) >= count:
            break
        roll_data = await fetch_house_roll_call_vote(client, db, year, roll)
        if roll_data:
            results.append(roll_data)

    logger.info("Fetched %d recent House roll calls", len(results))
    api_cache_set(db, "congress", cache_key, results)
    return results


import re as _re

_platform_rate_limiter = RateLimiter(0.5)  # max 0.5 req/s to senator websites

_STRIP_TAGS = {
    "script", "style", "nav", "header", "footer", "aside",
    "noscript", "svg", "form", "iframe", "button", "select",
    "input", "textarea",
}


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
    plain = _re.sub(r"\s+", " ", plain).strip()
    plain = _strip_nav_artifacts(plain)
    return plain


_NAV_ARTIFACT_PATTERNS = _re.compile(
    r"(?:Skip to (?:primary navigation|main content|content)|"
    r"Toggle (?:navigation|submenu|menu)|"
    r"× Close (?:Mobile Nav|Menu)|"
    r"Menu Menu Menu|Hamburger|Breadcrumb|"
    r"Share (?:on|via) (?:Facebook|Twitter|X|LinkedIn|Email)|"
    r"(?:Facebook|Twitter|Instagram|YouTube)\s*(?:Facebook|Twitter|Instagram|YouTube)|"
    r"How Can [\w]+ Help\?|"
    r"Send [\w]+ A Message|"
    r"Scheduling Requests|"
    r"HELP WITH A FEDERAL AGENCY|"
    r"Flag Request Servi\w*|"
    r"Appropriations & CDS Requests|"
    r"Schedule a Tour)",
    _re.IGNORECASE,
)


def _strip_nav_artifacts(text: str) -> str:
    """Remove common navigation/UI artifacts from extracted text."""
    text = _NAV_ARTIFACT_PATTERNS.sub("", text)
    text = _re.sub(r"\s+", " ", text).strip()
    if text.startswith(". ") or text.startswith(", "):
        text = text[2:].strip()
    return text


_BOILERPLATE_SIGNALS = [
    "skip to content", "skip to primary navigation", "skip to main",
    "open search", "close search", "toggle submenu",
    "follow senator", "on social media", "newsletter signup",
    "how can i help", "facebook-f", "x-twitter",
    "share your opinion", "scheduling requests", "media requests",
    "stay engaged", "connect with senator",
]

_POLICY_SIGNALS = [
    "legislation", "vote", "bill", "act", "committee", "sponsor",
    "policy", "reform", "regulation", "funding", "appropriation",
    "healthcare", "education", "defense", "economy", "tax",
    "environment", "immigration", "security", "infrastructure",
    "agriculture", "energy", "veterans", "social security",
    "medicare", "medicaid", "gun", "labor", "trade",
    "budget", "amendment", "bipartisan",
]


def _is_policy_content(text: str) -> bool:
    """Detect whether scraped text has actual policy content vs boilerplate."""
    lower = text[:1500].lower()

    boilerplate_hits = sum(1 for s in _BOILERPLATE_SIGNALS if s in lower)
    policy_hits = sum(1 for s in _POLICY_SIGNALS if s in lower)

    words = lower.split()
    if len(words) < 40:
        return False

    if boilerplate_hits >= 4 and policy_hits < 3:
        return False

    return True


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
    cache_key = f"platform-text-{senator_id}-v4"
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

            # Reject error pages that slipped through with a 200 status
            plain_lower = plain.lower()
            if any(sig in plain_lower for sig in (
                "page not found", "404 error", "page requested",
                "not found (404)", "page you requested",
                "this page doesn't exist", "page does not exist",
            )):
                logger.debug("Skipping error page for %s at %s", senator_name, url)
                continue

            if len(plain) > 200 and _is_policy_content(plain):
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
