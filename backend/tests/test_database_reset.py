"""Tests for reset_all_data() and the admin endpoint that runs it.

reset_vector_db() itself (sqlite-vec) is tested in
test_vector_store_sqlitevec.py; here only reset_all_data()'s bookkeeping
around that call is (its vector_db_* summary keys were chromadb_* before
the 2026-07 migration cleanup).
"""

from unittest.mock import patch

import pytest

from app import models  # noqa: F401 — registers all Base subclasses before db_session's create_all()
from app.database import reset_all_data


class TestResetAllDataVectorStoreSummary:
    def test_records_vector_db_collections_on_success(self, db_session, monkeypatch):
        monkeypatch.setattr("app.database.SessionLocal", lambda: db_session)
        with patch("app.pipeline.vector_store.reset_vector_db"):
            summary = reset_all_data()
        assert summary["vector_db_collections"] == 2
        assert "vector_db_error" not in summary

    def test_records_vector_db_error_on_failure(self, db_session, monkeypatch):
        monkeypatch.setattr("app.database.SessionLocal", lambda: db_session)
        with patch(
            "app.pipeline.vector_store.reset_vector_db",
            side_effect=RuntimeError("boom"),
        ):
            summary = reset_all_data()
        assert summary["vector_db_error"] == "reset failed — see server logs"
        assert "vector_db_collections" not in summary


class TestResetAllDataTables:
    def test_clears_annual_report_holdings(self, db_session, monkeypatch):
        db_session.add(models.Senator(id="S1", name="A Senator", state="TX", party="R"))
        disclosure = models.FinancialDisclosure(
            senator_id="S1", filing_id="f", report_label="2025 annual report", source_url="u", parsed=True,
        )
        disclosure.holdings.append(models.FinancialHolding(asset_name="Apple", category="STOCKS", value_text="x"))
        db_session.add(disclosure)
        db_session.commit()
        monkeypatch.setattr("app.database.SessionLocal", lambda: db_session)
        with patch("app.pipeline.vector_store.reset_vector_db"):
            reset_all_data()
        assert db_session.query(models.FinancialDisclosure).count() == 0
        assert db_session.query(models.FinancialHolding).count() == 0

    def test_empties_every_table_but_the_kept_ones(self, db_session, monkeypatch):
        from sqlalchemy import func, select

        from app.database import RESET_KEEPS, Base

        db_session.add(models.Senator(id="S1", name="A Senator", state="TX", party="R"))
        db_session.add(models.PipelineRun(status="completed"))
        db_session.add(models.ActionIssue(date="2026-09-01", rank=1, title="An issue", related_explore_ids="[12, 40]"))
        db_session.add(models.ApiCache(tier="action-refresh-lock", cache_key="lock", data_json="{}"))
        db_session.add(models.ApiCache(tier="fec", cache_key="k", data_json="{}"))
        db_session.commit()
        monkeypatch.setattr("app.database.SessionLocal", lambda: db_session)
        with patch("app.pipeline.vector_store.reset_vector_db"):
            summary = reset_all_data()
        tables = {t.name for t in Base.metadata.sorted_tables}
        assert RESET_KEEPS <= tables  # a renamed table must not drop out of the keep list unnoticed
        assert set(summary) >= tables - RESET_KEEPS
        assert db_session.query(models.PipelineRun).count() == 1  # run history is kept
        # A kept issue no longer links to Explore rowids the rebuild reuses.
        db_session.expire_all()
        assert db_session.query(models.ActionIssue).one().related_explore_ids == "[]"
        # The refresh lease the reset holds while it runs is the one row it
        # leaves in api_cache.
        assert [r.tier for r in db_session.query(models.ApiCache).all()] == ["action-refresh-lock"]
        for table in Base.metadata.sorted_tables:
            if table.name not in RESET_KEEPS | {"api_cache"}:
                assert db_session.execute(select(func.count()).select_from(table)).scalar_one() == 0, table.name


