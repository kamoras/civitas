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


class TestEnsureIndexesCreatesPartialUnique:
    def test_partial_unique_index_created(self, monkeypatch):
        import os
        import tempfile

        from sqlalchemy import create_engine, inspect

        from app import database

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        monkeypatch.setattr(database, "engine", eng)
        database.Base.metadata.create_all(bind=eng)

        database._ensure_indexes()

        names = {i["name"] for i in inspect(eng).get_indexes("pipeline_runs")}
        assert "ux_pipeline_runs_one_running" in names


class TestAcquirePipelineLock:
    def test_acquires_when_free_and_blocks_second_caller(self, db_session):
        from app.pipeline.senate_pipeline import _acquire_pipeline_lock

        run = _acquire_pipeline_lock(db_session)
        assert run is not None
        assert run.status == PipelineStatus.RUNNING
        # Second caller sees the running row and yields (early-return path).
        assert _acquire_pipeline_lock(db_session) is None

    def test_integrity_error_on_commit_yields_gracefully(self, db_session, monkeypatch):
        # The race window the DB constraint closes: another container
        # commits its running row between our check and our commit — the
        # acquirer must roll back and yield, not crash the nightly job.
        from sqlalchemy.exc import IntegrityError

        from app.pipeline import senate_pipeline

        real_commit = db_session.commit
        calls = {"n": 0}

        def racing_commit():
            calls["n"] += 1
            if calls["n"] == 1:
                raise IntegrityError("UNIQUE constraint failed", None, Exception())
            return real_commit()

        monkeypatch.setattr(db_session, "commit", racing_commit)
        assert senate_pipeline._acquire_pipeline_lock(db_session) is None


class TestAcquirePipelineLockGeneric:
    """run_tracker.acquire_pipeline_lock (2026-07-23) — the generalized
    version of senate_pipeline's own lock, extended to House/Stock/
    Supplementary, which had no lock at all before this (not even the
    check-then-insert Senate had pre-O15) — confirmed live as the root
    cause of stock-trades data going stale 4+ days and supplementary
    data 1+ day after a since-fixed deploy-race incident killed
    pipelines mid-run, orphaning their DB row at status=running forever.

    Uses HousePipelineRun as the test subject specifically to prove this
    isn't Senate-specific — senate_pipeline._acquire_pipeline_lock's own
    tests above cover the Senate-specific delegation.
    """

    def test_acquires_when_free_and_blocks_second_caller(self, db_session):
        from app.models import HousePipelineRun
        from app.pipeline.run_tracker import acquire_pipeline_lock

        run = acquire_pipeline_lock(db_session, HousePipelineRun, timedelta(hours=12))
        assert run is not None
        assert run.status == PipelineStatus.RUNNING
        assert acquire_pipeline_lock(db_session, HousePipelineRun, timedelta(hours=12)) is None

    def test_stale_row_is_auto_cleared_and_fresh_lock_acquired(self, db_session):
        # The core regression fix: a row orphaned by a killed process
        # (container restart mid-run) must not block this pipeline
        # forever — only Senate had this auto-clear before 2026-07-23.
        from app.models import HousePipelineRun
        from app.pipeline.run_tracker import acquire_pipeline_lock

        stale = HousePipelineRun(started_at=utcnow() - timedelta(hours=13), status=PipelineStatus.RUNNING)
        db_session.add(stale)
        db_session.commit()
        stale_id = stale.id

        run = acquire_pipeline_lock(db_session, HousePipelineRun, timedelta(hours=12))
        assert run is not None
        assert run.id != stale_id

        cleared = db_session.query(HousePipelineRun).filter(HousePipelineRun.id == stale_id).one()
        assert cleared.status == PipelineStatus.STALE
        assert cleared.completed_at is not None

    def test_fresh_row_within_timeout_is_not_cleared_and_blocks(self, db_session):
        from app.models import HousePipelineRun
        from app.pipeline.run_tracker import acquire_pipeline_lock

        fresh = HousePipelineRun(started_at=utcnow() - timedelta(hours=1), status=PipelineStatus.RUNNING)
        db_session.add(fresh)
        db_session.commit()

        assert acquire_pipeline_lock(db_session, HousePipelineRun, timedelta(hours=12)) is None
        unchanged = db_session.query(HousePipelineRun).one()
        assert unchanged.status == PipelineStatus.RUNNING

    def test_integrity_error_on_commit_yields_gracefully(self, db_session, monkeypatch):
        from sqlalchemy.exc import IntegrityError

        from app.models import HousePipelineRun
        from app.pipeline.run_tracker import acquire_pipeline_lock

        real_commit = db_session.commit
        calls = {"n": 0}

        def racing_commit():
            calls["n"] += 1
            if calls["n"] == 1:
                raise IntegrityError("UNIQUE constraint failed", None, Exception())
            return real_commit()

        monkeypatch.setattr(db_session, "commit", racing_commit)
        assert acquire_pipeline_lock(db_session, HousePipelineRun, timedelta(hours=12)) is None


class TestEnsureIndexesCoversAllFourPipelineTables:
    def test_all_four_partial_unique_indexes_created(self, monkeypatch):
        import os
        import tempfile

        from sqlalchemy import create_engine, inspect

        from app import database

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        monkeypatch.setattr(database, "engine", eng)
        database.Base.metadata.create_all(bind=eng)

        database._ensure_indexes()

        expected = {
            "pipeline_runs": "ux_pipeline_runs_one_running",
            "house_pipeline_runs": "ux_house_pipeline_runs_one_running",
            "stock_trades_pipeline_runs": "ux_stock_trades_pipeline_runs_one_running",
            "supplementary_pipeline_runs": "ux_supplementary_pipeline_runs_one_running",
        }
        inspector = inspect(eng)
        for table, index_name in expected.items():
            names = {i["name"] for i in inspector.get_indexes(table)}
            assert index_name in names, f"missing {index_name} on {table}"


class TestRefreshActionIssuesLockWrapper:
    def test_lock_held_skips_run(self, db_session, monkeypatch):
        from app.pipeline.analyze import action_center

        assert _acquire_refresh_lock(db_session) is True  # simulate other container
        called = {"n": 0}
        monkeypatch.setattr(action_center, "_run_refresh", lambda db: called.__setitem__("n", called["n"] + 1) or 99)

        assert action_center.refresh_action_issues(db_session) == 0
        assert called["n"] == 0

    def test_lock_free_runs_and_releases(self, db_session, monkeypatch):
        from app.pipeline.analyze import action_center

        monkeypatch.setattr(action_center, "_run_refresh", lambda db: 7)
        assert action_center.refresh_action_issues(db_session) == 7
        # Released in the finally — immediately reacquirable.
        assert _acquire_refresh_lock(db_session) is True

    def test_release_failure_is_swallowed(self, db_session, monkeypatch):
        # A failed release must not raise out of the refresh — the row
        # expires via the stale window instead.
        def boom(*a, **k):
            raise RuntimeError("db gone")

        monkeypatch.setattr(db_session, "query", boom)
        _release_refresh_lock(db_session)  # must not raise
