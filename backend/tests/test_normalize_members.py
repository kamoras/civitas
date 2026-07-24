"""Tests for _calculate_years_in_office/_calculate_house_years — both
compute tenure from the current calendar year, which must come from the
project's canonical UTC clock (app.time_utils.utcnow), not a local-
timezone-dependent datetime.now()/date.today() call (2026-07-23
timezone-consistency pass).
"""

from unittest.mock import patch

from app.pipeline.transform.normalize_members import (
    _calculate_house_years,
    _calculate_years_in_office,
)


class TestCalculateYearsInOffice:
    def test_computes_from_earliest_senate_term_start_year(self):
        member = {"terms": {"item": [{"chamber": "Senate", "startYear": 2015}]}}
        with patch("app.pipeline.transform.normalize_members.utcnow") as mock_utcnow:
            mock_utcnow.return_value.year = 2026
            assert _calculate_years_in_office(member, {}) == 11

    def test_uses_earliest_of_multiple_senate_terms(self):
        member = {
            "terms": {"item": [
                {"chamber": "Senate", "startYear": 2021},
                {"chamber": "Senate", "startYear": 2009},
            ]},
        }
        with patch("app.pipeline.transform.normalize_members.utcnow") as mock_utcnow:
            mock_utcnow.return_value.year = 2026
            assert _calculate_years_in_office(member, {}) == 17

    def test_falls_back_to_attribution_since_year_when_no_terms(self):
        member = {"depiction": {"attribution": "Senator since 2003"}}
        with patch("app.pipeline.transform.normalize_members.utcnow") as mock_utcnow:
            mock_utcnow.return_value.year = 2026
            assert _calculate_years_in_office(member, {}) == 23

    def test_returns_zero_when_nothing_resolves(self):
        assert _calculate_years_in_office({}, {}) == 0


class TestCalculateHouseYears:
    def test_computes_from_earliest_house_term_start_year(self):
        member = {"terms": {"item": [{"chamber": "House of Representatives", "startYear": 2019}]}}
        with patch("app.pipeline.transform.normalize_members.utcnow") as mock_utcnow:
            mock_utcnow.return_value.year = 2026
            assert _calculate_house_years(member, {}) == 7

    def test_falls_back_to_attribution_since_year_when_no_terms(self):
        member = {"depiction": {"attribution": "Representative since 2017"}}
        with patch("app.pipeline.transform.normalize_members.utcnow") as mock_utcnow:
            mock_utcnow.return_value.year = 2026
            assert _calculate_house_years(member, {}) == 9

    def test_returns_zero_when_nothing_resolves(self):
        assert _calculate_house_years({}, {}) == 0
