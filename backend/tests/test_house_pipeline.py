"""Tests for run_house_pipeline's lock acquisition (2026-07-23).

Before this, House had no lock at all — an unconditional insert on every
call, so a row orphaned by a killed process (a deploy restarting the
container mid-run) stayed status=running forever, with nothing to clear
it and nothing to stop a later attempt piling a second run on top.
Confirmed live as part of why supplementary/stock-trades data went
stale after a since-fixed deploy-race incident killed pipelines mid-run
(House itself recovered because the NEXT night's Senate run happened to
succeed and the chain reached House fresh, but the underlying gap was
real and applies equally to House).

Only the lock's skip/auto-clear behavior is tested here, not full
pipeline execution — run_house_pipeline has many fetch/analyze phases
with no existing mocked-harness test file, and the lock returns before
any of them run, so testing it doesn't require building one.
"""

from datetime import timedelta
from unittest.mock import patch

from app.models import HousePipelineRun, PipelineStatus
from app.pipeline import house_pipeline
from app.time_utils import utcnow


class TestHousePipelineLock:
    def test_skips_when_a_recent_run_is_already_marked_running(self, db_session):
        db_session.add(HousePipelineRun(started_at=utcnow() - timedelta(minutes=5), status=PipelineStatus.RUNNING))
        db_session.commit()

        with patch("app.pipeline.house_pipeline.SessionLocal", return_value=db_session):
            import asyncio
            result = asyncio.run(house_pipeline.run_house_pipeline())

        assert result == {"status": "skipped", "reason": "already_running"}
        assert db_session.query(HousePipelineRun).count() == 1
        assert house_pipeline.is_house_pipeline_running() is False

    def test_stale_running_row_is_cleared_before_a_fresh_attempt(self, db_session):
        stale = HousePipelineRun(started_at=utcnow() - timedelta(hours=13), status=PipelineStatus.RUNNING)
        db_session.add(stale)
        db_session.commit()
        stale_id = stale.id

        # fetch_representatives (Phase 1) mocked to fail fast rather than
        # hit the real Congress API — only the lock's clear-then-acquire
        # behavior is under test here, not downstream pipeline execution.
        with patch("app.pipeline.house_pipeline.SessionLocal", return_value=db_session), \
             patch("app.pipeline.house_pipeline.fetch_representatives", side_effect=RuntimeError("network mocked off")):
            import asyncio
            try:
                asyncio.run(house_pipeline.run_house_pipeline())
            except Exception:
                pass

        cleared = db_session.query(HousePipelineRun).filter(HousePipelineRun.id == stale_id).one()
        assert cleared.status == PipelineStatus.STALE
        assert db_session.query(HousePipelineRun).count() == 2  # the stale row + a fresh one
