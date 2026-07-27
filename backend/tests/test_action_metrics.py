"""Tests for the Action Center's per-run validator counters
(action_metrics.py) — the audit-M9 fix making validator hit-rates
measurable instead of log-only."""

import json
from unittest.mock import patch

# Registers every table on Base before conftest's db_session fixture calls
# create_all. Without it this module's only model reference is an inline
# import inside a test body — too late for that test's own fixture — so the
# api_cache assertions here passed or failed purely on whether some earlier
# test in the session happened to import app.models first.
from app import models  # noqa: F401
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


class TestPersistMetricsHelper:
    """_persist_metrics is the shared exit-path writer in action_center.

    It exists so the two early aborts (nothing fetched, nothing
    policy-relevant) leave a record: those runs return before the tail
    persist, so a broken feed and a genuinely quiet hour were both an
    absent row and could not be told apart after the fact.
    """

    def test_writes_current_counters_under_a_run_key(self, db_session):
        from app.models import ApiCache
        from app.pipeline.analyze.action_center import _persist_metrics

        action_metrics.reset()
        action_metrics.increment("refresh_aborted_no_articles")

        returned = _persist_metrics(db_session)

        assert returned == {"refresh_aborted_no_articles": 1}
        row = db_session.query(ApiCache).filter(ApiCache.tier == "action-metrics").one()
        assert row.cache_key.startswith("run-")
        assert json.loads(row.data_json)["counts"] == {"refresh_aborted_no_articles": 1}

    def test_survives_a_failing_write(self, db_session):
        # Same posture as persist() itself: reporting on a refresh must
        # never be what takes the refresh down.
        from app.pipeline.analyze.action_center import _persist_metrics

        action_metrics.reset()
        action_metrics.increment("articles_fetched", 3)
        with patch("app.pipeline.cache.api_cache_set", side_effect=RuntimeError("boom")):
            assert _persist_metrics(db_session) == {"articles_fetched": 3}
