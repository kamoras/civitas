"""settings.PROCESS_ROLE: production runs the read-only API and the pipeline
in separate processes (docker-compose.swarm.yml). What each role starts,
and what the API role refuses."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import main as main_module
from app.background import WritesElsewhere, running_writers, start_writer, writing
from app.config import settings


@pytest.fixture(autouse=True)
def _role_lock_path(tmp_path, monkeypatch):
    """Not the container's real lock in /dev/shm."""
    monkeypatch.setattr(main_module, "_ROLE_LOCK_PATH", str(tmp_path / "pipeline.lock"))


@pytest.fixture()
def role(monkeypatch):
    def set_role(value: str) -> None:
        monkeypatch.setattr(settings, "PROCESS_ROLE", value)

    return set_role


@pytest.fixture()
def started(monkeypatch):
    ran: list[str] = []

    def record(name):
        def _record(*_args, **_kwargs):
            ran.append(name)
        return _record

    async def _idle():
        ran.append("visit-consumer")

    async def _bootstrap():
        ran.append("explore-bootstrap")

    class _Loop:
        def run_in_executor(self, _executor, fn):
            ran.append(fn.__name__)

    monkeypatch.setattr(main_module, "init_db", record("init-db"))
    monkeypatch.setattr(main_module, "_invalidate_orphaned_pipelines", record("sweep"))
    monkeypatch.setattr(main_module, "start_scheduler", record("scheduler"))
    monkeypatch.setattr(main_module, "stop_scheduler", lambda: None)
    monkeypatch.setattr(main_module, "_start_pipeline_side_startup_jobs", record("startup-jobs"))
    monkeypatch.setattr(main_module, "_bootstrap_explore", _bootstrap)
    monkeypatch.setattr(main_module, "warm_bill_collection_cache", record("bill-cache"))
    monkeypatch.setattr(main_module, "run_visit_consumer", _idle)
    monkeypatch.setattr(main_module.asyncio, "get_running_loop", lambda: _Loop())
    return ran


class TestStartup:
    """Each role's lifespan starts its own half and nothing of the other's."""

    async def _run(self, started) -> set[str]:
        async with main_module.lifespan(main_module.app):
            await main_module.asyncio.sleep(0)
        return set(started)

    @pytest.mark.parametrize("value, expected", [
        pytest.param("api", {"init-db", "bill-cache", "_preload_models", "visit-consumer"},
                     id="api_starts_only_the_read_side"),
        pytest.param("worker", {"init-db", "sweep", "scheduler", "explore-bootstrap", "startup-jobs", "visit-consumer"},
                     id="worker_starts_only_the_pipeline_side"),
        pytest.param("all", {"init-db", "sweep", "scheduler", "bill-cache", "_preload_models",
                             "explore-bootstrap", "startup-jobs", "visit-consumer"},
                     id="all_starts_both"),
    ])
    async def test_each_role_starts_its_own_half(self, started, role, value, expected):
        role(value)
        assert await self._run(started) == expected


def test_the_search_model_is_preloaded_and_nothing_else():
    # No read request may load a model (AGENTS.md): Explore search encodes
    # with the similarity model. No read uses the primary one (it served
    # /api/qa), so no API worker holds a copy of it.
    loaded: list[str] = []
    with patch("app.pipeline.vector_store.get_similarity_model", lambda: loaded.append("similarity")), \
            patch("app.pipeline.vector_store.get_embedding_model", lambda: loaded.append("primary")):
        main_module._preload_models()
    assert loaded == ["similarity"]


