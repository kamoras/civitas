"""Tests for historical_gdp.py's peak-relative CAGR fix.

Averaging YoY growth during a term that begins mid-recession-rebound
rewards the arithmetic of recovering off a depressed base, not managed
prosperity — confirmed against real MeasuringWorth data (2026-07):
Harding's 1921-23 term and FDR's 1933-45 term produced nearly identical
average YoY growth (9.36% / 9.19%) despite very different real stories (a
mild ~3% dip vs. the ~26% Depression collapse). Peak-relative CAGR fixes
this without touching the normal (no-recent-contraction) case.
"""

from app.pipeline.fetch.historical_gdp import compute_term_gdp_growth


class TestComputeTermGdpGrowth:
    def test_no_recent_contraction_uses_plain_average(self):
        # Steady 2%/year growth, no dip in the lookback window.
        gdp = {y: 100 * (1.02 ** (y - 2000)) for y in range(1995, 2010)}
        growth = compute_term_gdp_growth(gdp, 2001, 2005)
        assert growth is not None
        assert 1.5 <= growth <= 2.5

    def test_term_starting_mid_recession_rebound_uses_peak_relative_cagr(self):
        # Peak at year 10 (1000), crashes 30% by year 11, "recovers" with
        # explosive YoY growth through year 13 without ever regaining the
        # year-10 peak.
        gdp = {10: 1000.0, 11: 700.0, 12: 800.0, 13: 900.0}
        growth = compute_term_gdp_growth(gdp, 11, 13)
        # Plain YoY average would be ((800-700)/700 + (900-800)/800)/2*100
        # = (14.3 + 12.5)/2 = ~13.4%, crediting the rebound's own
        # arithmetic. Peak-relative CAGR (1000 -> 900 over 3 years) is
        # negative: the term never regained the pre-crash peak.
        assert growth is not None
        assert growth < 0

    def test_term_fully_within_a_decline_is_unaffected(self):
        # No rebound inside the term at all — Hoover's real shape (GDP
        # falls every year of the term, no recovery to measure yet).
        gdp = {y: v for y, v in zip(range(1929, 1934), [1191100, 1089800, 1020000, 888400, 877400])}
        growth = compute_term_gdp_growth(gdp, 1929, 1933)
        assert growth is not None
        assert growth < 0

    def test_missing_term_end_year_falls_back_to_latest_available(self):
        gdp = {10: 1000.0, 11: 700.0, 12: 800.0, 13: 900.0}
        # term_end_year=15 isn't in the series; should fall back to 13.
        growth = compute_term_gdp_growth(gdp, 11, 15)
        assert growth is not None

    def test_short_term_without_enough_years_returns_none(self):
        gdp = {2020: 100.0, 2021: 105.0}
        growth = compute_term_gdp_growth(gdp, 2021, 2021)
        assert growth is None
