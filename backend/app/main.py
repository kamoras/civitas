import asyncio
import os
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import logging
from app.config import settings

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.cache_headers import ETagCacheMiddleware
from app.api.router import api_router
from app.database import init_db
from app.scheduler import start_scheduler, stop_scheduler
from app.background import WritesElsewhere, WritesHeld, start_writer, writing

# Configure logging level from PIPELINE_LOG_LEVEL env setting
_level_name = (settings.PIPELINE_LOG_LEVEL or "info").upper()
_level = getattr(logging, _level_name, logging.INFO)
# Add a StreamHandler to root so app.* loggers have somewhere to write
_root = logging.getLogger()
if not _root.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    _root.addHandler(_handler)
_root.setLevel(_level)
for _n in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi", "app"):
    logging.getLogger(_n).setLevel(_level)
# httpx logs every request URL at INFO — including api_key query params,
# which must not end up in docker logs. Keep third-party HTTP loggers at
# WARNING regardless of the app log level.
for _n in ("httpx", "httpcore"):
    logging.getLogger(_n).setLevel(logging.WARNING)


async def _bootstrap_explore() -> None:
    """Run explore pipeline once at startup if the document store is empty."""
    await asyncio.sleep(5)
    try:
        from app.database import SessionLocal
        from app.models import ExploreDocument

        db = SessionLocal()
        count = db.query(ExploreDocument.id).limit(1).first()
        db.close()

        if count is None:
            _logger = logging.getLogger("app.main")
            _logger.info("Explore document store is empty — running initial ingestion")
            from app.pipeline.explore_pipeline import run_explore_pipeline
            from app.pipeline import lease

            # Registered for the admin data reset: the pipeline hands its
            # writes to threads while this awaits. And a lease, so a reset in
            # another process sees it too.
            with writing("Explore bootstrap"):
                async with lease.job_async(lease.EXPLORE) as held:
                    if held:
                        await run_explore_pipeline(days_back=60)
    except WritesHeld as held:
        logging.getLogger("app.main").info("%s", held)
    except Exception as e:
        logging.getLogger("app.main").warning("Explore bootstrap failed: %s", e)


def _preload_embedding_models() -> None:
    """Load both sentence-transformers models eagerly so the first request
    that needs one doesn't pay the load: Explore search encodes queries
    with the similarity model (vector_store.search_explore), /api/qa with
    the primary one. Only the primary used to be preloaded, so the first
    search after every restart still loaded its model inline."""
    from app.pipeline.vector_store import get_embedding_model, get_similarity_model

    for load in (get_similarity_model, get_embedding_model):
        try:
            load()
        except Exception as e:
            logging.getLogger("app.main").warning("Embedding model preload failed: %s", e)


def _invalidate_orphaned_pipelines() -> None:
    """Mark stale the 'running' pipeline rows a restart left behind, for
    every pipeline (run_tracker.sweep_orphaned_runs).

    Pipelines run in threads of the backend process, so a restart kills
    them without letting them record it: any 'running' row is left over
    from a prior crash or deploy. (check-and-deploy.sh does not deploy
    while one runs, which is what keeps a start-first rollout's overlap
    from sweeping a live run.) Every table, not just the Senate's: an
    unswept row reads as "running" on the admin dashboard and blocks a
    manual trigger until STALE_PIPELINE_TIMEOUT ages it out. The one
    exception is a Senate row whose lease still holds — it may be a run
    live in the other task, the one case that can be seen.
    """
    from app.pipeline.run_tracker import sweep_orphaned_runs

    sweep_orphaned_runs()