def test_a_request_during_the_preload_waits_for_it_rather_than_loading_twice(monkeypatch):
    import threading
    import time

    from app.pipeline import vector_store

    built = []

    def slow_model(name):
        built.append(name)
        time.sleep(0.2)
        return object()

    monkeypatch.setattr(vector_store, "_model", None)
    monkeypatch.setattr(vector_store, "SentenceTransformer", slow_model)
    threads = [threading.Thread(target=vector_store.get_embedding_model) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(built) == 1


def test_a_failed_model_preload_is_only_logged():
    def boom():
        raise OSError("no model files")

    with patch("app.pipeline.vector_store.get_similarity_model", boom):
        main_module._preload_models()  # no raise


def test_a_late_scheduler_job_still_runs():
    # APScheduler's default one-second grace would skip a heartbeat or the
    # nightly run whenever the loop was busy at its moment.
    from app import scheduler

    assert scheduler.scheduler._job_defaults["misfire_grace_time"] >= 60
    assert scheduler.scheduler._job_defaults["coalesce"] is True


class TestWriters:
    def test_the_api_role_refuses_writer_threads(self, role):
        role("api")
        with pytest.raises(WritesElsewhere):
            start_writer(lambda: None, name="test-refused")
        assert "test-refused" not in running_writers()

    def test_the_api_role_refuses_enclosed_writes(self, role):
        role("api")
        with pytest.raises(WritesElsewhere):
            with writing("test-refused"):
                pass

    @pytest.mark.parametrize("value", ["worker", "all"])
    def test_other_roles_start_them(self, role, value):
        role(value)
        start_writer(lambda: None, name="test-allowed").join(timeout=5)

    def test_a_trigger_that_reaches_the_api_process_is_a_503(self, role, monkeypatch):
        # Real route, real exception handler: nginx sends triggers to the
        # pipeline service, so one arriving here is a routing gap to fix,
        # not a run to quietly start beside page requests.
        role("api")
        monkeypatch.setattr(settings, "PIPELINE_TRIGGER_TOKEN", "t")
        client = TestClient(main_module.app)  # no `with`: no lifespan
        resp = client.post("/api/justices/pipeline/trigger", headers={"Authorization": "Bearer t"})
        assert resp.status_code == 503
        assert "pipeline service" in resp.json()["detail"]


@pytest.fixture(autouse=True)
def _heartbeat_file(tmp_path, monkeypatch):
    """The scheduler heartbeat in a file of the test's own."""
    monkeypatch.setattr("app.scheduler.heartbeat_path", lambda: str(tmp_path / "scheduler_heartbeat.json"))


class TestNextRunTime:
    """The API process runs no scheduler: it reports the next run from the
    heartbeat the pipeline process's scheduler keeps, and nothing once that
    goes stale — a pipeline service that is down has no next run."""

    def test_reported_while_the_heartbeat_is_fresh(self, monkeypatch):
        from app import scheduler

        monkeypatch.setattr(scheduler, "_live_next_run", lambda: "2026-09-29T03:00:00+00:00")
        scheduler._record_next_run()
        assert not scheduler.scheduler.running
        assert scheduler.get_next_run_time() == "2026-09-29T03:00:00+00:00"

    def test_a_beat_in_the_same_moment_as_the_run_records_the_next_one(self, monkeypatch):
        # The scheduler advances next_run_time only after submitting the
        # run; a beat in between saw the time that had just fired.
        from datetime import datetime, timezone
        from types import SimpleNamespace

        from apscheduler.triggers.cron import CronTrigger

        from app import scheduler

        just_fired = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=1)
        job = SimpleNamespace(next_run_time=just_fired,
                              trigger=CronTrigger(hour=just_fired.hour, minute=just_fired.minute, timezone="UTC"))
        monkeypatch.setattr(scheduler.scheduler, "get_job", lambda job_id: job)
        assert datetime.fromisoformat(scheduler._live_next_run()) == just_fired.replace(second=0) + timedelta(days=1)

    def test_none_once_the_heartbeat_is_stale(self, monkeypatch):
        from app import scheduler

        monkeypatch.setattr(scheduler, "_live_next_run", lambda: "2026-09-29T03:00:00+00:00")
        scheduler._record_next_run()
        later = scheduler.utcnow() + scheduler._HEARTBEAT_STALE + timedelta(seconds=1)
        monkeypatch.setattr(scheduler, "utcnow", lambda: later)
        assert scheduler.get_next_run_time() is None

    def test_none_without_any_heartbeat(self):
        from app import scheduler

        assert scheduler.get_next_run_time() is None


