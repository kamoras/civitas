"""Fetch executive order data from the Federal Register API.

The Federal Register API (federalregister.gov/api/v1) is free with no API key.
It contains presidential documents from Clinton (1994) onward.
"""

import logging

import httpx

from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S

logger = logging.getLogger(__name__)

FR_BASE = "https://www.federalregister.gov/api/v1"

PRESIDENT_SLUGS: dict[str, str] = {
    "clinton-42": "william-j-clinton",
    "gwbush-43": "george-w-bush",
    "obama-44": "barack-obama",
    "trump-45": "donald-trump",
    "biden-46": "joe-biden",
    "trump-47": "donald-trump",
}

TERM_RANGES: dict[str, tuple[str, str]] = {
    "clinton-42": ("1993-01-20", "2001-01-20"),
    "gwbush-43": ("2001-01-20", "2009-01-20"),
    "obama-44": ("2009-01-20", "2017-01-20"),
    "trump-45": ("2017-01-20", "2021-01-20"),
    "biden-46": ("2021-01-20", "2025-01-20"),
    "trump-47": ("2025-01-20", "2029-01-20"),
}


async def fetch_rulemaking_stats(
    client: httpx.AsyncClient,
    president_id: str,
) -> dict | None:
    """Fetch rulemaking activity stats during a president's term.

    Queries final rules and proposed rules to measure agency activity
    and effectiveness during the term.

    Returns dict with keys: rulemaking_count, rulemaking_finalized_pct
    """
    if president_id not in TERM_RANGES:
        return None

    term_start, term_end = TERM_RANGES[president_id]
    # Rules are agency documents, so the presidential-document president
    # filter used by fetch_eo_count doesn't apply here. Instead, end the
    # window the day BEFORE the next term starts: both endpoints are
    # inclusive and adjacent terms share Jan 20, so a raw [start, end]
    # query counted inauguration-day publications toward both presidents.
    from datetime import date, timedelta

    end_exclusive = (date.fromisoformat(term_end) - timedelta(days=1)).isoformat()
    counts: dict[str, int] = {}

    for doc_type in ("RULE", "PRORULE"):
        params = {
            "conditions[type][]": doc_type,
            "conditions[publication_date][gte]": term_start,
            "conditions[publication_date][lte]": end_exclusive,
            "per_page": 1,
            "page": 1,
        }
        try:
            resp = await client.get(
                f"{FR_BASE}/documents.json",
                params=params,
                timeout=DEFAULT_FETCH_TIMEOUT_S,
            )
            resp.raise_for_status()
            raw_count = resp.json().get("count", 0)
            counts[doc_type] = raw_count if isinstance(raw_count, int) else 0
        except Exception:
            logger.warning("FR rulemaking fetch failed")
            return None

    total = counts.get("RULE", 0) + counts.get("PRORULE", 0)
    finalized_pct = (
        (counts["RULE"] / total * 100) if total > 0 else 0.0
    )

    logger.info(
        "Federal Register rulemaking: %s — %d final, %d proposed (%.0f%% finalized)",
        president_id, counts.get("RULE", 0), counts.get("PRORULE", 0), finalized_pct,
    )
    return {
        "rulemaking_count": total,
        "rulemaking_finalized_pct": finalized_pct,
    }


async def fetch_all_rulemaking_stats(
    client: httpx.AsyncClient,
) -> dict[str, dict]:
    """Fetch rulemaking stats for all presidents in the Federal Register."""
    results: dict[str, dict] = {}
    for president_id in PRESIDENT_SLUGS:
        data = await fetch_rulemaking_stats(client, president_id)
        if data:
            results[president_id] = data
    return results
