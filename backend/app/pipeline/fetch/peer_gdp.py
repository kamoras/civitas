"""Real GDP per person for the US and 13 peer economies, so a president's
growth can be compared with the economies that faced the same world.

Since 1961, 68% of the variation in a presidency's per-person growth is
shared with the median of these 13 economies over the same years, and the
Democratic-Republican growth gap disappears relative to them (World Bank
data, docs/research/president-scores.md; Blinder & Watson 2016, AER
106(4), on oil shocks, productivity and world growth). Effectiveness
scores US growth relative to the peers, which removes those shared shocks
(president v8).

Catch-up growth is set aside first. A poorer economy grows faster by
catching up with a richer one (beta convergence: Baumol 1986, AER 76(5);
Barro & Sala-i-Martin 1992, JPE 100(2)), and in the 1950s and 1960s
Western Europe and Japan were far poorer than the US, so their faster
growth then was not a shock the US shared. Raw, it made a postwar
presidency's relative growth rise with its start year (r = +0.73 over the
13 completed postwar terms). Each year's US-minus-peers growth is set
against the peers' median income gap with the US the year before, the
slope fitted every run over every year since 1947 (convergence_rate, 8.9
points per log gap in October 2026), and the part that gap predicts is
taken off. That brings the era trend to +0.41; what is left comes from
the three terms since 2017, the years the US outgrew Europe and Japan.
A slope fitted across peer-years instead (each peer's growth on its own
gap) moved from -3.3 to -5.3 with the sample and over-credited every
recent term, and one fitted per term followed noise
(docs/research/president-scores.md).

Two sources, one per span, never mixed within a year:
  - Through its last year (2022 in the 2023 release): the Maddison Project
    Database (GDP per capita, 2011 international $, at purchasing-power
    parity), in app/data/peer_gdp_per_capita.json, written by
    scripts/fetch_peer_gdp.py. History; it changes only with a release.
  - After it: the World Bank's NY.GDP.PCAP.KD (constant 2015 US$), live,
    carrying the Maddison levels forward by its growth.
The income gap needs levels at purchasing-power parity, which only
Maddison has back to 1946, and taking growth from the same series keeps
the gap and the growth it is fitted against consistent: with World Bank
growth from 1961 and Maddison levels the fitted rate came out 11.5 rather
than 7.5. The two series' growth rates agree closely where both exist
(r = 0.90 over 868 country-years, 1961-2022, mean difference 0.16
points), which is what the World Bank's few most recent years rest on.
"""

import json
import logging
import math
import pathlib
import statistics

import httpx
from sqlalchemy.orm import Session

from app.contact import BOT_USER_AGENT
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S

logger = logging.getLogger(__name__)

# The same 13 advanced economies as the research note: Britain, France,
# Germany, the Netherlands, Canada, Australia, Sweden, Italy, Belgium,
# Denmark, Switzerland, Norway, Japan.
PEER_COUNTRIES = ("GBR", "FRA", "DEU", "NLD", "CAN", "AUS", "SWE", "ITA", "BEL", "DNK", "CHE", "NOR", "JPN")
US = "USA"
# Fewest peers with a growth rate over a window before their median is used.
_MIN_PEERS = 8
# The first year with a bundled level, so the first growth year the
# convergence rate is fitted on is the next.
_FIRST_GAP_YEAR = 1946
# Fewest years before the convergence rate is trusted. A run that can't fit
# one keeps the stored figures.
_MIN_CONVERGENCE_YEARS = 30

_WB_URL = (
    "https://api.worldbank.org/v2/country/{countries}/indicator/NY.GDP.PCAP.KD"
    "?format=json&date={first}:{last}&per_page=2000"
)
_BUNDLED = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "peer_gdp_per_capita.json"
_CACHE_TIER = "world_bank_gdp_per_capita"
_CACHE_TTL_HOURS = 24 * 30  # annual figures, revised a few times a year


def bundled_per_capita() -> dict[str, dict[int, float]]:
    """{country: {year: GDP per person}} from the bundled Maddison file."""
    raw = json.loads(_BUNDLED.read_text())
    return {c: {int(y): float(v) for y, v in years.items()} for c, years in raw["countries"].items()}


async def fetch_world_bank_per_capita(
    client: httpx.AsyncClient, db: Session, first_year: int, last_year: int,
) -> dict[str, dict[int, float]] | None:
    """{country: {year: GDP per person}} from the World Bank, `first_year`
    to `last_year`, for the US and every peer. None when it can't be
    fetched (an outage is not "no data": the caller keeps the stored
    values)."""
    countries = ";".join((US, *PEER_COUNTRIES))
    url = _WB_URL.format(countries=countries, first=first_year, last=last_year)
    cached = api_cache_get(db, _CACHE_TIER, url, max_age_hours=_CACHE_TTL_HOURS)
    if cached is None:
        try:
            resp = await client.get(url, headers={"User-Agent": BOT_USER_AGENT}, timeout=DEFAULT_FETCH_TIMEOUT_S)
            resp.raise_for_status()
            body = resp.json()
            rows = body[1] if isinstance(body, list) and len(body) > 1 and body[1] else None
        except Exception:
            logger.warning("World Bank GDP per capita fetch failed", exc_info=True)
            return None
        if not rows:
            logger.warning("World Bank GDP per capita: no rows in the response")
            return None
        cached = [
            {"c": r["countryiso3code"], "y": int(r["date"]), "v": r["value"]}
            for r in rows if r.get("value") is not None
        ]
        api_cache_set(db, _CACHE_TIER, url, cached, normal_ttl_hours=_CACHE_TTL_HOURS)
    out: dict[str, dict[int, float]] = {}
    for r in cached:
        out.setdefault(r["c"], {})[int(r["y"])] = float(r["v"])
    return out