class TestResetGuard:
    @pytest.fixture(autouse=True)
    def _one_session(self, db_session, monkeypatch):
        monkeypatch.setattr("app.database.SessionLocal", lambda: _Unclosable(db_session))

    async def _refused(self, db_session):
        from fastapi import HTTPException

        from app.api.admin import admin_reset_data

        with patch("app.database.reset_all_data") as reset:
            with pytest.raises(HTTPException) as refused:
                await admin_reset_data()
        reset.assert_not_called()
        assert refused.value.status_code == 409
        return refused.value.detail

    async def _refused_and_released(self, db_session):
        from app.pipeline import lease

        detail = await self._refused(db_session)
        # A refused reset lets its own lease go at once.
        assert not lease.held(db_session, lease.DATA_RESET)
        return detail

    async def test_refuses_while_a_pipeline_run_is_live_in_any_process(self, db_session):
        from app.time_utils import utcnow

        db_session.add(models.HousePipelineRun(status="running", started_at=utcnow()))
        db_session.commit()
        assert "House run" in await self._refused_and_released(db_session)

    async def test_refuses_while_a_writer_in_this_process_runs(self, db_session):
        from app.background import writing

        with writing("bill-status-refresh"):
            assert "bill-status-refresh" in await self._refused_and_released(db_session)

    async def test_refuses_while_the_action_center_refresh_holds_its_lease(self, db_session):
        from app.pipeline import lease

        assert lease.acquire(db_session, lease.ACTION_REFRESH) is not None
        assert "Action Center refresh" in await self._refused_and_released(db_session)

    async def test_refuses_while_another_process_resets(self, db_session):
        from app.pipeline import lease

        assert lease.acquire(db_session, lease.DATA_RESET) is not None
        assert "Another data reset" in await self._refused(db_session)

    async def test_holds_every_writer_off_for_the_wipe_and_lets_go_after(self, db_session, monkeypatch):
        from app.api.admin import admin_reset_data
        from app.background import WritesHeld, start_writer
        from app.pipeline import lease
        from app.pipeline.analyze import action_center
        from app.pipeline.run_tracker import STALE_PIPELINE_TIMEOUT, acquire_pipeline_lock_why

        monkeypatch.setattr(action_center, "_run_refresh", lambda db: 7)
        during = {}

        def wipe():
            # What another process's pipeline or refresh meets mid-wipe.
            during["pipeline_started"] = acquire_pipeline_lock_why(db_session, models.HousePipelineRun, STALE_PIPELINE_TIMEOUT)[0] is not None
            during["refresh_ran"] = action_center.refresh_action_issues(db_session) == 7
            try:
                start_writer(lambda: None, name="test-late")
                during["writer_started"] = True
            except WritesHeld:
                during["writer_started"] = False
            return {"senators": 2}

        with patch("app.database.reset_all_data", side_effect=wipe):
            result = await admin_reset_data()
        assert result["rowsDeleted"] == 2
        assert during == {"pipeline_started": False, "refresh_ran": False, "writer_started": False}
        # The backed-out pipeline left no run row, and the reset let go.
        assert db_session.query(models.HousePipelineRun).count() == 0
        assert not lease.held(db_session, lease.DATA_RESET)


class _Unclosable:
    """The test's one session, handed to code that closes what it opens."""

    def __init__(self, session):
        self._session = session

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._session, name)


class TestYieldingToAReset:
    """Another process's pipeline or refresh, meeting a reset: it checks the
    reset's lease inside its own lock's insert, so backing out writes
    nothing."""

    def test_a_pipeline_backs_out_leaving_no_run_row(self, db_session):
        from app.pipeline import lease
        from app.pipeline.run_tracker import STALE_PIPELINE_TIMEOUT, acquire_pipeline_lock_why

        assert lease.acquire(db_session, lease.DATA_RESET) is not None
        assert acquire_pipeline_lock_why(db_session, models.HousePipelineRun, STALE_PIPELINE_TIMEOUT)[0] is None
        assert db_session.query(models.HousePipelineRun).count() == 0

    def test_a_locked_database_is_busy_not_a_crash(self, db_session, monkeypatch):
        from sqlalchemy.exc import OperationalError

        from app.pipeline.run_tracker import STALE_PIPELINE_TIMEOUT, acquire_pipeline_lock_why

        def locked():
            raise OperationalError("INSERT", {}, Exception("database is locked"))

        monkeypatch.setattr(db_session, "flush", locked)
        assert acquire_pipeline_lock_why(db_session, models.HousePipelineRun, STALE_PIPELINE_TIMEOUT)[0] is None

    def test_a_lease_yields_without_committing_its_row(self, db_session):
        from app.pipeline import lease

        assert lease.acquire(db_session, lease.DATA_RESET) is not None
        with lease.holding(db_session, lease.ACTION_REFRESH, yield_to=lease.DATA_RESET) as token:
            assert token is None
        assert db_session.query(models.ApiCache).filter_by(tier=lease.ACTION_REFRESH).count() == 0


