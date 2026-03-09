import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import logging
from app.config import settings

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.router import api_router
from app.database import init_db
from app.scheduler import start_scheduler, stop_scheduler

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
            await run_explore_pipeline(days_back=60)
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
    from datetime import datetime
    from app.database import SessionLocal
    from app.models import PipelineRun

    db = SessionLocal()
    try:
        orphaned = db.query(PipelineRun).filter(PipelineRun.status == "running").all()
        for run in orphaned:
            run.status = "stale"
            run.completed_at = datetime.utcnow()
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
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _preload_embedding_model)
    asyncio.create_task(_bootstrap_explore())
    yield
    stop_scheduler()


app = FastAPI(
    title="Civitas API",
    description="Backend API for the Civitas senator representation tracker",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=500)
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
