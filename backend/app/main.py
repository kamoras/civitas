import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import logging
from app.config import settings

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.cache_headers import DataVersionCacheMiddleware
from app.api.router import api_router
from app.database import init_db
from app.scheduler import start_scheduler, stop_scheduler
from app.time_utils import utcnow
from app.background import WritesHeld, start_writer, writing

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
            # Registered for the admin data reset: the pipeline hands its
            # writes to threads while this awaits.
            with writing("Explore bootstrap"):
                await run_explore_pipeline(days_back=60)
    except WritesHeld as held:
        logging.getLogger("app.main").info("%s", held)
    except Exception as e:
        logging.getLogger("app.main").warning("Explore bootstrap failed: %s", e)


def _preload_embedding_model() -> None:
    """Load the sentence-transformers model eagerly so the first search is fast."""
    try:
        from app.pipeline.vector_store import get_embedding_model
        get_embedding_model()
    except Exception as e:
        logging.getLogger("app.main").warning("Embedding model preload failed: %s", e)


def _invalidate_orphaned_pipelines() -> None:
    """Mark any 'running' pipeline rows as stale on startup.

    If the app is starting, no pipeline thread from this process can be
    active -- any 'running' row is left over from a prior crash or deploy.
    """
    from app.database import SessionLocal
    from app.models import PipelineRun, PipelineStatus

    db = SessionLocal()
    try:
        from app.pipeline import lease

        if lease.held(db, lease.SENATE_RUN):
            # A live Senate run holds it — another process's, during a
            # rollout's overlap: not an orphan.
            return
        orphaned = db.query(PipelineRun).filter(PipelineRun.status == PipelineStatus.RUNNING).all()
        for run in orphaned:
            run.status = PipelineStatus.STALE
            run.completed_at = utcnow()
            run.error_message = "Marked stale: app restarted while pipeline was running"
            logging.getLogger("app.main").warning(
                "Invalidated orphaned pipeline run #%d (started %s)",
                run.id, run.started_at,
            )
        if orphaned:
            db.commit()
    except Exception as e:
        logging.getLogger("app.main").warning("Orphan pipeline cleanup failed: %s", e)
    finally:
        db.close()


PROCESS_STARTED_AT: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global PROCESS_STARTED_AT
    from datetime import datetime, timezone
    PROCESS_STARTED_AT = datetime.now(timezone.utc).isoformat()
    init_db()
    _invalidate_orphaned_pipelines()
    start_scheduler()
    # Pre-build the bills-in-flight collection cache on a background thread
    # so the first /api/bills request after a deploy is a cache hit instead
    # of paying the ~1.5s cold rebuild (see bill_service.py).
    from app.services.bill_service import warm_bill_collection_cache
    warm_bill_collection_cache()
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _preload_embedding_model)
    # Held for the lifespan: the event loop keeps only a weak reference to a
    # task, so an unreferenced one can be garbage-collected mid-ingestion.
    bootstrap_task = asyncio.create_task(_bootstrap_explore())

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
                    logging.getLogger("app.main").info("Startup rescore skipped: a data reset or another rescore holds the database")
                    return
                rescore_stale_legislative_effectiveness(_rescore_session)
                rescore_stale_constituent_alignment(_rescore_session)
        except Exception:
            # Each rescore logs its own failures; this is the lease's.
            logging.getLogger("app.main").exception("Startup rescore failed")
        finally:
            db.close()

    start_writer(_startup_rescore, name="startup-rescore")

    from app.api.visits import run_visit_consumer
    visit_consumer_task = asyncio.create_task(run_visit_consumer())

    yield

    visit_consumer_task.cancel()
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


app.add_middleware(GZipMiddleware, minimum_size=500)
# Added after GZip, so it runs *outside* it: a 304 short-circuit should
# never reach the compressor, and the ETag is a weak validator precisely
# because the body below it may or may not have been compressed.
app.add_middleware(DataVersionCacheMiddleware)
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