def test_startup_waits_out_a_senate_run_live_in_another_process(db_session, monkeypatch):
    """A live run's row is left alone, and watched: once its lease is no
    longer live — the other task finished, or died — the row is decided."""
    from app import main
    from app.pipeline import lease
    from app.time_utils import utcnow

    db_session.add(models.PipelineRun(status="running", started_at=utcnow()))
    db_session.commit()
    token = lease.acquire(db_session, lease.SENATE_RUN)  # its run, beating elsewhere
    monkeypatch.setattr("app.database.SessionLocal", lambda: _Unclosable(db_session))
    watchers = []
    monkeypatch.setattr(main, "start_writer", lambda target, name: watchers.append(target))

    main._invalidate_orphaned_pipelines()
    assert db_session.query(models.PipelineRun).one().status == "running" and len(watchers) == 1

    lease.release(db_session, lease.SENATE_RUN, token)  # the other task died
    watchers[0]()
    assert db_session.query(models.PipelineRun).one().status == "stale"


def test_startup_clears_a_senate_run_nobody_holds(db_session, monkeypatch):
    from app.main import _invalidate_orphaned_pipelines
    from app.time_utils import utcnow

    db_session.add(models.PipelineRun(status="running", started_at=utcnow()))
    db_session.commit()
    monkeypatch.setattr("app.database.SessionLocal", lambda: _Unclosable(db_session))
    _invalidate_orphaned_pipelines()
    assert db_session.query(models.PipelineRun).one().status == "stale"


