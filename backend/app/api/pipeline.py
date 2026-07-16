import asyncio
import logging
import secrets
import threading

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import PipelineRun, PipelineStatus
from app.pipeline.senate_pipeline import run_senate_pipeline
from app.schemas import PipelineRunSchema, PipelineStatusSchema

logger = logging.getLogger(__name__)

router = APIRouter()


def _is_pipeline_running(db: Session) -> bool:
    """Check the shared database for a currently running pipeline.

    A run older than 2 hours is considered stale and ignored.
    """
    from datetime import datetime, timedelta

    cutoff = datetime.utcnow() - timedelta(hours=12)
    return (
        db.query(PipelineRun)
        .filter(PipelineRun.status == PipelineStatus.RUNNING, PipelineRun.started_at > cutoff)
        .first()
        is not None
    )


@router.get("/pipeline/status", response_model=PipelineStatusSchema)
def pipeline_status(db: Session = Depends(get_db)) -> PipelineStatusSchema:
    """Return the last pipeline run info and next scheduled time."""
    db.expire_all()
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
            current_phase=last_run.current_phase,
            senators_processed=last_run.senators_processed,
            senators_total=last_run.senators_total or 0,
            senators_failed=last_run.senators_failed,
            bills_classified=last_run.bills_classified,
            llm_calls=last_run.llm_calls,
            cache_hits=last_run.cache_hits,
            cache_misses=last_run.cache_misses,
            elapsed_seconds=last_run.elapsed_seconds,
            error_message=last_run.error_message,
        )

    try:
        from app.scheduler import get_next_run_time
        next_time = get_next_run_time()
    except Exception:
        next_time = None

    return PipelineStatusSchema(
        last_run=last_run_schema,
        next_scheduled=next_time,
        is_running=_is_pipeline_running(db),
    )


@router.post("/pipeline/trigger")
async def trigger_pipeline(
    authorization: str | None = Header(default=None),
    senator: str | None = Query(default=None, description="Filter to a single senator by name"),
    fetch_only: bool = Query(default=False, description="Stop after fetch phase (no LLM analysis)"),
    db: Session = Depends(get_db),
) -> dict:
    """Trigger a pipeline run. Requires Bearer token matching PIPELINE_TRIGGER_TOKEN."""
    if not settings.PIPELINE_TRIGGER_TOKEN:
        raise HTTPException(status_code=503, detail="Pipeline trigger token not configured")

    expected = f"Bearer {settings.PIPELINE_TRIGGER_TOKEN}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing authorization token")

    if _is_pipeline_running(db):
        raise HTTPException(status_code=409, detail="Pipeline is already running")

    def _run_in_thread():
        from app.pipeline.house_pipeline import run_house_pipeline
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                run_senate_pipeline(senator_filter=senator, fetch_only=fetch_only)
            )
            if senator is None and not fetch_only and result.get("status") not in ("skipped", "failed"):
                logger.info("Senate pipeline done — starting House pipeline")
                loop.run_until_complete(run_house_pipeline())
        except BaseException:
            logger.exception("Pipeline run failed")
        finally:
            loop.close()

    threading.Thread(target=_run_in_thread, daemon=True, name="pipeline-run").start()
    return {"message": "Pipeline run triggered", "senator_filter": senator, "fetch_only": fetch_only}
