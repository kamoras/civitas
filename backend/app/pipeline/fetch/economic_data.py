"""Fetch economic data from BLS public API.

BLS public API (api.bls.gov) requires no key for basic access (25 queries/day).
Series used:
  - CES0000000001: Total nonfarm employment (thousands, seasonally adjusted)
"""

import logging

import httpx

from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S
from app.time_utils import utcnow

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


async def fetch_employment_data(
    client: httpx.AsyncClient,
    start_year: int,
    end_year: int,
) -> list[dict] | None:
    """Fetch monthly nonfarm payroll data for a year range.

    BLS limits: 20-year span per request, 25 requests/day without key.
    Returns list of {year, period, value} dicts.
    """
    # Cap at the current year (BLS has no future data), never at a
    # hardcoded year — a fixed cap silently froze the payroll series for
    # any in-progress term once the calendar passed it.
    capped_end = min(end_year, utcnow().year)

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
    """Calculate net jobs attributed to a presidential term, in millions.

    Baseline is January of the term's SECOND calendar year, not
    inauguration January (2026-07 fix): the GDP
    component of the same Effectiveness score excludes year 1 per
    Blinder & Watson (2016) — outcomes in a president's first year
    primarily reflect the predecessor's policy — but jobs were counted
    from inauguration month, so the two components of one score used
    opposite attribution rules. Both now start the attribution clock at
    the same point. Endpoint stays January of the term-end year
    (matching calculate_gdp_adjusted, which also does not extend into
    the successor's lag window — conservative and symmetric with the
    GDP series' shape).

    For a term still in progress there is no term-end January yet — the
    old code returned None, silently scoring the incumbent on a
    different basis (no jobs component) than every completed term in
    the same ranking (the other half of that same attribution fix). The endpoint now falls
    back to the latest available month in the series: the headline
    nonfarm series (CES0000000001) is seasonally adjusted, so a
    non-January endpoint is comparable, and the per-year normalization
    in the scorer already uses elapsed (not full) term years for an
    in-progress term.
    """
    jan_values: dict[int, float] = {}
    latest_val: float | None = None
    latest_key: tuple[int, int] = (0, 0)
    for entry in data:
        period = entry.get("period", "")
        if not period.startswith("M"):
            continue
        year = int(entry["year"])
        month = int(period[1:])
        if period == "M01":
            jan_values[year] = float(entry["value"])
        if (year, month) > latest_key:
            latest_key = (year, month)
            latest_val = float(entry["value"])

    baseline_year = term_start_year + 1
    start_val = jan_values.get(baseline_year)
    end_val = jan_values.get(term_end_year)
    if end_val is None:
        # In-progress term (or a series gap at term end): use the latest
        # available month, provided it is at or past the baseline.
        if latest_val is not None and latest_key >= (baseline_year, 1):
            end_val = latest_val

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


