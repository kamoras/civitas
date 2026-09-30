"""Tests for app.config's computed defaults."""

import datetime

import pytest

from app.config import _default_current_congress
from app.pipeline.fetch.congress import congress_for_year


class TestDefaultCurrentCongress:
    """CURRENT_CONGRESS used to be a hardcoded literal (119) that only a
    separate ops alert could catch going stale after a new Congress
    convened. Now computed from the wall clock so it never needs a manual
    bump — this just confirms the inlined formula (kept import-free to
    avoid pulling pipeline code into config at settings-module load time)
    stays in lockstep with the pipeline's own congress_for_year."""

    def test_matches_pipeline_formula_for_current_year(self):
        year = datetime.date.today().year
        assert _default_current_congress() == congress_for_year(year)

    @pytest.mark.parametrize("year, congress", [
        (2025, 119), (2026, 119), (2027, 120), (2028, 120), (2033, 123),
    ])
    def test_pipeline_formula_across_years(self, year, congress):
        assert congress_for_year(year) == congress

    def test_the_new_congress_starts_when_it_convenes(self):
        # January 3 of an odd year (20th Amendment), not January 1: a day
        # early, every scored window would point at a Congress with no bills.
        assert _default_current_congress(datetime.date(2027, 1, 2)) == 119
        assert _default_current_congress(datetime.date(2027, 1, 3)) == 120
        assert _default_current_congress(datetime.date(2026, 1, 1)) == 119

    def test_the_staleness_check_agrees_on_the_convening_day(self):
        from app.pipeline.fetch.congress import expected_current_congress
        for day in (datetime.date(2027, 1, 2), datetime.date(2027, 1, 3), datetime.date(2026, 6, 1)):
            now = datetime.datetime(day.year, day.month, day.day, 12)
            assert expected_current_congress(now) == _default_current_congress(day)
