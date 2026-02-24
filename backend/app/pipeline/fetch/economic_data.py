"""Fetch economic data from BLS public API.

BLS public API (api.bls.gov) requires no key for basic access (25 queries/day).
Series used:
  - CES0000000001: Total nonfarm employment (thousands, seasonally adjusted)
"""

import logging

import httpx

logger = logging.getLogger(__name__)

BLS_BASE = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

NONFARM_SERIES = "CES0000000001"

TERM_YEARS: dict[str, tuple[int, int]] = {
    "clinton-42": (1993, 2001),
    "gwbush-43": (2001, 2009),
    "obama-44": (2009, 2017),
    "trump-45": (2017, 2021),
    "biden-46": (2021, 2025),
    "trump-47": (2025, 2029),
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
            timeout=30.0,
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
