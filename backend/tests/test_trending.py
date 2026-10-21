"""app/trending.py — ActionIssue's "trending" badge: more readers than an
even share of the day's views would give, by an exact binomial test."""

import pytest

from app.trending import ALPHA, _upper_tail, compute_trending_issue_ids


@pytest.mark.parametrize("counts, live, expected", [
    pytest.param({}, 10, set(), id="no_views_trends_nothing"),
    # One view on one of the day's issues: reader feedback was that this
    # should never read as "trending" just for being the only data point.
    pytest.param({"i1": 1}, 10, set(), id="a_single_view_does_not_trend"),
    # With one issue live there is nothing to stand out from.
    pytest.param({"i1": 500}, 1, set(), id="a_lone_issue_never_trends"),
    pytest.param({"i1": 12, "i2": 11, "i3": 13, "i4": 12}, 4, set(),
                 id="an_issue_merely_matching_the_pack_does_not_trend"),
    pytest.param({"i1": 100, "i2": 12, "i3": 15, "i4": 11}, 4, {"i1"},
                 id="an_issue_well_above_its_peers_trends"),
    pytest.param({"i1": 100, "i2": 90, "i3": 10, "i4": 12}, 4, {"i1", "i2"},
                 id="multiple_issues_can_trend_at_once"),
    # A uniformly busy day marks no issue: each is close to an even share.
    pytest.param({"i1": 500, "i2": 480, "i3": 510, "i4": 495}, 4, set(),
                 id="a_busy_even_day_trends_nothing"),
    # Issues nobody viewed still count toward the even share.
    pytest.param({"i1": 6, "i2": 4, "i3": 5}, 3, set(), id="six_of_fifteen_views_on_one_of_three_is_chance_level"),
    pytest.param({"i1": 6}, 30, {"i1"}, id="six_views_on_one_of_thirty_is_not"),
])
def test_compute_trending_issue_ids(counts, live, expected):
    assert compute_trending_issue_ids(counts, live) == expected


def test_the_tail_is_the_exact_binomial():
    # P(X >= 2) for Binomial(3, 1/2) = 3/8 + 1/8.
    assert _upper_tail(2, 3, 0.5) == pytest.approx(0.5)
    assert _upper_tail(0, 3, 0.5) == 1.0 and _upper_tail(4, 3, 0.5) == 0.0
    # A busy day's counts don't overflow.
    assert 0 < _upper_tail(600, 2000, 0.25) < ALPHA
