"""Tests for paginate_bounds — shared pagination arithmetic extracted from
senator_service.py / representative_service.py's list and sub-resource
endpoints (previously copy-pasted at 6 call sites)."""

import pytest

from app.services.pagination import paginate_bounds


@pytest.mark.parametrize("total, page, expected_pages, expected_page", [
    pytest.param(95, 3, 10, 3, id="page_within_range_is_unchanged"),
    pytest.param(25, 99, 3, 3, id="page_beyond_total_pages_clamps_down"),
    pytest.param(25, 0, 3, 1, id="page_below_one_clamps_up"),
    pytest.param(0, 1, 1, 1, id="zero_results_still_yields_one_page"),
    pytest.param(20, 1, 2, 1, id="exact_multiple_does_not_add_an_extra_page"),
])
def test_paginate_bounds(total, page, expected_pages, expected_page):
    assert paginate_bounds(total=total, page=page, per_page=10) == (expected_pages, expected_page)