class TestLease:
    def test_a_live_holder_is_never_replaced_and_a_stale_one_is(self, db_session):
        from datetime import timedelta

        from app.pipeline import lease
        from app.time_utils import utcnow

        assert lease.acquire(db_session, lease.SENATE_RUN) is not None
        assert lease.acquire(db_session, lease.SENATE_RUN) is None  # live: refused
        db_session.query(models.ApiCache).update({"cached_at": utcnow() - timedelta(minutes=11)})
        db_session.commit()
        assert lease.acquire(db_session, lease.SENATE_RUN) is not None  # its beats stopped: replaced

    def test_the_resets_lease_outlasts_an_unbeaten_wipe(self, db_session):
        from datetime import timedelta

        from app.pipeline import lease
        from app.time_utils import utcnow

        lease.acquire(db_session, lease.DATA_RESET)
        lease.acquire(db_session, lease.BILL_REFRESH)
        db_session.query(models.ApiCache).update({"cached_at": utcnow() - timedelta(minutes=20)})
        db_session.commit()
        assert lease.held(db_session, lease.DATA_RESET)
        assert not lease.held(db_session, lease.BILL_REFRESH)

    def test_a_job_yields_to_a_reset_and_holds_nothing(self, db_session, monkeypatch):
        from app.pipeline import lease

        monkeypatch.setattr("app.database.SessionLocal", lambda: _Unclosable(db_session))
        lease.acquire(db_session, lease.DATA_RESET)
        with lease.job(lease.BALLOT_SYNC) as held:
            assert not held and held.why.startswith("an admin data reset holds the database")
        assert not lease.held(db_session, lease.BALLOT_SYNC)

    def test_a_tracked_job_takes_its_tracker_then_its_lease(self, db_session, monkeypatch, caplog):
        """lease.tracked_job: the tracker this process's entry points share,
        then the lease (another process); refused by either, it holds
        neither, and the skip is logged as the caller's."""
        import asyncio
        import logging

        from app.pipeline import lease
        from app.pipeline.run_tracker import PipelineRunTracker

        monkeypatch.setattr("app.database.SessionLocal", lambda: _Unclosable(db_session))
        tracker = PipelineRunTracker()

        with lease.tracked_job(lease.BALLOT_SYNC, tracker) as held:
            assert held and tracker.is_running and lease.held(db_session, lease.BALLOT_SYNC)
        assert not tracker.is_running and not lease.held(db_session, lease.BALLOT_SYNC)

        token = tracker.start(holder="The scheduled sync")  # a run going in this process
        with caplog.at_level(logging.INFO, logger="app.pipeline.lease"), \
                lease.tracked_job(lease.BALLOT_SYNC, tracker, who="The nightly step") as held:
            assert not held and held.why == "The scheduled sync is already running in this process"
        assert "The nightly step skipped" in caplog.text
        assert tracker.is_running and not lease.held(db_session, lease.BALLOT_SYNC)
        tracker.stop(token)

        async def nightly():
            async with lease.tracked_job_async(lease.BALLOT_SYNC, tracker) as held:
                return bool(held)

        lease.acquire(db_session, lease.BALLOT_SYNC)  # a sync in another process
        assert asyncio.run(nightly()) is False
        assert not tracker.is_running

    def test_a_run_going_in_this_process_keeps_its_lease_row(self, db_session, monkeypatch):
        """Its beats held up past the stale window, its row could be taken
        over; the tracker refuses first, so nothing touches it."""
        from datetime import timedelta

        from app.pipeline import lease
        from app.pipeline.run_tracker import PipelineRunTracker
        from app.time_utils import utcnow

        monkeypatch.setattr("app.database.SessionLocal", lambda: _Unclosable(db_session))
        tracker = PipelineRunTracker()
        token = tracker.start()
        mine = lease.acquire(db_session, lease.COVERAGE_REFRESH)
        db_session.query(models.ApiCache).update({"cached_at": utcnow() - timedelta(minutes=15)})
        db_session.commit()
        with lease.tracked_job(lease.COVERAGE_REFRESH, tracker) as held:
            assert not held
        assert lease.beat(db_session, lease.COVERAGE_REFRESH, mine)  # still the running job's
        tracker.stop(token)

    def test_a_refused_lease_leaves_the_tracker_alone(self, db_session, monkeypatch):
        """The lease comes before the tracker's start: a tick refused by it
        holds nothing an in-process entry point would be refused by, and a
        hung run's tracker is replaced only by the lease's next holder."""
        import time
        from datetime import timedelta

        from app.pipeline import lease
        from app.pipeline.run_tracker import PipelineRunTracker

        monkeypatch.setattr("app.database.SessionLocal", lambda: _Unclosable(db_session))
        tracker = PipelineRunTracker()
        lease.acquire(db_session, lease.BALLOT_SYNC)  # a sync in another process
        with lease.tracked_job(lease.BALLOT_SYNC, tracker) as held:
            assert not held and not tracker.is_running

        token = tracker.start()  # a hung run here, its lease not yet lapsed
        tracker._started_at = time.time() - (lease.max_hold(lease.BALLOT_SYNC) + timedelta(minutes=1)).total_seconds()
        with lease.tracked_job(lease.BALLOT_SYNC, tracker) as held:
            assert not held
        assert tracker.is_running  # still the hung run's, not cleared
        tracker.stop(token)

    def test_a_tracked_async_body_is_cut_off_where_its_guards_stop_holding(self, db_session, monkeypatch):
        import asyncio
        from datetime import timedelta

        from app.pipeline import lease
        from app.pipeline.run_tracker import PipelineRunTracker

        monkeypatch.setattr("app.database.SessionLocal", lambda: _Unclosable(db_session))
        monkeypatch.setitem(
            lease.HUNG_AFTER, lease.COVERAGE_REFRESH,
            lease.stale_after(lease.COVERAGE_REFRESH) + timedelta(seconds=0.05),
        )
        tracker = PipelineRunTracker()

        async def hangs():
            async with lease.tracked_job_async(lease.COVERAGE_REFRESH, tracker) as held:
                assert held
                await asyncio.sleep(10)

        with pytest.raises(lease.CutOff):
            asyncio.run(hangs())

        async def times_out_inside():  # a request's own timeout is its failure, not a cut-off
            async with lease.tracked_job_async(lease.COVERAGE_REFRESH, tracker):
                raise TimeoutError("a request timed out")

        monkeypatch.setitem(lease.HUNG_AFTER, lease.COVERAGE_REFRESH, timedelta(hours=2))
        with pytest.raises(TimeoutError, match="a request timed out"):
            asyncio.run(times_out_inside())
        assert not tracker.is_running and not lease.held(db_session, lease.COVERAGE_REFRESH)

    def test_a_refusal_names_the_holder_as_it_named_itself(self, db_session):
        from app.pipeline import lease

        lease.acquire(db_session, lease.BALLOT_SYNC, who="Election pipeline's confirmed-candidate phase")
        assert lease.refusal(db_session, lease.BALLOT_SYNC) == (
            "Election pipeline's confirmed-candidate phase is already running (this process or another)"
        )
        lease.acquire(db_session, lease.BILL_REFRESH)
        assert lease.holder(db_session, lease.BILL_REFRESH) == lease.TIERS[lease.BILL_REFRESH]

    def test_every_lease_is_one_the_reset_names(self):
        from app.pipeline import lease

        tiers = {v for k, v in vars(lease).items() if k.isupper() and isinstance(v, str) and v.endswith("-lock")}
        assert tiers == set(lease.TIERS)

    def test_only_a_locked_database_is_busy(self, db_session, monkeypatch):
        from sqlalchemy.exc import OperationalError

        from app.pipeline import lease

        def fail(message):
            def flush():
                raise OperationalError("INSERT", {}, Exception(message))
            return flush

        monkeypatch.setattr(db_session, "flush", fail("database is locked"))
        assert lease.acquire(db_session, lease.BILL_REFRESH) is None
        monkeypatch.setattr(db_session, "flush", fail("no such table: api_cache"))
        with pytest.raises(OperationalError):
            lease.acquire(db_session, lease.BILL_REFRESH)

    def test_a_senate_run_takes_its_lease_before_its_lock(self, db_session, monkeypatch):
        """No RUNNING row without a live lease beside it: held elsewhere, the
        run doesn't start — and takes no lock."""
        import asyncio

        from app.pipeline import lease, senate_pipeline

        monkeypatch.setattr(senate_pipeline, "SessionLocal", lambda: _Unclosable(db_session))
        assert lease.acquire(db_session, lease.SENATE_RUN) is not None  # a live run elsewhere
        result = asyncio.run(senate_pipeline.run_senate_pipeline())
        assert result["status"] == "skipped"
        assert db_session.query(models.PipelineRun).count() == 0

    def test_every_lease_has_a_hung_horizon(self):
        from app.pipeline import lease

        assert set(lease.HUNG_AFTER) == set(lease.TIERS)
        assert all(lease.max_hold(tier).total_seconds() > 0 for tier in lease.TIERS)

    def test_a_hung_holders_lease_stops_being_renewed(self, db_session, monkeypatch):
        import time
        from datetime import timedelta

        from app.pipeline import lease

        monkeypatch.setattr(lease, "BEAT_S", 0.01)
        monkeypatch.setitem(lease.HUNG_AFTER, lease.BILL_REFRESH, lease.stale_after(lease.BILL_REFRESH) + timedelta(seconds=0.05))
        beats = []
        monkeypatch.setattr(lease, "beat", lambda db, tier, token: beats.append(time.monotonic()) or True)
        with lease.holding(db_session, lease.BILL_REFRESH):
            time.sleep(0.3)
        assert beats and beats[-1] - beats[0] < 0.1  # stopped at its limit, not at the end of the hold

    async def test_a_cancelled_async_take_lets_go_of_what_it_took(self, db_session, monkeypatch):
        import asyncio
        import threading

        from app.pipeline import lease

        monkeypatch.setattr("app.database.SessionLocal", lambda: _Unclosable(db_session))
        took = threading.Event()
        let_go = threading.Event()
        real_take = lease._take
        monkeypatch.setattr(lease, "_take", lambda *a: (took.wait(), real_take(*a))[1])
        real_let_go = lease._let_go_and_close
        monkeypatch.setattr(lease, "_let_go_and_close", lambda *a: (real_let_go(*a), let_go.set()))

        async def use():
            async with lease.job_async(lease.EXPLORE):
                pass

        task = asyncio.ensure_future(use())
        await asyncio.sleep(0.05)
        task.cancel()
        took.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(let_go.wait, 5)
        assert not lease.held(db_session, lease.EXPLORE)

    def test_a_senate_run_whose_lock_fails_lets_go_of_its_lease(self, db_session, monkeypatch):
        import asyncio

        from app.pipeline import lease, senate_pipeline

        monkeypatch.setattr(senate_pipeline, "SessionLocal", lambda: _Unclosable(db_session))

        def broken(_db):
            raise RuntimeError("disk I/O error")

        monkeypatch.setattr(senate_pipeline, "_acquire_pipeline_lock", broken)
        with pytest.raises(RuntimeError):
            asyncio.run(senate_pipeline.run_senate_pipeline())
        assert not lease.held(db_session, lease.SENATE_RUN)


