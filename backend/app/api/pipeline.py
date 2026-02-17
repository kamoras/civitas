import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import PipelineRun
from app.pipeline.orchestrator import run_full_pipeline
from app.schemas import PipelineRunSchema, PipelineStatusSchema

logger = logging.getLogger(__name__)

router = APIRouter()

# Simple in-memory flag for whether a pipeline is currently running
_pipeline_running = False


@router.get("/pipeline/status", response_model=PipelineStatusSchema)
def pipeline_status(db: Session = Depends(get_db)) -> PipelineStatusSchema:
    """Return the last pipeline run info and next scheduled time."""
    last_run = (
        db.query(PipelineRun)
        .order_by(PipelineRun.started_at.desc())
        .first()
    )
    last_run_schema = None
    if last_run:
        last_run_schema = PipelineRunSchema(
            id=last_run.id,
            started_at=last_run.started_at,
            completed_at=last_run.completed_at,
            status=last_run.status,
            senators_processed=last_run.senators_processed,
            senators_failed=last_run.senators_failed,
            bills_classified=last_run.bills_classified,
            llm_calls=last_run.llm_calls,
            cache_hits=last_run.cache_hits,
            cache_misses=last_run.cache_misses,
            elapsed_seconds=last_run.elapsed_seconds,
            error_message=last_run.error_message,
        )

    # Get next scheduled time from scheduler if available
    try:
        from app.scheduler import get_next_run_time
        next_time = get_next_run_time()
    except Exception:
        next_time = None

    return PipelineStatusSchema(
        last_run=last_run_schema,
        next_scheduled=next_time,
        is_running=_pipeline_running,
    )


@router.post("/pipeline/trigger")
async def trigger_pipeline(
    authorization: str | None = Header(default=None),
    senator: str | None = Query(default=None, description="Filter to a single senator by name"),
    fetch_only: bool = Query(default=False, description="Stop after fetch phase (no LLM analysis)"),
) -> dict:
    """Trigger a pipeline run. Requires Bearer token matching PIPELINE_TRIGGER_TOKEN."""
    global _pipeline_running

    if not settings.PIPELINE_TRIGGER_TOKEN:
        raise HTTPException(status_code=503, detail="Pipeline trigger token not configured")

    expected = f"Bearer {settings.PIPELINE_TRIGGER_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing authorization token")

    if _pipeline_running:
        raise HTTPException(status_code=409, detail="Pipeline is already running")

    async def _run():
        global _pipeline_running
        _pipeline_running = True
        try:
            await run_full_pipeline(senator_filter=senator, fetch_only=fetch_only)
        except Exception:
            logger.exception("Pipeline run failed")
        finally:
            _pipeline_running = False

    asyncio.create_task(_run())
    return {"message": "Pipeline run triggered", "senator_filter": senator, "fetch_only": fetch_only}
