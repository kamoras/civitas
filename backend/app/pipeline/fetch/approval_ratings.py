"""Fetch senator approval ratings from publicly available sources.

Primary source: Wikipedia (most reliable structured data)
Fallback: Morning Consult (when not blocked by Cloudflare)

These ratings are directional indicators, not precise tracking polls.
"""

import asyncio
import logging
import re

import httpx
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set

logger = logging.getLogger(__name__)


async def _try_wikipedia(
    client: httpx.AsyncClient,
    senator_name: str,
) -> dict | None:
    """Extract approval/favorability data from Wikipedia.

    Searches the senator's main page and the electoral history for
    any approval/favorability numbers cited from pollsters.
    """
    try:
        wiki_name = senator_name.replace(" ", "_")
        url = (
            "https://en.wikipedia.org/w/api.php"
            f"?action=parse&page={wiki_name}&prop=wikitext&format=json"
            "&redirects=1"
        )
        resp = await client.get(
            url,
            timeout=10.0,
            headers={"User-Agent": "ModernPunk/1.0 (civic transparency tool)"},
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        wikitext = (data.get("parse") or {}).get("wikitext", {}).get("*", "")
        if not wikitext:
            return None

        approval_patterns = [
            # "approval rating was 67% ... disapproval rating 28%"
            re.compile(
                r"approval\s+(?:rating\s+)?(?:(?:of|was|is|at|stands?\s+at)\s+)?"
                r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)"
                r".*?"
                r"disapproval\s+(?:rating\s+)?(?:(?:of|was|is|at|stands?\s+at)\s+)?"
                r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)",
                re.IGNORECASE | re.DOTALL,
            ),
            # "67% approval ... 28% disapproval"
            re.compile(
                r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s+approv(?:al|e)"
                r".*?"
                r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s+disapprov(?:al|e)",
                re.IGNORECASE | re.DOTALL,
            ),
            # "an 83% approval rating" (single mention, search for disapproval nearby)
            re.compile(
                r"(?:a|an|has)\s+(?:a\s+)?(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s+approv(?:al|e)"
                r".*?"
                r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s+disapprov(?:al|e)",
                re.IGNORECASE | re.DOTALL,
            ),
            # "favorability ... unfavorability" variant
            re.compile(
                r"favorab(?:le|ility)\s+(?:rating\s+)?(?:(?:of|was|is|at)\s+)?"
                r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)"
                r".*?"
                r"unfavorab(?:le|ility)\s+(?:rating\s+)?(?:(?:of|was|is|at)\s+)?"
                r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)",
                re.IGNORECASE | re.DOTALL,
            ),
            # Morning Consult / Civiqs specific: "found X has a 65% approval"
            re.compile(
                r"(?:Morning\s+Consult|Civiqs|poll(?:ing)?)"
                r".*?"
                r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s*approv(?:al|e)"
                r".*?"
                r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s*disapprov(?:al|e)",
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

        # Fallback: find any single approval % in the Approval ratings section
        section_match = re.search(
            r"(?:Approval|Favorability)\s+rating.*?(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s*approv",
            wikitext,
            re.IGNORECASE | re.DOTALL,
        )
        if section_match:
            approve = float(section_match.group(1))
            if 10 <= approve <= 95:
                return {
                    "approve": approve,
                    "disapprove": round(100 - approve - 10, 1),
                    "source": "Wikipedia (estimated)",
                }
    except Exception:
        pass
    return None


async def _try_morning_consult(
    client: httpx.AsyncClient,
    senator_name: str,
) -> dict | None:
    """Try Morning Consult's data endpoint for senator approval.

    Often blocked by Cloudflare, so this is a fallback.
    """
    try:
        slug = senator_name.lower().replace(" ", "-").replace(".", "")
        url = f"https://morningconsult.com/wp-json/mc-polls/v1/senator/{slug}"
        resp = await client.get(
            url,
            timeout=10.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible)"},
        )
        if resp.status_code == 200:
            data = resp.json()
            approve = data.get("approve")
            disapprove = data.get("disapprove")
            if approve and disapprove:
                return {
                    "approve": float(approve),
                    "disapprove": float(disapprove),
                    "source": "Morning Consult",
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

    Returns dict with approve, disapprove, source keys or None.
    Results are cached for 7 days.
    """
    cache_key = f"approval-{senator_id}-v3"
    cached = api_cache_get(db, "approval", cache_key, max_age_hours=168)
    if cached is not None:
        return cached if cached != {} else None

    for fetcher in (_try_wikipedia, _try_morning_consult):
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
