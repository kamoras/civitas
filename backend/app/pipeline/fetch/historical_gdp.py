"""Fetch historical US real GDP (1790-present) from MeasuringWorth.

Extends Effectiveness's GDP component to every president. BEA/FRED (this
platform's existing GDP source, via economic_data.py) only covers 1930
(FRED) or 1947 (BEA NIPA) onward — real limits of those specific series,
not of GDP data existing at all. MeasuringWorth (Samuel H. Williamson,
"What Was the U.S. GDP Then?", an established academic economic-history
reference already cited by name in this platform's own Effectiveness
component comments) publishes a continuous annual real-GDP series back to
1790, built from the same NIPA/BEA series for the modern era and
historical reconstructions (Johnston & Williamson) for the pre-1929
period — one continuous series, not a splice this platform has to
reconcile itself.

Uses the site's CSV export endpoint (found during development — the
site's own "Download the Results in a Spreadsheet Format" link) rather
than scraping the interactive result page's embedded HTML form data,
which is far more fragile.
"""

import csv
import io
import logging

import httpx
from sqlalchemy.orm import Session

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

EXPORT_URL = "https://www.measuringworth.com/datasets/usgdp/export.php"

_RATE_LIMITER = RateLimiter(rps=1.0)
_CACHE_TIER = "historical-gdp"
_CACHE_KEY = "real-gdp-1790-present"
_CACHE_MAX_AGE_HOURS = 24 * 30  # a fully-historical annual series changes at most once/year


def _parse_gdp_csv(text: str) -> dict[int, float]:
    """Returns year -> real GDP (millions of constant dollars).

    First line is a citation string, not CSV data — skipped if it doesn't
    parse as a (year, value) row.
    """
    result: dict[int, float] = {}
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if len(row) < 2:
            continue
        year_text, value_text = row[0].strip(), row[1].strip().replace(",", "")
        try:
            year = int(year_text)
            value = float(value_text)
        except ValueError:
            continue  # citation line, header line, or a blank/malformed row
        result[year] = value
    return result


async def fetch_historical_real_gdp(
    client: httpx.AsyncClient, db: Session, start_year: int = 1790, end_year: int = 2025,
) -> dict[int, float]:
    """Fetch the full annual real-GDP series once; callers compute each
    president's own term growth rate from the shared series (same pattern
    as economic_data.fetch_gdp_by_year, reused per-president rather than
    re-fetched).

    Returns an empty dict (never None) on failure."""
    cached = api_cache_get(db, _CACHE_TIER, _CACHE_KEY, max_age_hours=_CACHE_MAX_AGE_HOURS)
    if cached is not None:
        return {int(k): v for k, v in cached["data"].items()}

    url = f"{EXPORT_URL}?year_source={start_year}&year_result={end_year}&use%5B%5D=REALGDP"
    resp = await fetch_with_retry(
        client, _RATE_LIMITER, "GET", url, log_label="MeasuringWorth real GDP",
    )
    if resp is None or resp.status_code != 200:
        logger.warning("Failed to fetch MeasuringWorth GDP export (%s)", url)
        return {}

    try:
        data = _parse_gdp_csv(resp.text)
    except Exception:
        logger.exception("Failed to parse MeasuringWorth GDP CSV")
        return {}

    if not data:
        logger.warning("MeasuringWorth GDP export parsed to zero rows — export format may have changed")
        return {}

    api_cache_set(db, _CACHE_TIER, _CACHE_KEY, {"data": data})
    return data


def compute_term_gdp_growth(
    gdp_by_year: dict[int, float], term_start_year: int, term_end_year: int,
) -> float | None:
    """Average annual real-GDP growth rate (%) across a president's term,
    computed from the shared year->GDP series.

    Mirrors economic_data.calculate_gdp_adjusted's year-1-exclusion
    reasoning (Blinder & Watson 2016) where enough years are available —
    a term shorter than 3 calendar years (e.g. a partial/ongoing term)
    just uses the years it has rather than excluding down to nothing.
    """
    years = [y for y in range(term_start_year, term_end_year + 1) if y in gdp_by_year]
    if len(years) >= 4:
        years = years[1:]  # exclude year 1 (prior administration's inherited economy)
    if len(years) < 2:
        return None
    growth_rates = []
    for y in years[1:]:
        prev = gdp_by_year.get(y - 1)
        cur = gdp_by_year.get(y)
        if prev and cur and prev > 0:
            growth_rates.append((cur - prev) / prev * 100)
    if not growth_rates:
        return None
    return sum(growth_rates) / len(growth_rates)