def test_a_run_proceeded_past_is_forgotten_and_its_late_stop_is_a_no_op():
    """The tracker describes what is running now: a run a newer one was
    started past (a stale DB lock, a hung-run override) no longer counts."""
    from app.pipeline.run_tracker import PipelineRunTracker

    tracker = PipelineRunTracker()
    hung = tracker.start()
    newer = tracker.start()
    tracker.stop(hung)  # the hung one finally returns
    assert tracker.is_running
    tracker.stop(newer)
    assert not tracker.is_running and tracker.age is None


def test_a_run_finishing_during_the_orphan_check_keeps_its_status(db_session, monkeypatch):
    from app import main
    from app.pipeline import lease
    from app.time_utils import utcnow

    run = models.PipelineRun(status="running", started_at=utcnow())
    db_session.add(run)
    db_session.commit()
    monkeypatch.setattr("app.database.SessionLocal", lambda: _Unclosable(db_session))

    def finishes_meanwhile(db, tier):
        # Between the orphan check's reads: the run writes its final status,
        # then lets go of its lease.
        db_session.query(models.PipelineRun).update({"status": "completed"})
        db_session.commit()
        return False

    monkeypatch.setattr(lease, "held", finishes_meanwhile)
    main._check_orphaned_senate_runs()
    db_session.expire_all()
    assert db_session.query(models.PipelineRun).one().status == "completed"


