import logging

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session

from app.api.auth import check_pipeline_token
from app.api.pipeline_runner import run_pipeline_in_thread
from app.database import get_db
from app.models import PipelineRun
from app.pipeline.senate_pipeline import run_senate_pipeline
from app.schemas import PipelineRunSchema, PipelineStatusSchema

logger = logging.getLogger(__name__)

router = APIRouter()

def _is_pipeline_running(db: Session) -> bool:
    """Check the shared database for a currently running pipeline.

    A leftover row a dead run left is ignored (run_tracker.live_run), so it
    can't wedge the "is a pipeline already running?" guard.
    """
    from app.pipeline.run_tracker import run_in_progress

    return run_in_progress(db, PipelineRun)


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
def trigger_pipeline(
    authorization: str | None = Header(default=None),
    senator: str | None = Query(default=None, description="Filter to a single senator by name"),
    fetch_only: bool = Query(default=False, description="Stop after fetch phase (no LLM analysis)"),
    db: Session = Depends(get_db),
) -> dict:
    """Trigger a pipeline run. Requires Bearer token matching PIPELINE_TRIGGER_TOKEN.
    A plain def: the run-in-progress check is a blocking database read, so
    FastAPI runs it on the threadpool rather than the event loop."""
    check_pipeline_token(authorization)

    if _is_pipeline_running(db):
        raise HTTPException(status_code=409, detail="Pipeline is already running")

    async def _run_pipelines():
        from app.pipeline.fetch.district_pvi import run_house_on_sitting_lines
        from app.pipeline.house_pipeline import run_house_pipeline
        result = await run_senate_pipeline(senator_filter=senator, fetch_only=fetch_only)
        if senator is None and not fetch_only and result.get("status") not in ("skipped", "failed"):
            logger.info("Senate pipeline done — starting House pipeline")
            await run_house_on_sitting_lines(run_house_pipeline)

    run_pipeline_in_thread(_run_pipelines, name="pipeline-run", error_label="Pipeline run failed")
    return {"message": "Pipeline run triggered", "senator_filter": senator, "fetch_only": fetch_only}