class TestKeywordBackfill:
    """The backfill of a new keyword index is recorded as owed, so the
    process allowed to write runs it — whichever process created the index,
    and after a restart that killed it."""

    def _add_doc(self, db_session):
        from app.models import ExploreDocument

        db_session.add(ExploreDocument(
            doc_type="Rule", source="Federal Register", title="wildfire response rule",
            body="", date="2026-07-01", chamber="Executive",
        ))
        db_session.commit()

    def _pending(self, engine) -> bool:
        with engine.connect() as conn:
            return conn.execute(text(
                "SELECT 1 FROM explore_fts_meta WHERE key = 'backfill_pending'"
            )).fetchone() is not None

    def test_the_api_process_leaves_it_owed_and_the_worker_runs_it(self, db_session, role, monkeypatch):
        from app.pipeline import lexical_index as module
        from app.pipeline.lexical_index import search_lexical

        engine = db_session.get_bind()
        self._add_doc(db_session)
        ran: list[bool] = []

        def _synchronous(bound):
            ran.append(True)
            module._run_backfill(bound)

        monkeypatch.setattr(module, "_backfill_in_background", _synchronous)

        role("api")
        assert module.ensure_lexical_index(engine)
        assert ran == [] and self._pending(engine)

        role("worker")
        assert module.ensure_lexical_index(engine)
        assert ran == [True] and not self._pending(engine)
        assert len(search_lexical(db_session, "wildfire", limit=5)) == 1

        # Done: the next startup owes nothing.
        assert module.ensure_lexical_index(engine)
        assert ran == [True]

    def test_a_killed_backfill_is_run_again_on_the_next_start(self, db_session, monkeypatch):
        from app.pipeline import lexical_index as module

        engine = db_session.get_bind()
        self._add_doc(db_session)
        started: list[bool] = []
        monkeypatch.setattr(module, "_backfill_in_background", lambda bound: started.append(True))

        assert module.ensure_lexical_index(engine)  # started, then "killed"
        assert module.ensure_lexical_index(engine)
        assert started == [True, True] and self._pending(engine)


    def test_an_explore_runs_full_rebuild_settles_an_owed_backfill(self, db_session, monkeypatch):
        # Its rebuild re-tokenises the whole corpus: the next start owes
        # nothing more.
        from app.pipeline import lexical_index as module

        engine = db_session.get_bind()
        self._add_doc(db_session)
        monkeypatch.setattr(module, "_backfill_in_background", lambda bound: None)  # "killed"
        assert module.ensure_lexical_index(engine) and self._pending(engine)
        assert module.rebuild_index(db_session) == 1
        assert not self._pending(engine)

class TestOnePipelineProcess:
    """The pipeline side takes a per-container lock; a second process
    (a second uvicorn worker, however it was launched) refuses to start."""

    @pytest.mark.parametrize("value", ["worker", "all"])
    async def test_a_second_pipeline_process_refuses_to_start(self, role, monkeypatch, value):
        import os

        role(value)
        monkeypatch.setattr(main_module, "init_db", lambda: None)
        held = main_module._take_pipeline_role_lock()  # the first process
        try:
            with pytest.raises(RuntimeError, match="single process"):
                async with main_module.lifespan(main_module.app):
                    pass
        finally:
            os.close(held)

    def test_the_lock_is_free_again_once_released(self):
        import os

        os.close(main_module._take_pipeline_role_lock())
        os.close(main_module._take_pipeline_role_lock())

    async def test_the_lifespan_releases_it(self, role, started):
        import os

        role("worker")
        async with main_module.lifespan(main_module.app):
            pass
        os.close(main_module._take_pipeline_role_lock())

    async def test_api_processes_take_no_lock(self, role, started):
        import os

        role("api")
        held = main_module._take_pipeline_role_lock()
        try:
            async with main_module.lifespan(main_module.app):
                pass
        finally:
            os.close(held)
        assert "scheduler" not in started


def test_the_heartbeat_is_not_a_registered_writer(monkeypatch):
    # A data reset refuses while any writer is registered; a timestamp
    # write must not be what holds it off.
    from app import scheduler
    from app.background import running_writers

    seen = []
    monkeypatch.setattr(scheduler, "_record_next_run", lambda: seen.append(running_writers()))
    scheduler._heartbeat()
    assert seen == [[]]


