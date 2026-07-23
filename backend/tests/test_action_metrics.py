"""Tests for the Action Center's per-run validator counters
(action_metrics.py) — the audit-M9 fix making validator hit-rates
measurable instead of log-only."""

from unittest.mock import patch

from app.pipeline.analyze import action_metrics


class TestCounters:
    def test_increment_and_snapshot(self):
        action_metrics.reset()
        action_metrics.increment("facts_dropped_meta")
        action_metrics.increment("facts_dropped_meta")
        action_metrics.increment("issues_skipped_grounding", 3)
        assert action_metrics.snapshot() == {
            "facts_dropped_meta": 2,
            "issues_skipped_grounding": 3,
        }

    def test_reset_clears(self):
        action_metrics.increment("anything")
        action_metrics.reset()
        assert action_metrics.snapshot() == {}

    def test_persist_writes_api_cache_row(self, db_session):
        from app.pipeline.cache import api_cache_get

        action_metrics.reset()
        action_metrics.increment("facts_dropped_placeholder")
        action_metrics.persist(db_session, "run-2026-07-22-2300")

        cached = api_cache_get(
            db_session, "action-metrics", "run-2026-07-22-2300", max_age_hours=1,
        )
        assert cached == {"counts": {"facts_dropped_placeholder": 1}}

    def test_persist_failure_is_swallowed(self, db_session):
        # A metrics write must never take down the refresh it reports on.
        action_metrics.reset()
        with patch(
            "app.pipeline.cache.api_cache_set", side_effect=RuntimeError("boom"),
        ):
            action_metrics.persist(db_session, "run-x")  # must not raise
