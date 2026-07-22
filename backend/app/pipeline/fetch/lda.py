"""Senate Lobbying Disclosure Act (LDA) filings — real lobbying spend.

The donor-vote overlap feature historically carried a lobbying_spend field
that was 0 for every match (the 2026-07 adversarial audit flagged the
feature as mislabeled). This module fills it with actual registered
federal lobbying activity from lda.senate.gov: for an organization, the
sum of registrant-reported income (outside firms hired by the org) plus
self-reported expenses (in-house lobbying) across a filing year.

Notes on interpretation:
- Amounts are order-of-magnitude signals, not audited totals — quarterly
  amendments can double-count and the client-name search is fuzzy on the
  LDA side. Good enough to distinguish "this org lobbies Washington with
  $2M/yr" from "no registered lobbying at all".
- The API is public, no key required; anonymous rate limit is low, so
  results are cached hard in api_cache and only matched donor orgs
  (a few hundred unique names) are ever queried.
"""

import hashlib
import logging

import httpx
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

LDA_API_BASE = "https://lda.senate.gov/api/v1"

# Anonymous LDA limit is ~15 requests/minute — stay safely under it.
_rate_limiter = RateLimiter(0.2)


def _sum_filing_amounts(results: list[dict]) -> float:
    """Sum lobbying income+expenses across filings, skipping registrations.

    Registration filings (RR) carry no amounts; termination filings can.
    Income = what outside firms report earning from this client;
    expenses = what the org reports spending on in-house lobbying.
    """
    total = 0.0
    for filing in results or []:
        ftype = (filing.get("filing_type") or "").upper()
        if ftype.startswith("RR"):
            continue
        for field in ("income", "expenses"):
            val = filing.get(field)
            if val:
                try:
                    total += float(val)
                except (TypeError, ValueError):
                    pass
    return total


async def fetch_lobbying_spend(
    client: httpx.AsyncClient, db: Session, org_name: str, year: int
) -> float:
    """Total registered lobbying activity for an organization in a year.

    Returns 0.0 for organizations with no registered lobbying (which is
    itself meaningful) and on any fetch failure (logged, non-fatal).
    """
    org_key = (org_name or "").strip().upper()
    if len(org_key) < 3:
        return 0.0

    # Include a stable hash of the full org key so two different orgs that
    # share an 80-char prefix (e.g. federal vs. state PAC variants of one
    # sponsor) can't collide onto one cached spend figure.
    key_hash = hashlib.sha256(org_key.encode()).hexdigest()[:12]
    cache_key = f"lda-spend-{year}-{org_key[:60]}-{key_hash}"
    cached = api_cache_get(db, "lda", cache_key)
    if cached is not None:
        return float(cached.get("total", 0.0))

    # Follow pagination: a heavy-lobbying client can file dozens to
    # low-hundreds of filings a year (multiple outside firms × quarterly
    # reports + in-house). Summing only the first page systematically
    # UNDERcounted exactly the biggest spenders — and the figure is shown
    # verbatim in user-facing text. Bounded to keep one pathological org
    # from stalling the enrichment loop; the cap is logged if hit so a
    # silent truncation can't masquerade as a complete total.
    _MAX_PAGES = 10
    total = 0.0
    url: str | None = f"{LDA_API_BASE}/filings/"
    params: dict | None = {"client_name": org_key, "filing_year": year, "page_size": 25}
    pages = 0
    try:
        while url and pages < _MAX_PAGES:
            await _rate_limiter.acquire()
            resp = await client.get(url, params=params, timeout=DEFAULT_FETCH_TIMEOUT_S)
            if resp.status_code == 429:
                logger.warning("LDA rate limited for %s — skipping (uncached)", org_key)
                return 0.0
            resp.raise_for_status()
            data = resp.json()
            total += _sum_filing_amounts(data.get("results", []))
            url = data.get("next")  # absolute URL from the API, or None
            params = None  # `next` already encodes the query
            pages += 1
        if url and pages >= _MAX_PAGES:
            logger.warning(
                "LDA spend for %s (%d) hit the %d-page cap — total may be a lower bound",
                org_key, year, _MAX_PAGES,
            )
    except Exception as exc:
        logger.warning("LDA fetch failed for %s: %s", org_key, exc)
        return 0.0

    api_cache_set(db, "lda", cache_key, {"total": round(total, 2)})
    return total


async def enrich_lobbying_matches_with_lda(matches: list[dict], db: Session, lda_year: int) -> None:
    """Mutate donor-vote lobbying matches in place, adding real registered
    lobbying spend (LDA filings) to each.

    Uses its own short-lived httpx client rather than a caller-supplied one:
    an earlier version reused the FETCH-phase client here, which was already
    closed by the time the analysis loop ran, and silently failed every LDA
    lookup with "client has been closed" (2026-07 finding: 184 failures in a
    single run — lobbyingSpend had effectively always been 0 in production).
    Best-effort per match: one org's lookup failing doesn't block the others.
    Shared by senate_pipeline.py and house_pipeline.py.
    """
    if not matches:
        return

    async with httpx.AsyncClient() as lda_client:
        for m in matches:
            try:
                spend = await fetch_lobbying_spend(
                    lda_client, db, m.get("lobbyistOrg", ""), lda_year,
                )
                m["lobbyingSpend"] = round(spend)
                if spend > 0:
                    m["description"] = (
                        m.get("description", "")
                        + f" Registered federal lobbying (LDA {lda_year}): ${spend:,.0f}."
                    )
            except Exception:
                logger.exception(
                    "LDA enrichment failed for %s (non-fatal)", m.get("lobbyistOrg", "?"),
                )