def test_the_heartbeat_never_touches_the_database(monkeypatch):
    # A long pipeline transaction holding the write lock must not stop the
    # beat: a missed beat reads as a pipeline service that is gone.
    from app import scheduler

    def no_database():
        raise AssertionError("the heartbeat opened a database session")

    monkeypatch.setattr("app.database.SessionLocal", no_database)
    monkeypatch.setattr(scheduler, "_live_next_run", lambda: None)
    scheduler._record_next_run()
    beat, value = scheduler.read_heartbeat()
    assert value == {"nextRun": None}


def test_an_unreadable_heartbeat_file_is_unreadable_not_missing(tmp_path, monkeypatch):
    from app import scheduler
    from app.shared_state import UNREADABLE

    monkeypatch.setattr(scheduler, "heartbeat_path", lambda: str(tmp_path))  # a directory
    assert scheduler.read_heartbeat() is UNREADABLE


def test_a_failed_heartbeat_is_only_logged(monkeypatch):
    from app import scheduler

    def boom():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(scheduler, "_record_next_run", boom)
    scheduler._heartbeat()


def test_the_lock_is_per_database(monkeypatch):
    # A second local dev server on another database has no run state to
    # protect from this one.
    first = main_module._role_lock_path()
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite:////tmp/other.db")
    assert main_module._role_lock_path() != first


def test_the_test_run_has_its_own_ram_dir():
    import os

    from app.api.throttle import RAM_DIR

    assert RAM_DIR == os.environ["CIVITAS_RAM_DIR"] and RAM_DIR != "/dev/shm"