def bundled_last_year(bundled: dict) -> int:
    """The last year every economy has in the bundled file."""
    return min(max(years) for years in bundled.values())


def annual_growth(country: str, year: int, world_bank: dict, bundled: dict) -> float | None:
    """Real GDP per person growth into `year` (%), from the bundled Maddison
    series through its last year and the World Bank after, never one year's
    level against the other source's."""
    source = bundled if year <= bundled_last_year(bundled) else world_bank
    series = source.get(country) or {}
    prev, cur = series.get(year - 1), series.get(year)
    if not prev or cur is None:
        return None
    return (cur / prev - 1) * 100


def income_gap(country: str, year: int, world_bank: dict, bundled: dict) -> float | None:
    """log(country's GDP per person / the US's) at PPP in `year`: the
    bundled Maddison levels, carried past their last year by each
    economy's World Bank growth."""
    def level(c: str) -> float | None:
        series = bundled.get(c) or {}
        if year in series:
            return series[year]
        last = max(series) if series else None
        if last is None or year < last:
            return None
        value = series[last]
        for y in range(last + 1, year + 1):
            g = annual_growth(c, y, world_bank, bundled)
            if g is None:
                return None
            value *= 1 + g / 100
        return value

    mine, us = level(country), level(US)
    return math.log(mine / us) if mine and us else None


def _peer_median_growth(year: int, world_bank: dict, bundled: dict) -> float | None:
    rates = [g for g in (annual_growth(c, year, world_bank, bundled) for c in PEER_COUNTRIES) if g is not None]
    return statistics.median(rates) if len(rates) >= _MIN_PEERS else None


def _peer_median_gap(year: int, world_bank: dict, bundled: dict) -> float | None:
    gaps = [g for g in (income_gap(c, year, world_bank, bundled) for c in PEER_COUNTRIES) if g is not None]
    return statistics.median(gaps) if len(gaps) >= _MIN_PEERS else None


def _year(year: int, world_bank: dict, bundled: dict) -> tuple[float, float, float] | None:
    """(US growth, peers' median growth, peers' median income gap the year
    before) for one year, or None without all three."""
    us = annual_growth(US, year, world_bank, bundled)
    peers = _peer_median_growth(year, world_bank, bundled)
    gap = _peer_median_gap(year - 1, world_bank, bundled)
    return None if us is None or peers is None or gap is None else (us, peers, gap)


def convergence_rate(world_bank: dict, bundled: dict, last_year: int) -> float | None:
    """Points of US-minus-peers growth per unit of the peers' median log
    income gap with the US the year before, over every year from 1947 to
    `last_year`: the Theil-Sen slope, the median of the slopes between
    every pair of years (Theil 1950; Sen 1968, JASA 63(324)). Positive
    while the peers are poorer and catching up (a negative gap lowers the
    US's relative growth). None with fewer than _MIN_CONVERGENCE_YEARS
    years.

    Not least squares: single years of postwar reconstruction move a
    least-squares slope from 5.6 to 10.2 depending on the year the fit
    starts (1947-1955); the median of pairwise slopes stays within 7.1 to
    9.9, and every rate in that range leaves the era trend near zero."""
    points = [p for p in (_year(y, world_bank, bundled) for y in range(_FIRST_GAP_YEAR + 1, last_year + 1)) if p]
    if len(points) < _MIN_CONVERGENCE_YEARS:
        return None
    slopes = [
        ((a[0] - a[1]) - (b[0] - b[1])) / (a[2] - b[2])
        for i, a in enumerate(points) for b in points[i + 1:] if a[2] != b[2]
    ]
    return statistics.median(slopes) if slopes else None


def peer_relative_growth(
    start_year: int, end_year: int, world_bank: dict, bundled: dict, rate: float,
) -> dict | None:
    """{"us", "peers", "relative", "years"} over the years a term is
    credited with: average annual US growth per person, the peers' median
    growth averaged over the same years, and US minus peers with the part
    the peers' catch-up predicts taken off (rate x the year's median income
    gap, convergence_rate). The years are the ones Effectiveness
    already credits (historical_gdp.compute_term_gdp_growth's standard
    window): the term's second calendar year through its last that has
    figures, the first reflecting the predecessor (Blinder & Watson's lag),
    or the first year for a term with no other published yet. None unless
    every one of those years has the US, enough peers and their gap."""
    credited = [y for y in range(start_year + 1, end_year + 1) if annual_growth(US, y, world_bank, bundled) is not None]
    # A term with no credited year published yet (a sitting president's
    # second year still running) uses its first, as compute_term_gdp_growth
    # does for a term too short to drop one.
    years = credited or [start_year]
    points = [_year(y, world_bank, bundled) for y in years]
    if not all(points):
        return None
    us = statistics.mean(p[0] for p in points)
    peers = statistics.mean(p[1] for p in points)
    relative = statistics.mean(p[0] - p[1] - rate * p[2] for p in points)
    return {"us": round(us, 4), "peers": round(peers, 4), "relative": round(relative, 4), "years": len(years)}