def test_a_senate_run_held_off_says_why(db_session, monkeypatch):
    import asyncio

    from app.pipeline import lease, senate_pipeline

    monkeypatch.setattr(senate_pipeline, "SessionLocal", lambda: _Unclosable(db_session))
    lease.acquire(db_session, lease.DATA_RESET)
    result = asyncio.run(senate_pipeline.run_senate_pipeline())
    assert result == {"status": "skipped", "reason": lease.REFUSED_BY_RESET}


def test_busy_holds_off_a_fresh_run_not_a_hung_one():
    import time
    from datetime import timedelta

    from app.pipeline.run_tracker import PipelineRunTracker

    tracker = PipelineRunTracker()
    first = tracker.start(holder="The scheduled sync")
    assert tracker.busy() and tracker.busy(hung_after=timedelta(hours=2))
    assert tracker.holder == "The scheduled sync"
    tracker._started_at = time.time() - 3 * 3600  # three hours in: hung
    assert tracker.busy() and not tracker.busy(hung_after=timedelta(hours=2))
    second = tracker.start()  # replaces it
    tracker.stop(first)  # the replaced run's late stop is a no-op
    assert tracker.busy(hung_after=timedelta(hours=2)) and tracker.age < timedelta(minutes=1)
    tracker.stop(second)
    assert not tracker.is_running and tracker.holder is None
