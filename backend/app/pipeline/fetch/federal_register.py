"""Fetch executive order data from the Federal Register API.

The Federal Register API (federalregister.gov/api/v1) is free with no API key.
It contains presidential documents from Clinton (1994) onward.
"""

import logging
import re
from datetime import date, timedelta

import httpx
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S

logger = logging.getLogger(__name__)

FR_BASE = "https://www.federalregister.gov/api/v1"


# "Deemed Significant Under EO 12866" — OMB/OIRA's own certified classification
# for a rule with substantial economic or policy impact, NOT a heuristic this
# codebase invented. Confirmed live: raw final rules ("RULE" type) publish at
# roughly a dozen a day, overwhelmingly routine/administrative (a railroad
# training-simulation waiver, a fisheries quota adjustment) — unusable volume
# for an early-signal feed. Filtering to `significant=1` cuts that to roughly
# 1/day, the same order of magnitude as a Senate/House final-passage vote.
SIGNIFICANT_RULE_LOOKBACK_DAYS = 3


# A trailing "; Correction"-shaped clause marks a metadata/formatting fix
# to an already-published rule, not a new substantive action — the
# underlying rule was the actual news, days earlier, and would already
# have been surfaced on its own publication. Confirmed live: the real
# vocabulary is broader than the bare word "Correction" — "Correction and
# Technical Amendment" and "Correcting Amendments" are both genuine
# correction notices for otherwise-significant rules. Matching "correct"
# immediately after a semicolon (not just the exact word "Correction")
# catches all three without also matching a substantive clause that merely
# mentions "correct" mid-sentence (e.g. "; Requiring Correct Labeling" —
# "correct" there isn't adjacent to the semicolon).
_CORRECTION_CLAUSE_RE = re.compile(r";\s*correct", re.IGNORECASE)


def _is_correction(title: str) -> bool:
    return bool(_CORRECTION_CLAUSE_RE.search(title))


async def fetch_recent_significant_rules(
    client: httpx.AsyncClient,
    db: Session,
    max_age_hours: int | None = None,
) -> list[dict]:
    """Fetch recently published significant final rules for early-signal
    reporting — see SIGNIFICANT_RULE_LOOKBACK_DAYS for why "significant" is
    the notability gate rather than "any final rule".

    `max_age_hours` overrides the default cache TTL, matching congress.py's
    fetch_recent_roll_calls — a much shorter value for the near-real-time
    early-signal poller than the nightly scoring caller would use.
    """
    cache_key = f"significant-rules-{SIGNIFICANT_RULE_LOOKBACK_DAYS}d"
    cached = api_cache_get(db, "federal_register", cache_key, max_age_hours=max_age_hours)
    if cached is not None:
        return cached

    start = (date.today() - timedelta(days=SIGNIFICANT_RULE_LOOKBACK_DAYS)).isoformat()
    params = {
        "conditions[type][]": "RULE",
        "conditions[significant]": "1",
        "conditions[publication_date][gte]": start,
        "per_page": 20,
        "order": "newest",
    }
    try:
        resp = await client.get(
            f"{FR_BASE}/documents.json", params=params, timeout=DEFAULT_FETCH_TIMEOUT_S,
        )
        resp.raise_for_status()
        raw_results = resp.json().get("results", [])
    except Exception as e:
        logger.warning("Federal Register recent significant rules fetch failed: %s", e)
        return []

    results = [
        {
            "title": r.get("title", ""),
            "abstract": r.get("abstract") or "",
            "documentNumber": r.get("document_number", ""),
            "htmlUrl": r.get("html_url", ""),
            "publicationDate": r.get("publication_date", ""),
            "agencies": [a.get("name", "") for a in r.get("agencies", []) if a.get("name")],
        }
        for r in raw_results
        if not _is_correction(r.get("title", ""))
    ]
    api_cache_set(db, "federal_register", cache_key, results, normal_ttl_hours=max_age_hours)
    return results
