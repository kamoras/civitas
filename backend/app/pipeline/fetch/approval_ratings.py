"""Fetch senator approval ratings from publicly available sources.

Primary source: Morning Consult senator approval tracker
Fallback: Civiqs daily tracking data
Last resort: Wikipedia favorability data

All sources use Cloudflare or similar protection, so we fetch the
structured JSON endpoints that back their interactive charts when possible.
"""

import asyncio
import logging
import re

import httpx
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set

logger = logging.getLogger(__name__)


async def _try_morning_consult(
    client: httpx.AsyncClient,
    senator_name: str,
) -> dict | None:
    """Try Morning Consult's data endpoint for senator approval.

    Morning Consult uses Cloudflare protection on their main site, but
    their embedded chart data may be accessible through their CDN.
    Returns None if blocked or unavailable.
    """
    try:
        slug = senator_name.lower().replace(" ", "-").replace(".", "")
        url = f"https://morningconsult.com/wp-json/mc-polls/v1/senator/{slug}"
        resp = await client.get(url, timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "approve": data.get("approve"),
                "disapprove": data.get("disapprove"),
                "source": "Morning Consult",
            }
    except Exception:
        pass
    return None


async def _try_civiqs(
    client: httpx.AsyncClient,
    senator_name: str,
) -> dict | None:
    """Try Civiqs' senator approval tracking data.

    Civiqs tracks approval for most senators with daily updates.
    Their data is in embedded JSON within the page HTML.
    """
    try:
        name_parts = senator_name.lower().split()
        first = name_parts[0]
        last = name_parts[-1]
        slug = f"{first}_{last}"
        url = f"https://civiqs.com/results/approve_senator_{slug}"
        resp = await client.get(
            url,
            timeout=10.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CivitasBot/1.0)"},
        )
        if resp.status_code != 200:
            return None

        text = resp.text
        approve_match = re.search(r'"Approve":\s*(\d+(?:\.\d+)?)', text)
        disapprove_match = re.search(r'"Disapprove":\s*(\d+(?:\.\d+)?)', text)
        if approve_match and disapprove_match:
            return {
                "approve": float(approve_match.group(1)),
                "disapprove": float(disapprove_match.group(1)),
                "source": "Civiqs",
            }
    except Exception:
        pass
    return None


async def _try_wikipedia(
    client: httpx.AsyncClient,
    senator_name: str,
) -> dict | None:
    """Extract approval/favorability data from Wikipedia.

    Many senator Wikipedia pages mention their approval rating from
    Morning Consult or other pollsters in the article text. We parse
    structured data from the Wikipedia API.
    """
    try:
        wiki_name = senator_name.replace(" ", "_")
        url = (
            "https://en.wikipedia.org/w/api.php"
            f"?action=parse&page={wiki_name}&prop=wikitext&format=json"
            "&redirects=1"
        )
        resp = await client.get(url, timeout=10.0)
        if resp.status_code != 200:
            return None

        data = resp.json()
        wikitext = (data.get("parse") or {}).get("wikitext", {}).get("*", "")
        if not wikitext:
            return None

        approval_patterns = [
            re.compile(
                r"approval\s+rating\s+(?:of\s+)?(\d{1,2}(?:\.\d+)?)\s*%"
                r".*?disapproval\s+(?:rating\s+)?(?:of\s+)?(\d{1,2}(?:\.\d+)?)\s*%",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"(\d{1,2}(?:\.\d+)?)\s*%\s+approv(?:al|e)"
                r".*?(\d{1,2}(?:\.\d+)?)\s*%\s+disapprov(?:al|e)",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"Morning\s+Consult.*?(\d{1,2}(?:\.\d+)?)\s*%\s+approv"
                r".*?(\d{1,2}(?:\.\d+)?)\s*%\s+disapprov",
                re.IGNORECASE | re.DOTALL,
            ),
        ]

        for pattern in approval_patterns:
            match = pattern.search(wikitext)
            if match:
                approve = float(match.group(1))
                disapprove = float(match.group(2))
                if 10 <= approve <= 95 and 5 <= disapprove <= 90:
                    return {
                        "approve": approve,
                        "disapprove": disapprove,
                        "source": "Wikipedia",
                    }
    except Exception:
        pass
    return None


async def fetch_senator_approval(
    client: httpx.AsyncClient,
    db: Session,
    senator_id: str,
    senator_name: str,
) -> dict | None:
    """Fetch approval rating for a senator from the best available source.

    Tries Morning Consult, Civiqs, and Wikipedia in order.
    Returns dict with approve, disapprove, source keys or None.
    Results are cached for 7 days.
    """
    cache_key = f"approval-{senator_id}-v1"
    cached = api_cache_get(db, "approval", cache_key, max_age_hours=168)
    if cached is not None:
        return cached if cached != {} else None

    for fetcher in (_try_morning_consult, _try_civiqs, _try_wikipedia):
        try:
            result = await fetcher(client, senator_name)
            if result:
                logger.info(
                    "Approval for %s: %.0f%% approve, %.0f%% disapprove (%s)",
                    senator_name,
                    result["approve"],
                    result["disapprove"],
                    result["source"],
                )
                api_cache_set(db, "approval", cache_key, result)
                return result
        except Exception as e:
            logger.debug("Approval fetch failed for %s: %s", senator_name, e)

    api_cache_set(db, "approval", cache_key, {})
    return None


async def fetch_all_senator_approvals(
    client: httpx.AsyncClient,
    db: Session,
    senators: list[dict],
) -> dict[str, dict]:
    """Fetch approval ratings for all senators.

    Returns a mapping of senator_id -> {approve, disapprove, source}.
    Fetches concurrently in small batches to avoid rate limiting.
    """
    results: dict[str, dict] = {}
    batch_size = 5

    for i in range(0, len(senators), batch_size):
        batch = senators[i : i + batch_size]
        tasks = [
            fetch_senator_approval(client, db, s["id"], s["name"])
            for s in batch
        ]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for senator, result in zip(batch, batch_results):
            if isinstance(result, dict) and result:
                results[senator["id"]] = result
            elif isinstance(result, Exception):
                logger.debug(
                    "Approval fetch error for %s: %s", senator["name"], result
                )

        if i + batch_size < len(senators):
            await asyncio.sleep(1.0)

    logger.info(
        "Approval ratings fetched for %d/%d senators",
        len(results),
        len(senators),
    )
    return results
