"""Annual US unemployment and consumer-price series for presidential
Effectiveness (president v10), from FRED (Federal Reserve Bank of St. Louis):
UNRATE, the BLS civilian unemployment rate (monthly, from 1948), and
CPIAUCSL, the BLS consumer price index for all urban consumers (monthly,
from 1947). Each is averaged over a calendar year's published months, once
the year's December figure is out, so a year still under way is never read
as a whole one. A month the agency never published is left out of its
year's average rather than guessed: the October 2025 government shutdown
stopped BLS collection that month, and neither series has a figure for it.
"""

import csv
import io
import logging
import statistics
from collections import defaultdict

import httpx
from sqlalchemy.orm import Session

from app.contact import BOT_USER_AGENT
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S

logger = logging.getLogger(__name__)

UNEMPLOYMENT = "UNRATE"
CONSUMER_PRICES = "CPIAUCSL"

_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
_CACHE_TIER = "fred-annual-series"
_CACHE_TTL_HOURS = 24 * 7  # monthly releases; only complete years are used


def annual_averages(text: str) -> dict[int, float]:
    """{year: mean of its published monthly values} from a FRED graph CSV,
    for each year whose December is published. FRED writes a month with no
    figure as "" or "." ."""
    months: dict[int, list[float]] = defaultdict(list)
    december: set[int] = set()
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2 or not row[0][:4].isdigit():
            continue
        try:
            value = float(row[1])
        except ValueError:
            continue
        year = int(row[0][:4])
        months[year].append(value)
        if row[0][5:7] == "12":
            december.add(year)
    return {year: statistics.mean(values) for year, values in months.items() if year in december}


async def fetch_annual_series(client: httpx.AsyncClient, db: Session, series: str) -> dict[int, float] | None:
    """{year: annual average} for one FRED series, or None when it can't be
    fetched (an outage is not "no data": the caller keeps stored values)."""
    url = _URL.format(series=series)
    cached = api_cache_get(db, _CACHE_TIER, series, max_age_hours=_CACHE_TTL_HOURS)
    if cached is None:
        try:
            resp = await client.get(url, headers={"User-Agent": BOT_USER_AGENT}, timeout=DEFAULT_FETCH_TIMEOUT_S)
            resp.raise_for_status()
        except Exception:
            logger.warning("FRED %s fetch failed", series, exc_info=True)
            return None
        years = annual_averages(resp.text)
        if not years:
            logger.warning("FRED %s: no complete years in the response", series)
            return None
        cached = {"years": {str(y): v for y, v in years.items()}}
        api_cache_set(db, _CACHE_TIER, series, cached, normal_ttl_hours=_CACHE_TTL_HOURS)
    return {int(y): float(v) for y, v in cached["years"].items()}


def inflation_by_year(cpi: dict[int, float]) -> dict[int, float]:
    """Annual-average CPI growth into each year (%)."""
    return {y: (cpi[y] / cpi[y - 1] - 1) * 100 for y in cpi if y - 1 in cpi}