class TestPipelineServiceLiveness:
    """The API process alerts when the pipeline service stops: the site
    stays up without it, and every other watchdog runs inside it."""

    @pytest.fixture(autouse=True)
    def _records_in_tmp(self, tmp_path, monkeypatch):
        # The missing-heartbeat record lives on the data volume: here, a
        # directory of the test's own, whether or not /data exists.
        monkeypatch.setattr("app.atomic_write.runtime_data_path", lambda name: str(tmp_path / name))

    @pytest.fixture()
    def sent(self, monkeypatch):
        alerts = []
        monkeypatch.setattr("app.ops_alerts.send_ops_alert", lambda subject, body, **kw: alerts.append(subject))
        return alerts

    def _beat(self, age):
        import os
        import time

        from app import scheduler

        path = scheduler.heartbeat_path()
        with open(path, "w") as fh:
            fh.write("{}")
        beat = time.time() - age.total_seconds()
        os.utime(path, (beat, beat))

    def test_a_fresh_heartbeat_is_quiet(self, sent):
        from app.ops_alerts import check_pipeline_service_alive

        self._beat(timedelta(minutes=4))
        check_pipeline_service_alive()
        assert sent == []

    def test_a_stale_heartbeat_alerts(self, sent):
        from app.ops_alerts import PIPELINE_SERVICE_SILENT_AFTER, check_pipeline_service_alive

        self._beat(PIPELINE_SERVICE_SILENT_AFTER + timedelta(minutes=1))
        check_pipeline_service_alive()
        assert sent == ["Pipeline service is not running"]

    def test_no_heartbeat_yet_is_a_service_still_starting(self, sent, monkeypatch):
        # Its first deploy (or a fresh volume): pulling, migrating. Not a
        # page until it has been missing as long as silence is allowed —
        # counted from a record on the volume, so an API restarted by each
        # deploy doesn't start the wait over.
        import os
        import time

        from app import ops_alerts
        from app.shared_state import record_path

        monkeypatch.setattr(ops_alerts, "_heartbeat_missing_noticed", None)
        ops_alerts.check_pipeline_service_alive()
        assert sent == []
        path = record_path(ops_alerts._HEARTBEAT_MISSING_RECORD)
        noticed = time.time() - ops_alerts.PIPELINE_SERVICE_SILENT_AFTER.total_seconds() - 60
        os.utime(path, (noticed, noticed))
        ops_alerts.check_pipeline_service_alive()
        assert sent == ["Pipeline service is not running"]

    def test_a_missing_heartbeat_still_pages_when_the_record_cant_be_written(self, sent, monkeypatch):
        # A volume refusing the write must not silence the alert for good.
        from app import ops_alerts
        from app.time_utils import utcnow

        def refuse(*_a, **_k):
            raise OSError("read-only")

        monkeypatch.setattr("app.shared_state.write_record", refuse)
        monkeypatch.setattr(ops_alerts, "_heartbeat_missing_noticed",
                            utcnow() - ops_alerts.PIPELINE_SERVICE_SILENT_AFTER - timedelta(minutes=1))
        ops_alerts.check_pipeline_service_alive()
        assert sent == ["Pipeline service is not running"]

    def test_a_heartbeat_clears_the_missing_record(self, sent):
        import os

        from app import ops_alerts
        from app.shared_state import record_path

        ops_alerts.check_pipeline_service_alive()
        assert os.path.exists(record_path(ops_alerts._HEARTBEAT_MISSING_RECORD))
        self._beat(timedelta(minutes=1))
        ops_alerts.check_pipeline_service_alive()
        assert not os.path.exists(record_path(ops_alerts._HEARTBEAT_MISSING_RECORD))

    def test_an_unreadable_database_is_not_evidence(self, sent, monkeypatch):
        from app.ops_alerts import check_pipeline_service_alive
        from app.shared_state import UNREADABLE

        monkeypatch.setattr("app.scheduler.read_heartbeat", lambda: UNREADABLE)
        monkeypatch.setattr("app.ops_alerts._heartbeat_unreadable_since", None)
        check_pipeline_service_alive()
        assert sent == []

    def test_a_heartbeat_unreadable_for_as_long_as_silence_is_allowed_alerts(self, sent, monkeypatch):
        from app import ops_alerts
        from app.shared_state import UNREADABLE
        from app.time_utils import utcnow

        monkeypatch.setattr("app.scheduler.read_heartbeat", lambda: UNREADABLE)
        monkeypatch.setattr(
            ops_alerts, "_heartbeat_unreadable_since",
            utcnow() - ops_alerts.PIPELINE_SERVICE_SILENT_AFTER - timedelta(minutes=1),
        )
        ops_alerts.check_pipeline_service_alive()
        assert sent == ["Pipeline heartbeat unreadable"]

    def test_a_readable_heartbeat_ends_the_unreadable_run(self, sent, monkeypatch):
        from app import ops_alerts
        from app.time_utils import utcnow

        monkeypatch.setattr(ops_alerts, "_heartbeat_unreadable_since", utcnow() - timedelta(days=1))
        self._beat(timedelta(minutes=1))
        ops_alerts.check_pipeline_service_alive()
        assert sent == [] and ops_alerts._heartbeat_unreadable_since is None

    def test_a_dedupe_key_sends_once_across_processes(self, tmp_path, monkeypatch):
        """Every API worker runs the liveness check at the same moments: the
        dedupe row is inserted as the claim, so only one of them sends."""
        from concurrent.futures import ThreadPoolExecutor

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app import ops_alerts
        from app.database import Base

        engine = create_engine(f"sqlite:///{tmp_path / 'alerts.db'}", connect_args={"timeout": 5})
        Base.metadata.create_all(engine)
        monkeypatch.setattr(ops_alerts, "SessionLocal", sessionmaker(bind=engine))
        # Both pass the read check before either records: the race.
        monkeypatch.setattr(ops_alerts, "_already_sent", lambda key: False)
        delivered = []
        monkeypatch.setattr(ops_alerts.settings, "ALERT_NTFY_URL", "https://ntfy.invalid/x")
        monkeypatch.setattr(ops_alerts, "_send_ntfy", lambda subject, body: delivered.append(subject))
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda _: ops_alerts.send_ops_alert("down", "b", dedupe_key="k"), range(4)))
        assert delivered == ["down"]


    async def test_only_the_api_process_watches(self, role, started, monkeypatch):
        watched = []

        async def _watch():
            watched.append(1)

        monkeypatch.setattr(main_module, "_watch_pipeline_service", _watch)
        for value, expected in (("api", [1]), ("worker", [1]), ("all", [1])):
            role(value)
            async with main_module.lifespan(main_module.app):
                await main_module.asyncio.sleep(0)
            assert watched == expected, value


