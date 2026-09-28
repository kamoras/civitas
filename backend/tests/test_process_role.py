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
    monkeypatch.setattr("app.services.bill_service.warm_bill_collection_cache", record("bill-cache"))
    monkeypatch.setattr("app.api.visits.run_visit_consumer", _idle)
    monkeypatch.setattr(main_module.asyncio, "get_running_loop", lambda: _Loop())
    return ran



class TestStartup:
    """Each role's lifespan starts its own half and nothing of the other's."""

    async def _run(self, started) -> set[str]:
        async with main_module.lifespan(main_module.app):
            await main_module.asyncio.sleep(0)
        return set(started)

    async def test_api_starts_only_the_read_side(self, started, role):
        role("api")
        assert await self._run(started) == {
            "init-db", "bill-cache", "_preload_models", "visit-consumer",
        }

    async def test_worker_starts_only_the_pipeline_side(self, started, role):
        role("worker")
        assert await self._run(started) == {
            "init-db", "sweep", "scheduler", "explore-bootstrap", "startup-jobs", "visit-consumer",
        }

    async def test_all_starts_both(self, started, role):
        role("all")
        assert await self._run(started) == {
            "init-db", "sweep", "scheduler", "bill-cache", "_preload_models",
            "explore-bootstrap", "startup-jobs", "visit-consumer",
        }


def test_both_embedding_models_are_preloaded():
    # No read request may load a model (AGENTS.md): Explore search encodes
    # with the similarity model, /api/qa with the primary one.
    loaded: list[str] = []
    with patch("app.pipeline.vector_store.get_similarity_model", lambda: loaded.append("similarity")), \
            patch("app.pipeline.vector_store.get_embedding_model", lambda: loaded.append("primary")):
        main_module._preload_models()
    assert loaded == ["similarity", "primary"]


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
        main_module._preload_models()


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


class TestNextRunTime:
    """The API process runs no scheduler: it reports the next run from the
    heartbeat the pipeline process's scheduler keeps, and nothing once that
    goes stale — a pipeline service that is down has no next run."""

    @pytest.fixture()
    def shared_db(self, db_session, monkeypatch):
        from contextlib import contextmanager

        from sqlalchemy.orm import Session

        @contextmanager
        def _scope():
            session = Session(bind=db_session.get_bind())
            try:
                yield session
            finally:
                session.close()

        monkeypatch.setattr("app.database.session_scope", _scope)
        return db_session

    def test_reported_while_the_heartbeat_is_fresh(self, shared_db, monkeypatch):
        from app import scheduler

        monkeypatch.setattr(scheduler, "_live_next_run", lambda: "2026-09-29T03:00:00+00:00")
        scheduler._record_next_run()
        assert not scheduler.scheduler.running
        assert scheduler.get_next_run_time() == "2026-09-29T03:00:00+00:00"

    def test_none_once_the_heartbeat_is_stale(self, shared_db, monkeypatch):
        from app import scheduler

        monkeypatch.setattr(scheduler, "_live_next_run", lambda: "2026-09-29T03:00:00+00:00")
        scheduler._record_next_run()
        later = scheduler.utcnow() + scheduler._HEARTBEAT_STALE + timedelta(seconds=1)
        monkeypatch.setattr(scheduler, "utcnow", lambda: later)
        assert scheduler.get_next_run_time() is None

    def test_none_without_any_heartbeat(self, shared_db):
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

    @pytest.fixture()
    def sent(self, db_session, monkeypatch):
        from contextlib import contextmanager

        from sqlalchemy.orm import Session

        @contextmanager
        def _scope():
            session = Session(bind=db_session.get_bind())
            try:
                yield session
            finally:
                session.close()

        monkeypatch.setattr("app.database.session_scope", _scope)
        alerts = []
        monkeypatch.setattr("app.ops_alerts.send_ops_alert", lambda subject, body, **kw: alerts.append(subject))
        return alerts

    def _beat(self, db_session, age):
        from app.database import SCHEDULER_HEARTBEAT_KEY, SCHEDULER_HEARTBEAT_TIER
        from app.shared_state import write_row
        from app.time_utils import utcnow

        write_row(db_session, SCHEDULER_HEARTBEAT_TIER, SCHEDULER_HEARTBEAT_KEY, {}, at=utcnow() - age)
        db_session.commit()

    def test_a_fresh_heartbeat_is_quiet(self, db_session, sent):
        from app.ops_alerts import check_pipeline_service_alive

        self._beat(db_session, timedelta(minutes=4))
        check_pipeline_service_alive()
        assert sent == []

    def test_a_stale_heartbeat_alerts(self, db_session, sent):
        from app.ops_alerts import PIPELINE_SERVICE_SILENT_AFTER, check_pipeline_service_alive

        self._beat(db_session, PIPELINE_SERVICE_SILENT_AFTER + timedelta(minutes=1))
        check_pipeline_service_alive()
        assert sent == ["Pipeline service is not running"]

    def test_no_heartbeat_ever_alerts(self, sent):
        from app.ops_alerts import check_pipeline_service_alive

        check_pipeline_service_alive()
        assert sent == ["Pipeline service is not running"]

    def test_an_unreadable_database_is_not_evidence(self, sent, monkeypatch):
        from app.ops_alerts import check_pipeline_service_alive
        from app.shared_state import UNREADABLE

        monkeypatch.setattr("app.shared_state.read_row", lambda *a, **k: UNREADABLE)
        monkeypatch.setattr("app.ops_alerts._heartbeat_unreadable_since", None)
        check_pipeline_service_alive()
        assert sent == []

    def test_a_heartbeat_unreadable_for_as_long_as_silence_is_allowed_alerts(self, sent, monkeypatch):
        from app import ops_alerts
        from app.shared_state import UNREADABLE
        from app.time_utils import utcnow

        monkeypatch.setattr("app.shared_state.read_row", lambda *a, **k: UNREADABLE)
        monkeypatch.setattr(
            ops_alerts, "_heartbeat_unreadable_since",
            utcnow() - ops_alerts.PIPELINE_SERVICE_SILENT_AFTER - timedelta(minutes=1),
        )
        ops_alerts.check_pipeline_service_alive()
        assert sent == ["Pipeline heartbeat unreadable"]

    def test_a_readable_heartbeat_ends_the_unreadable_run(self, db_session, sent, monkeypatch):
        from app import ops_alerts
        from app.time_utils import utcnow

        monkeypatch.setattr(ops_alerts, "_heartbeat_unreadable_since", utcnow() - timedelta(days=1))
        self._beat(db_session, timedelta(minutes=1))
        ops_alerts.check_pipeline_service_alive()
        assert sent == [] and ops_alerts._heartbeat_unreadable_since is None

    def test_one_worker_checks_each_round(self, sent, monkeypatch):
        """Every API worker runs the watch at the same moments; a round goes
        to one of them, so a stale heartbeat alerts once, not per worker."""
        from concurrent.futures import ThreadPoolExecutor

        checks = []
        monkeypatch.setattr("app.ops_alerts.check_pipeline_service_alive", lambda: checks.append(1))
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda _: main_module._check_pipeline_service_once(), range(2)))
        assert checks == [1]

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
