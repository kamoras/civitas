"""Tests for the two cross-container locks (2026-07, platform-review O15).

Both were check-then-insert races before: two containers overlapping
during a blue/green deploy could each start a full nightly pipeline, or
each run the hourly action refresh (duplicate Bluesky posts, contended
SQLite writes). Both locks are now enforced by the database itself — a
partial UNIQUE index for pipeline_runs, api_cache's (tier, cache_key)
PRIMARY KEY for the refresh — so acquisition is the INSERT, not a check
racing ahead of one.
"""

from datetime import timedelta

from sqlalchemy import text

from app.models import ApiCache, PipelineRun, PipelineStatus
from app.pipeline.analyze.action_center import (
    _REFRESH_LOCK_STALE_S,
    _acquire_refresh_lock,
    _release_refresh_lock,
)
from app.time_utils import utcnow


def _create_partial_unique_index(session) -> None:
    # The db_session fixture builds the schema via create_all, which
    # doesn't include hand-rolled migration indexes — apply the same SQL
    # database._ensure_indexes runs in production.
    session.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_pipeline_runs_one_running "
        "ON pipeline_runs (status) WHERE status = 'running'"
    ))
    session.commit()


class TestPipelineRunUniqueRunning:
    def test_second_running_row_is_rejected_by_the_database(self, db_session):
        from sqlalchemy.exc import IntegrityError

        _create_partial_unique_index(db_session)
        db_session.add(PipelineRun(started_at=utcnow(), status=PipelineStatus.RUNNING))
        db_session.commit()

        db_session.add(PipelineRun(started_at=utcnow(), status=PipelineStatus.RUNNING))
        try:
            db_session.commit()
            raised = False
        except IntegrityError:
            db_session.rollback()
            raised = True
        assert raised

    def test_completed_history_rows_are_unaffected(self, db_session):
        _create_partial_unique_index(db_session)
        for _ in range(3):
            db_session.add(PipelineRun(
                started_at=utcnow(), status=PipelineStatus.COMPLETED,
            ))
        db_session.commit()  # must not raise — the index is partial
        assert db_session.query(PipelineRun).count() == 3


class TestActionRefreshLock:
    def test_lock_is_exclusive(self, db_session):
        assert _acquire_refresh_lock(db_session) is True
        assert _acquire_refresh_lock(db_session) is False  # held

    def test_release_allows_reacquire(self, db_session):
        assert _acquire_refresh_lock(db_session) is True
        _release_refresh_lock(db_session)
        assert _acquire_refresh_lock(db_session) is True

    def test_stale_lock_is_taken_over(self, db_session):
        # A crashed container never deletes its row — a holder older than
        # the stale window must not block refreshes forever.
        db_session.add(ApiCache(
            tier="action-refresh-lock", cache_key="lock", data_json="{}",
            cached_at=utcnow() - timedelta(seconds=_REFRESH_LOCK_STALE_S + 60),
        ))
        db_session.commit()
        assert _acquire_refresh_lock(db_session) is True

    def test_fresh_lock_is_not_taken_over(self, db_session):
        db_session.add(ApiCache(
            tier="action-refresh-lock", cache_key="lock", data_json="{}",
            cached_at=utcnow() - timedelta(seconds=60),
        ))
        db_session.commit()
        assert _acquire_refresh_lock(db_session) is False
