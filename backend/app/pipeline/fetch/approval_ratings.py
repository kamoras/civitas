"""Fetch senator approval ratings from publicly available sources.

Primary source: Wikipedia (most reliable structured data for senators).

These ratings are directional indicators, not precise tracking polls.
Wikipedia pages cite polls from Morning Consult, Civiqs, SurveyUSA,
and other outlets; we extract the numbers from wikitext markup.
"""

import asyncio
import logging
import re

import httpx
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set

logger = logging.getLogger(__name__)

_WIKI_UA = (
    "Civitas/1.0 "
    "(https://github.com/kamoras/civitas; civic-transparency-tool) "
    "httpx/0.27"
)

_APPROVAL_PATTERNS = [
    # "approval rating at 62%, with 31% disapproving"
    re.compile(
        r"approval\s+(?:rating\s+)?(?:(?:of|was|is|at|stands?\s+at)\s+)?"
        r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)"
        r".{0,120}?"
        r"(?:with\s+)?(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s*"
        r"(?:disapprov(?:al|ing|e)|negative)",
        re.IGNORECASE | re.DOTALL,
    ),
    # "disapproval rating was 28% ... approval 67%"  (reversed order)
    re.compile(
        r"disapproval\s+(?:rating\s+)?(?:(?:of|was|is|at)\s+)?"
        r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)"
        r".{0,120}?"
        r"approval\s+(?:rating\s+)?(?:(?:of|was|is|at)\s+)?"
        r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)",
        re.IGNORECASE | re.DOTALL,
    ),
    # "67% approval ... 28% disapproval"
    re.compile(
        r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s+approv(?:al|e)"
        r".{0,120}?"
        r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s+disapprov(?:al|e)",
        re.IGNORECASE | re.DOTALL,
    ),
    # "X% positive and Y% negative" (Morning Consult style)
    re.compile(
        r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s+positive"
        r".{0,80}?"
        r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s+negative",
        re.IGNORECASE | re.DOTALL,
    ),
    # "favorability ... unfavorability"
    re.compile(
        r"favorab(?:le|ility)\s+(?:rating\s+)?(?:(?:of|was|is|at)\s+)?"
        r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)"
        r".{0,120}?"
        r"unfavorab(?:le|ility)\s+(?:rating\s+)?(?:(?:of|was|is|at)\s+)?"
        r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)",
        re.IGNORECASE | re.DOTALL,
    ),
    # "an X% approval ... Y% disapproval" with connective words
    re.compile(
        r"(?:a|an|has|had|with)\s+(?:a\s+)?(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s+"
        r"approv(?:al|e)"
        r".{0,120}?"
        r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s+disapprov(?:al|e)",
        re.IGNORECASE | re.DOTALL,
    ),
    # Morning Consult / Civiqs / poll specific
    re.compile(
        r"(?:Morning\s+Consult|Civiqs|SurveyUSA|poll(?:ing|s)?)"
        r".{0,200}?"
        r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s*approv(?:al|e)"
        r".{0,120}?"
        r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s*disapprov(?:al|e)",
        re.IGNORECASE | re.DOTALL,
    ),
    # "approve ... disapprove" with numbers nearby
    re.compile(
        r"approv(?:al|e)\s+(?:rating\s+)?(?:(?:of|was|is|at|stands?\s+at)\s+)?"
        r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)"
        r".{0,200}?"
        r"disapprov(?:al|e)\s+(?:rating\s+)?(?:(?:of|was|is|at|stands?\s+at)\s+)?"
        r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)",
        re.IGNORECASE | re.DOTALL,
    ),
]

_REVERSED_PATTERN_INDICES = {1}


def _validate_rating(approve: float, disapprove: float) -> bool:
    """Sanity-check extracted numbers."""
    if not (10 <= approve <= 95 and 5 <= disapprove <= 90):
        return False
    if approve + disapprove > 110:
        return False
    return True


async def _try_wikipedia(
    client: httpx.AsyncClient,
    senator_name: str,
) -> dict | None:
    """Extract approval/favorability data from Wikipedia wikitext."""
    try:
        wiki_name = senator_name.replace(" ", "_")
        url = (
            "https://en.wikipedia.org/w/api.php"
            f"?action=parse&page={wiki_name}&prop=wikitext&format=json"
            "&redirects=1"
        )
        resp = await client.get(url, timeout=12.0, headers={"User-Agent": _WIKI_UA})
        if resp.status_code != 200:
            logger.debug(
                "Wikipedia returned %d for %s", resp.status_code, senator_name,
            )
            return None

        data = resp.json()
        wikitext = (data.get("parse") or {}).get("wikitext", {}).get("*", "")
        if not wikitext:
            return None

        # Focus on the approval ratings section if it exists
        section_start = None
        section_text = wikitext
        for marker in ("Approval ratings===", "Approval rating===",
                        "Favorability===", "Public image===",
                        "Approval ratings ==", "Approval rating =="):
            idx = wikitext.lower().find(marker.lower())
            if idx >= 0:
                section_start = idx
                section_text = wikitext[idx:idx + 3000]
                break

        for i, pattern in enumerate(_APPROVAL_PATTERNS):
            # Search in section first, then full text
            for text in ([section_text, wikitext] if section_start is not None else [wikitext]):
                match = pattern.search(text)
                if match:
                    if i in _REVERSED_PATTERN_INDICES:
                        disapprove = float(match.group(1))
                        approve = float(match.group(2))
                    else:
                        approve = float(match.group(1))
                        disapprove = float(match.group(2))

                    if _validate_rating(approve, disapprove):
                        return {
                            "approve": approve,
                            "disapprove": disapprove,
                            "source": "Wikipedia",
                        }

        # Last resort: single approval number in the approval section
        if section_start is not None:
            solo = re.search(
                r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s*"
                r"(?:approv(?:al|e|ing)|positive|favorable)",
                section_text,
                re.IGNORECASE,
            )
            if solo:
                approve = float(solo.group(1))
                if 10 <= approve <= 95:
                    return {
                        "approve": approve,
                        "disapprove": round(100 - approve - 10, 1),
                        "source": "Wikipedia (estimated)",
                    }

    except Exception as exc:
        logger.debug("Wikipedia approval parse error for %s: %s", senator_name, exc)
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
    cache_key = f"approval-{senator_id}-v4"
    cached = api_cache_get(db, "approval", cache_key, max_age_hours=168)
    if cached is not None:
        return cached if cached != {} else None

    try:
        result = await _try_wikipedia(client, senator_name)
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
            await asyncio.sleep(0.5)

    logger.info(
        "Approval ratings fetched for %d/%d senators",
        len(results),
        len(senators),
    )
    return results
