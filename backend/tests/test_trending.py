"""app/trending.py — the traction bar behind ActionIssue's "trending" badge."""

import pytest

from app.trending import compute_trending_issue_ids


@pytest.mark.parametrize("counts, expected", [
    pytest.param({}, set(), id="empty_input_trends_nothing"),
    # The exact scenario that motivated this: one issue has 1 view,
    # everything else has none. Reader feedback: that should never read
    # as "trending" just for being the day's only data point.
    pytest.param({"i1": 1}, set(), id="a_single_view_alone_does_not_trend"),
    pytest.param({"i1": 9}, set(), id="just_below_the_absolute_floor_trends_nothing"),
    pytest.param({"i1": 10}, {"i1"}, id="a_lone_issue_at_the_floor_trends"),
    # All four roughly even — none of them stands out.
    pytest.param({"i1": 12, "i2": 11, "i3": 13, "i4": 12}, set(),
                 id="an_issue_merely_matching_the_pack_does_not_trend"),
    pytest.param({"i1": 100, "i2": 12, "i3": 15, "i4": 11}, {"i1"},
                 id="an_issue_well_above_its_peers_trends"),
    pytest.param({"i1": 100, "i2": 90, "i3": 10, "i4": 12}, {"i1", "i2"},
                 id="multiple_issues_can_trend_at_once"),
    # Floor alone would flag everything here — the relative bar is what
    # keeps a uniformly busy day from marking every issue "trending".
    pytest.param({"i1": 500, "i2": 480, "i3": 510, "i4": 495}, set(),
                 id="high_traffic_day_still_requires_clearing_the_median_multiple"),
])
def test_compute_trending_issue_ids(counts, expected):
    assert compute_trending_issue_ids(counts) == expected
