"""Fetch economic data from BLS public API.

BLS public API (api.bls.gov) requires no key for basic access (25 queries/day).
Series used:
  - CES0000000001: Total nonfarm employment (thousands, seasonally adjusted)
"""

import csv
import io
import logging

import httpx

from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S

logger = logging.getLogger(__name__)

BLS_BASE = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

NONFARM_SERIES = "CES0000000001"

TERM_YEARS: dict[str, tuple[int, int]] = {
    "clinton-42": (1993, 2001),
    "gwbush-43": (2001, 2009),
    "obama-44":   (2009, 2017),
    "trump-45":   (2017, 2021),
    "biden-46":   (2021, 2025),
    "trump-47":   (2025, 2029),
    # Extended set for economics-only recalculation (Blinder & Watson 2016)
    "eisenhower-34": (1953, 1961),
    "jfk-35":        (1961, 1963),
    "lbj-36":        (1963, 1969),
    "nixon-37":      (1969, 1974),
    "ford-38":       (1974, 1977),
    "carter-39":     (1977, 1981),
    "reagan-40":     (1981, 1989),
    "ghwbush-41":    (1989, 1993),
}

# FRED series for annual real GDP growth (percent change from prior year).
# Source: U.S. Bureau of Economic Analysis via Federal Reserve Bank of
# St. Louis FRED.  Series A191RL1A225NBEA.
# Blinder & Watson (2016, AER 106(4), 1015–1045) use this series as
# the primary measure of presidential economic performance.
FRED_GDP_SERIES = "A191RL1A225NBEA"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


async def fetch_employment_data(
    client: httpx.AsyncClient,
    start_year: int,
    end_year: int,
) -> list[dict] | None:
    """Fetch monthly nonfarm payroll data for a year range.

    BLS limits: 20-year span per request, 25 requests/day without key.
    Returns list of {year, period, value} dicts.
    """
    capped_end = min(end_year, 2026)

    payload = {
        "seriesid": [NONFARM_SERIES],
        "startyear": str(start_year),
        "endyear": str(capped_end),
    }

    try:
        resp = await client.post(
            BLS_BASE,
            json=payload,
            timeout=DEFAULT_FETCH_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "REQUEST_SUCCEEDED":
            logger.warning("BLS request failed: %s", data.get("message"))
            return None

        series = data.get("Results", {}).get("series", [])
        if not series:
            return None

        return series[0].get("data", [])

    except Exception as e:
        logger.warning("BLS fetch failed (%d-%d): %s", start_year, capped_end, e)
        return None


def calculate_jobs_created(
    data: list[dict],
    term_start_year: int,
    term_end_year: int,
) -> float | None:
    """Calculate net jobs created during a presidential term.

    Uses January employment figures at start and end of term.
    Returns millions of jobs.
    """
    jan_values: dict[int, float] = {}
    for entry in data:
        if entry.get("period") == "M01":
            year = int(entry["year"])
            jan_values[year] = float(entry["value"])

    start_val = jan_values.get(term_start_year)
    end_val = jan_values.get(term_end_year)

    if start_val is None or end_val is None:
        return None

    jobs_thousands = end_val - start_val
    return round(jobs_thousands / 1000, 1)


async def fetch_jobs_for_president(
    client: httpx.AsyncClient,
    president_id: str,
) -> float | None:
    """Fetch and calculate jobs created for a single president."""
    term = TERM_YEARS.get(president_id)
    if not term:
        return None

    start_year, end_year = term
    data = await fetch_employment_data(client, start_year, end_year)
    if not data:
        return None

    return calculate_jobs_created(data, start_year, end_year)


async def fetch_gdp_by_year(
    client: httpx.AsyncClient,
) -> dict[int, float] | None:
    """Fetch annual real GDP growth rates from FRED (St. Louis Fed).

    Uses the BEA series A191RL1A225NBEA: 'Real Gross Domestic Product,
    Percent Change from Preceding Period, Annual, Seasonally Adjusted
    Annual Rate.'  Available from 1930 to present; no API key required
    for the CSV endpoint.

    Returns dict mapping calendar year → annual GDP growth (%).
    Returns None on fetch failure.

    Source: Federal Reserve Bank of St. Louis FRED,
      https://fred.stlouisfed.org/series/A191RL1A225NBEA.
    Cited in Blinder & Watson (2016, AER 106(4), 1015–1045) as the
    standard measure of annual presidential economic performance.
    """
    try:
        resp = await client.get(
            FRED_CSV_URL,
            params={"id": FRED_GDP_SERIES},
            headers={"User-Agent": "civitas-data-pipeline/1.0"},
            timeout=15.0,
        )
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        result: dict[int, float] = {}
        for row in reader:
            date_str = row.get("DATE", "")
            val_str = row.get(FRED_GDP_SERIES, "")
            if not date_str or not val_str or val_str == ".":
                continue
            year = int(date_str[:4])
            result[year] = float(val_str)
        return result if result else None
    except Exception as e:
        logger.warning("FRED GDP fetch failed: %s", e)
        return None


def calculate_gdp_adjusted(
    gdp_by_year: dict[int, float],
    term_start_year: int,
    term_end_year: int,
) -> tuple[float | None, float | None]:
    """Compute term-average GDP with and without the first calendar year.

    The first year of a presidential term largely reflects the preceding
    administration's fiscal policy, legislation, and economic conditions.
    Blinder & Watson (2016, AER 106(4), 1015–1045) and Bartels (2008,
    'Unequal Democracy,' Princeton UP, Table 2.1) both exclude or
    down-weight the first year when attributing economic outcomes to the
    sitting president.  The policy transmission lag is approximately
    6–18 months (Romer & Romer 2010, AER 100(3), 763–801).

    Returns:
        (gdp_avg_full, gdp_avg_adjusted) where gdp_avg_adjusted
        excludes the first calendar year.  Either may be None if
        insufficient data is available.
    """
    full_years = range(term_start_year, term_end_year)
    adjusted_years = range(term_start_year + 1, term_end_year)

    full_vals = [gdp_by_year[y] for y in full_years if y in gdp_by_year]
    adj_vals = [gdp_by_year[y] for y in adjusted_years if y in gdp_by_year]

    gdp_avg = round(sum(full_vals) / len(full_vals), 2) if full_vals else None
    gdp_adj = round(sum(adj_vals) / len(adj_vals), 2) if adj_vals else None
    return gdp_avg, gdp_adj


async def fetch_gdp_for_president(
    client: httpx.AsyncClient,
    president_id: str,
    gdp_by_year: dict[int, float],
) -> tuple[float | None, float | None]:
    """Return (gdp_avg_full, gdp_avg_adjusted) for a president.

    Uses pre-fetched gdp_by_year dict to avoid redundant HTTP requests.
    gdp_avg_adjusted excludes year 1 per Blinder & Watson (2016).
    """
    term = TERM_YEARS.get(president_id)
    if not term:
        return None, None
    return calculate_gdp_adjusted(gdp_by_year, term[0], term[1])
