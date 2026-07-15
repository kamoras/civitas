"""Tests for paginate_bounds — shared pagination arithmetic extracted from
senator_service.py / representative_service.py's list and sub-resource
endpoints (previously copy-pasted at 6 call sites)."""

from app.services.pagination import paginate_bounds


def test_page_within_range_is_unchanged():
    total_pages, page = paginate_bounds(total=95, page=3, per_page=10)
    assert total_pages == 10
    assert page == 3


def test_page_beyond_total_pages_clamps_down():
    total_pages, page = paginate_bounds(total=25, page=99, per_page=10)
    assert total_pages == 3
    assert page == 3


def test_page_below_one_clamps_up():
    total_pages, page = paginate_bounds(total=25, page=0, per_page=10)
    assert total_pages == 3
    assert page == 1


def test_zero_results_still_yields_one_page():
    total_pages, page = paginate_bounds(total=0, page=1, per_page=10)
    assert total_pages == 1
    assert page == 1


def test_exact_multiple_does_not_add_an_extra_page():
    total_pages, page = paginate_bounds(total=20, page=1, per_page=10)
    assert total_pages == 2
