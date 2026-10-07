"""Peer-relative GDP growth (president v8, app.pipeline.fetch.peer_gdp)."""

import math
import statistics

from app.pipeline.fetch.peer_gdp import (
    PEER_COUNTRIES,
    US,
    annual_growth,
    bundled_per_capita,
    convergence_rate,
    income_gap,
    peer_relative_growth,
)


def series(start_level, rate, first=1960, last=1990):
    return {y: start_level * (1 + rate / 100) ** (y - first) for y in range(first, last + 1)}


def economies(us_rate, peer_rates):
    return {US: series(100, us_rate), **{c: series(100, r) for c, r in zip(PEER_COUNTRIES, peer_rates)}}


def test_relative_growth_credits_the_years_after_the_first_against_the_peer_median():
    wb = economies(3.0, [1.0 + i * 0.25 for i in range(13)])  # median: the 7th, 2.5%
    levels = {c: {1960: 100.0} for c in wb}
    out = peer_relative_growth(1981, 1985, wb, levels, rate=0.0)
    assert out["years"] == 4  # 1982-1985
    assert abs(out["us"] - 3.0) < 1e-3 and abs(out["peers"] - 2.5) < 1e-3
    assert abs(out["relative"] - 0.5) < 1e-3


def test_the_peers_catch_up_is_set_aside():
    # Peers at half the US's income grow 4% to its 2%.
    wb = economies(2.0, [4.0] * 13)
    levels = {US: {1960: 100.0}, **{c: {1960: 50.0} for c in PEER_COUNTRIES}}
    rate = 6.0
    out = peer_relative_growth(1961, 1965, wb, levels, rate=rate)
    gaps = [income_gap(PEER_COUNTRIES[0], y - 1, wb, levels) for y in range(1962, 1966)]
    assert abs(out["us"] - out["peers"] - -2.0) < 1e-3
    assert abs(out["relative"] - statistics.mean(-2.0 - rate * g for g in gaps)) < 1e-3
    assert out["relative"] > -2.0  # a negative gap and a positive rate credit the US


def test_income_gap_carries_bundled_levels_forward_by_world_bank_growth():
    wb = {US: series(100, 0.0), "GBR": series(100, 10.0)}
    bundled = {US: {1980: 100.0}, "GBR": {1980: 50.0}}
    assert abs(income_gap("GBR", 1980, wb, bundled) - math.log(0.5)) < 1e-9
    assert abs(income_gap("GBR", 1981, wb, bundled) - math.log(0.55)) < 1e-9
    assert income_gap("GBR", 1979, wb, bundled) is None


def test_convergence_rate_recovers_a_planted_slope():
    # US-minus-peers growth = 1 + 6 x the peers' log gap the year before.
    us, peer = 100.0, 50.0
    levels: dict = {c: {} for c in (US, *PEER_COUNTRIES)}
    for year in range(1946, 1991):
        levels[US][year] = us
        for c in PEER_COUNTRIES:
            levels[c][year] = peer
        gap = math.log(peer / us)
        us *= 1.02
        peer *= 1 + (2.0 - (1.0 + 6.0 * gap)) / 100
    assert abs(convergence_rate(levels, levels, 1990) - 6.0) < 1e-6


def test_each_year_comes_from_one_source():
    wb = {US: {1960: 100.0, 1961: 110.0}}
    bundled = {US: {1959: 50.0, 1960: 60.0}}
    assert abs(annual_growth(US, 1961, wb, bundled) - 10.0) < 1e-9  # World Bank
    assert abs(annual_growth(US, 1960, wb, bundled) - 20.0) < 1e-9  # bundled Maddison


def test_too_few_peers_or_no_us_figures_is_none():
    wb = economies(3.0, [2.0] * 7)
    levels = {c: {1960: 100.0} for c in wb}
    assert peer_relative_growth(1981, 1985, wb, levels, rate=0.0) is None
    full = economies(1.0, [1.0] * 13)
    assert peer_relative_growth(2050, 2054, full, {c: {1960: 100.0} for c in full}, rate=0.0) is None


def test_too_few_years_fits_no_rate():
    wb = economies(2.0, [3.0] * 13)
    assert convergence_rate(wb, {c: {1960: 100.0} for c in wb}, 1975) is None  # 15 years


def test_bundled_file_covers_1946_through_the_world_bank_era():
    bundled = bundled_per_capita()
    assert set(bundled) == {US, *PEER_COUNTRIES}
    assert all(min(years) == 1946 and max(years) >= 2020 for years in bundled.values())
