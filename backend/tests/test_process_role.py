"""settings.PROCESS_ROLE: production runs the read-only API and the pipeline
in separate processes (docker-compose.swarm.yml). What each role starts,
and what the API role refuses."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import main as main_module
from app.background import WritesElsewhere, running_writers, start_writer, writing
from app.config import settings


@pytest.fixture()
def role(monkeypatch):
    def set_role(value: str) -> None:
        monkeypatch.setattr(settings, "PROCESS_ROLE", value)

    return set_role


class TestStartup:
    """Each role's lifespan starts its own half and nothing of the other's."""

    @pytest.fixture()
    def started(self, monkeypatch):
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

    async def _run(self, started) -> set[str]:
        async with main_module.lifespan(main_module.app):
            await main_module.asyncio.sleep(0)
        return set(started)

    async def test_api_starts_only_the_read_side(self, started, role):
        role("api")
        assert await self._run(started) == {
            "init-db", "bill-cache", "_preload_embedding_models", "visit-consumer",
        }

    async def test_worker_starts_only_the_pipeline_side(self, started, role):
        role("worker")
        assert await self._run(started) == {
            "init-db", "sweep", "scheduler", "explore-bootstrap", "startup-jobs", "visit-consumer",
        }

    async def test_all_starts_both(self, started, role):
        role("all")
        assert await self._run(started) == {
            "init-db", "sweep", "scheduler", "bill-cache", "_preload_embedding_models",
            "explore-bootstrap", "startup-jobs", "visit-consumer",
        }


def test_both_embedding_models_are_preloaded():
    # Explore search encodes with the similarity model; only the primary
    # used to be preloaded, so the first search still loaded one inline.
    loaded: list[str] = []
    with patch("app.pipeline.vector_store.get_similarity_model", lambda: loaded.append("similarity")), \
            patch("app.pipeline.vector_store.get_embedding_model", lambda: loaded.append("primary")):
        main_module._preload_embedding_models()
    assert sorted(loaded) == ["primary", "similarity"]


def test_a_failed_model_preload_does_not_stop_the_other():
    loaded: list[str] = []

    def boom():
        raise OSError("no model files")

    with patch("app.pipeline.vector_store.get_similarity_model", boom), \
            patch("app.pipeline.vector_store.get_embedding_model", lambda: loaded.append("primary")):
        main_module._preload_embedding_models()
    assert loaded == ["primary"]


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
    def test_computed_from_the_schedule_where_the_scheduler_is_not_running(self, monkeypatch):
        from app import scheduler

        monkeypatch.setattr(settings, "PIPELINE_CRON_SCHEDULE", "0 3 * * *")
        assert not scheduler.scheduler.running
        next_run = datetime.fromisoformat(scheduler.get_next_run_time())
        now = datetime.now(timezone.utc)
        assert (next_run.hour, next_run.minute) == (3, 0)
        assert next_run.utcoffset().total_seconds() == 0
        assert 0 < (next_run - now).total_seconds() <= 24 * 3600

    def test_none_for_an_invalid_schedule(self, monkeypatch):
        from app import scheduler

        monkeypatch.setattr(settings, "PIPELINE_CRON_SCHEDULE", "not a cron")
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


@pytest.mark.parametrize("value", ["worker", "all"])
async def test_the_pipeline_side_refuses_to_run_as_several_workers(role, monkeypatch, value):
    role(value)
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    with pytest.raises(RuntimeError, match="single worker"):
        async with main_module.lifespan(main_module.app):
            pass
