"""app/trending.py — the traction bar behind ActionIssue's "trending" badge."""

from app.trending import compute_trending_issue_ids


class TestComputeTrendingIssueIds:
    def test_empty_input_trends_nothing(self):
        assert compute_trending_issue_ids({}) == set()

    def test_a_single_view_alone_does_not_trend(self):
        """The exact scenario that motivated this: one issue has 1 view,
        everything else has none. Reader feedback: that should never read
        as "trending" just for being the day's only data point."""
        assert compute_trending_issue_ids({"i1": 1}) == set()

    def test_clears_the_absolute_floor_but_stays_below_it_trends_nothing(self):
        assert compute_trending_issue_ids({"i1": 9}) == set()

    def test_a_lone_issue_above_the_floor_trends(self):
        assert compute_trending_issue_ids({"i1": 10}) == {"i1"}

    def test_an_issue_merely_matching_the_pack_does_not_trend(self):
        # All four roughly even — none of them stands out.
        counts = {"i1": 12, "i2": 11, "i3": 13, "i4": 12}
        assert compute_trending_issue_ids(counts) == set()

    def test_an_issue_well_above_its_peers_trends(self):
        counts = {"i1": 100, "i2": 12, "i3": 15, "i4": 11}
        assert compute_trending_issue_ids(counts) == {"i1"}

    def test_multiple_issues_can_trend_at_once(self):
        counts = {"i1": 100, "i2": 90, "i3": 10, "i4": 12}
        assert compute_trending_issue_ids(counts) == {"i1", "i2"}

    def test_high_traffic_day_still_requires_clearing_the_median_multiple(self):
        # Floor alone would flag everything here — the relative bar is what
        # keeps a uniformly busy day from marking every issue "trending".
        counts = {"i1": 500, "i2": 480, "i3": 510, "i4": 495}
        assert compute_trending_issue_ids(counts) == set()
