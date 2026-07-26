"""Tests for app.config's computed defaults."""

import datetime

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

    def test_matches_pipeline_formula_across_years(self):
        for year in (2025, 2026, 2027, 2028, 2033):
            assert congress_for_year(year) == 1 + (year - 1789) // 2

    def test_returns_119_for_2026(self):
        assert congress_for_year(2026) == 119

    def test_returns_120_for_2027(self):
        assert congress_for_year(2027) == 120
