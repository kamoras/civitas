"""Tests for _calculate_years_in_office/_calculate_house_years — both
compute tenure from the current calendar year, which must come from the
project's canonical UTC clock (app.time_utils.utcnow), not a local-
timezone-dependent datetime.now()/date.today() call (2026-07-23
timezone-consistency pass).
"""

from unittest.mock import patch

import pytest

from app.pipeline.transform.normalize_members import (
    _calculate_house_years,
    _calculate_years_in_office,
)


@pytest.mark.parametrize("calculate, member, expected", [
    pytest.param(
        _calculate_years_in_office,
        {"terms": {"item": [{"chamber": "Senate", "startYear": 2015}]}},
        11, id="senate_from_earliest_term_start_year",
    ),
    pytest.param(
        _calculate_years_in_office,
        {"terms": {"item": [
            {"chamber": "Senate", "startYear": 2021},
            {"chamber": "Senate", "startYear": 2009},
        ]}},
        17, id="senate_uses_earliest_of_multiple_terms",
    ),
    pytest.param(
        _calculate_years_in_office,
        {"depiction": {"attribution": "Senator since 2003"}},
        23, id="senate_falls_back_to_attribution_since_year",
    ),
    pytest.param(
        _calculate_house_years,
        {"terms": {"item": [{"chamber": "House of Representatives", "startYear": 2019}]}},
        7, id="house_from_earliest_term_start_year",
    ),
    pytest.param(
        _calculate_house_years,
        {"depiction": {"attribution": "Representative since 2017"}},
        9, id="house_falls_back_to_attribution_since_year",
    ),
])
def test_tenure_counts_from_the_utc_clock(calculate, member, expected):
    with patch("app.pipeline.transform.normalize_members.utcnow") as mock_utcnow:
        mock_utcnow.return_value.year = 2026
        assert calculate(member, {}) == expected


@pytest.mark.parametrize("calculate", [_calculate_years_in_office, _calculate_house_years])
def test_returns_zero_when_nothing_resolves(calculate):
    assert calculate({}, {}) == 0
