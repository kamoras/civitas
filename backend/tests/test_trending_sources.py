"""A trending source that FAILED must not look like one with nothing to say.

Reddit 403'd on every production run for an unknown stretch while
fetch_trending_topics logged only a cheerful total — it was contributing
0 of 30 topics and looked identical to a quiet source. Reddit itself is
retired (it needs OAuth now and the platform has no credentials); what
these tests protect is the distinction that made the outage invisible.
"""

import logging
from unittest.mock import patch

from app.pipeline.fetch import trending
from app.pipeline.fetch.trending import TrendingTopic, fetch_trending_topics


def _topic(title: str) -> TrendingTopic:
    return TrendingTopic(title=title, source="test", traffic_score=1.0)


class TestAFailedSourceIsReportedAsFailed:
    def test_a_failing_source_logs_an_error_naming_it(self, caplog):
        with patch.object(trending, "_fetch_google_trends", return_value=None), \
             patch.object(trending, "_fetch_bluesky_trending", return_value=[_topic("x")]), \
             caplog.at_level(logging.ERROR):
            fetch_trending_topics()
        assert "google_trends" in caplog.text
        assert "FAILED" in caplog.text
        # The distinction itself, spelled out for whoever reads the log.
        assert "not empty" in caplog.text

    def test_a_source_that_succeeds_with_nothing_is_not_an_error(self, caplog):
        """[] is a real answer — the source was reachable and had nothing."""
        with patch.object(trending, "_fetch_google_trends", return_value=[]), \
             patch.object(trending, "_fetch_bluesky_trending", return_value=[]), \
             caplog.at_level(logging.ERROR):
            topics = fetch_trending_topics()
        assert topics == []
        assert "FAILED" not in caplog.text

    def test_every_source_failing_still_returns_a_list(self, caplog):
        """Ranking degrades rather than crashing — but it is recorded."""
        with patch.object(trending, "_fetch_google_trends", return_value=None), \
             patch.object(trending, "_fetch_bluesky_trending", return_value=None), \
             caplog.at_level(logging.ERROR):
            topics = fetch_trending_topics()
        assert topics == []
        assert "FAILED" in caplog.text

    def test_failures_are_counted_on_action_metrics(self):
        from app.pipeline.analyze import action_metrics

        with patch.object(trending, "_fetch_google_trends", return_value=None), \
             patch.object(trending, "_fetch_bluesky_trending", return_value=[_topic("x")]), \
             patch.object(action_metrics, "increment") as inc:
            fetch_trending_topics()
        keys = [c.args[0] for c in inc.call_args_list]
        assert "trending_source_failed_google_trends" in keys
        assert "trending_topics_bluesky" in keys


class TestRedditIsRetired:
    def test_no_reddit_fetcher_remains(self):
        """It needs OAuth the platform does not have, and an endpoint that
        always 403s is not a source. Verified from the production host:
        403 on every configured subreddit, with and without a custom
        User-Agent."""
        assert not hasattr(trending, "_fetch_reddit_trending")
        assert not hasattr(trending, "_REDDIT_SUBREDDITS")

    def test_the_module_no_longer_advertises_reddit_as_a_live_source(self):
        sources = trending.__doc__.split("Reddit was a third source")[0]
        assert "reddit" not in sources.lower()
