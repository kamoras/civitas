"""Ticker -> company name resolution via SEC's public ticker/CIK mapping.

SEC.gov publishes a stable, first-party bulk JSON file mapping every
registered ticker to its company name and CIK — no third-party dependency,
consistent with this repo's sourcing posture (see issue #45 investigation:
the two third-party "Stock Watcher" shortcuts are both dead). Used to turn a
PTR's disclosed ticker into a company name that industry_classifier's
existing embedding classifier can score, without adding a second taxonomy.
"""

import logging

import httpx
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set

logger = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
# SEC requests a descriptive User-Agent with contact info on all automated
# requests (https://www.sec.gov/os/webmaster-faq#developers) — a generic UA
# risks a block.
_HEADERS = {"User-Agent": "Civitas civic-transparency-platform contact@civitas-research.org"}

_ticker_map_cache: dict[str, str] | None = None


async def _fetch_ticker_map(client: httpx.AsyncClient, db: Session) -> dict[str, str]:
    """Fetch + cache the full ticker -> company name map (all US-listed issuers)."""
    global _ticker_map_cache
    if _ticker_map_cache is not None:
        return _ticker_map_cache

    cached = api_cache_get(db, "sec_tickers", "company_tickers", max_age_hours=24 * 7)
    if cached is not None:
        _ticker_map_cache = cached
        return cached

    try:
        resp = await client.get(TICKERS_URL, headers=_HEADERS, timeout=30.0)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        logger.error("Failed to fetch SEC company_tickers.json: %s", e)
        return _ticker_map_cache or {}

    # Raw shape: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
    ticker_map = {
        (entry.get("ticker") or "").upper(): entry.get("title", "")
        for entry in raw.values()
        if entry.get("ticker")
    }
    api_cache_set(db, "sec_tickers", "company_tickers", ticker_map)
    _ticker_map_cache = ticker_map
    return ticker_map


async def resolve_tickers(
    client: httpx.AsyncClient, db: Session, tickers: list[str | None],
) -> dict[str, str]:
    """Resolve a batch of tickers to company names.

    Returns a dict of only the tickers that resolved — callers should fall
    back to the PTR's disclosed asset_name (not a guess) for anything
    missing, since not every ticker on a filing is a US-listed equity in
    SEC's registry (ETFs, options, foreign issuers, etc.).
    """
    ticker_map = await _fetch_ticker_map(client, db)
    wanted = {t.upper() for t in tickers if t}
    return {t: ticker_map[t] for t in wanted if t in ticker_map}