def _start_pipeline_side_startup_jobs() -> None:
    """The startup work that writes: run where pipelines run, never in the
    read-only API process."""
    # Rebuild the sqlite-vec explore index when missing or built by a
    # different model (the 2026-07 chroma->sqlite-vec migration path, and
    # any future index-model change). Spawns its own daemon thread;
    # search reports "not ready" until it completes.
    try:
        from app.database import SessionLocal
        from app.pipeline.vector_store import ensure_explore_index
        ensure_explore_index(SessionLocal)
    except Exception:
        logging.getLogger(__name__).exception("Explore index check failed (non-fatal)")

    # A release that rescales Legislative Effectiveness or Constituent
    # Alignment leaves the stored scores and reference on the old scale until
    # the nightly run; bring them over now so pages and their breakdowns agree
    # (les_rescore.py, constituent_rescore.py). One thread, one after the
    # other, so the two never contend for SQLite's write lock.
    from app.database import SessionLocal as _rescore_session
    from app.pipeline.constituent_rescore import rescore_stale_constituent_alignment
    from app.pipeline.les_rescore import rescore_stale_legislative_effectiveness

    def _startup_rescore() -> None:
        from app.pipeline import lease

        # A lease, so an admin data reset in another process sees this run
        # and this run sees the reset (lease.DATA_RESET).
        db = _rescore_session()
        try:
            with lease.holding(db, lease.STARTUP_RESCORE, yield_to=lease.DATA_RESET) as token:
                if token is None:
                    logging.getLogger("app.main").info(
                        "Startup rescore skipped: %s", lease.refusal(db, lease.STARTUP_RESCORE),
                    )
                    return
                rescore_stale_legislative_effectiveness(_rescore_session)
                rescore_stale_constituent_alignment(_rescore_session)
        except Exception:
            # Each rescore logs its own failures; this is the lease's.
            logging.getLogger("app.main").exception("Startup rescore failed")
        finally:
            db.close()

    start_writer(_startup_rescore, name="startup-rescore")


PROCESS_STARTED_AT: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global PROCESS_STARTED_AT
    from datetime import datetime, timezone
    PROCESS_STARTED_AT = datetime.now(timezone.utc).isoformat()
    init_db()
    role = settings.PROCESS_ROLE
    serves_reads = role in ("all", "api")
    runs_pipelines = role in ("all", "worker")
    logging.getLogger("app.main").info("Backend process role: %s", role)
    if runs_pipelines and int(os.environ.get("WEB_CONCURRENCY") or 1) > 1:
        # The pipeline side keeps state only its own process can see — the
        # in-memory run flags the admin status endpoint (and through it
        # check-and-deploy.sh's busy check) reports, the data reset's
        # writer registry — and would run one scheduler per worker.
        raise RuntimeError(
            f"PROCESS_ROLE={role} must run as a single worker process "
            f"(WEB_CONCURRENCY={os.environ['WEB_CONCURRENCY']}); only PROCESS_ROLE=api scales out"
        )

    if runs_pipelines:
        # Only the process that runs pipelines may sweep their rows: the
        # API process sweeping on its own restart would mark a run live in
        # the pipeline process as dead.
        _invalidate_orphaned_pipelines()
        start_scheduler()
    if serves_reads:
        # Pre-build the bills-in-flight collection cache on a background
        # thread so the first /api/bills request after a deploy is a cache
        # hit instead of paying the ~1.5s cold rebuild (see bill_service.py).
        from app.services.bill_service import warm_bill_collection_cache
        warm_bill_collection_cache()
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _preload_embedding_models)

    bootstrap_task = None
    if runs_pipelines:
        # Held for the lifespan: the event loop keeps only a weak reference
        # to a task, so an unreferenced one can be garbage-collected
        # mid-ingestion.
        bootstrap_task = asyncio.create_task(_bootstrap_explore())
        _start_pipeline_side_startup_jobs()

    from app.api.visits import run_visit_consumer
    visit_consumer_task = asyncio.create_task(run_visit_consumer())

    yield

    visit_consumer_task.cancel()
    if bootstrap_task is not None:
        bootstrap_task.cancel()
    stop_scheduler()


app = FastAPI(
    title="Civitas API",
    description="Backend API for the Civitas senator representation tracker",
    version="0.1.0",
    lifespan=lifespan,
)



@app.exception_handler(WritesHeld)
async def _writes_held(_request, held: WritesHeld) -> JSONResponse:
    """An endpoint's writer refused while the admin data reset holds the
    database (app.background.writing)."""
    return JSONResponse(status_code=409, content={"detail": str(held)})


@app.exception_handler(WritesElsewhere)
async def _writes_elsewhere(_request, refused: WritesElsewhere) -> JSONResponse:
    """A trigger reached the read-only API process: nginx routes every
    trigger to the pipeline service, so this one is missing from that list
    (nginx/civitas.conf, "Background work")."""
    return JSONResponse(status_code=503, content={"detail": str(refused)})


app.add_middleware(GZipMiddleware, minimum_size=500)
# Added after GZip, so it runs *outside* it: a 304 short-circuit should
# never reach the compressor, and the ETag is a weak validator precisely
# because the body below it may or may not have been compressed.
app.add_middleware(ETagCacheMiddleware)
_cors_origins = [
    o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()
] if settings.CORS_ORIGINS else [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(api_router)