def test_a_second_outage_the_same_day_alerts_again(monkeypatch, tmp_path):
    """Keyed per outage (its last beat), not per day."""
    from datetime import timedelta

    from app import ops_alerts
    from app.time_utils import utcnow

    keys = []
    monkeypatch.setattr(ops_alerts, "send_ops_alert", lambda s, b, dedupe_key=None, condition=None: keys.append(dedupe_key))
    for last in (utcnow() - timedelta(hours=9), utcnow() - timedelta(hours=1)):
        monkeypatch.setattr("app.scheduler.read_heartbeat", lambda last=last: (last, {}))
        ops_alerts.check_pipeline_service_alive()
    assert len(keys) == 2 and keys[0] != keys[1]


def test_the_lock_follows_the_database_file_not_the_url(monkeypatch, tmp_path):
    # The default URL is relative: two checkouts, two ./data/civitas.db
    # files, one URL string.
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite:///data/civitas.db")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    monkeypatch.chdir(tmp_path / "a")
    first = main_module._role_lock_path()
    monkeypatch.chdir(tmp_path / "b")
    assert main_module._role_lock_path() != first


async def test_the_pipeline_lock_is_taken_before_init_db(monkeypatch):
    # init_db already starts background work in a pipeline-side role.
    order = []
    monkeypatch.setattr(settings, "PROCESS_ROLE", "worker")
    monkeypatch.setattr(main_module, "_take_pipeline_role_lock", lambda: order.append("lock") or None)

    def init():
        order.append("init_db")
        raise RuntimeError("stop here")

    monkeypatch.setattr(main_module, "init_db", init)
    try:
        async with main_module.lifespan(main_module.app):
            pass
    except RuntimeError:
        pass
    assert order == ["lock", "init_db"]


def test_a_beating_pipeline_service_resolves_its_silent_alert(monkeypatch):
    # Open until the heartbeat is back: the dashboard shows it as active
    # only while the service is really down, and a later outage alerts anew.
    from app import ops_alerts
    from app.time_utils import utcnow

    resolved = []
    outcome = [-1]  # the first resolve can't write; the next tick tries again
    monkeypatch.setattr(ops_alerts, "resolve_ops_alert", lambda c: (resolved.append(c), outcome.pop(0) if outcome else 1)[1])
    monkeypatch.setattr(ops_alerts, "send_ops_alert", lambda *a, **k: None)
    monkeypatch.setattr("app.scheduler.read_heartbeat", lambda: (utcnow(), {}))
    monkeypatch.setattr(ops_alerts, "_silent_alert_may_be_open", True)  # a new process
    monkeypatch.setattr(ops_alerts, "_unreadable_alert_may_be_open", False)
    ops_alerts.check_pipeline_service_alive()
    ops_alerts.check_pipeline_service_alive()
    assert resolved == ["pipeline-service-silent"] * 2
    # Not again every healthy tick: that reads the whole alert history.
    ops_alerts.check_pipeline_service_alive()
    assert resolved == ["pipeline-service-silent"] * 2


def test_a_readable_heartbeat_resolves_its_unreadable_alert_until_that_writes(monkeypatch):
    from app import ops_alerts
    from app.time_utils import utcnow

    resolved, outcome = [], [-1]
    monkeypatch.setattr(ops_alerts, "resolve_ops_alert",
                        lambda c: (resolved.append(c), outcome.pop(0) if outcome else 1)[1])
    monkeypatch.setattr("app.scheduler.read_heartbeat", lambda: (utcnow(), {}))
    monkeypatch.setattr(ops_alerts, "_silent_alert_may_be_open", False)
    monkeypatch.setattr(ops_alerts, "_unreadable_alert_may_be_open", True)  # a new process
    for _ in range(3):
        ops_alerts.check_pipeline_service_alive()
    assert resolved == ["pipeline-heartbeat-unreadable"] * 2  # failed once, then written, then left
