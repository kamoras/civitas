import logging

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from app.api.auth import check_pipeline_token
from app.database import get_db
from app.models import (
    ElectionPipelineRun,
    HousePipelineRun,
    PipelineRun,
    PipelineStatus,
    StockTradesPipelineRun,
    SupplementaryPipelineRun,
)
from app.pipeline.run_tracker import live_run, run_in_progress
from app.schemas import PipelineLinkSchema, PipelineRunSchema, PipelineStatusSchema

logger = logging.getLogger(__name__)

router = APIRouter()

# The nightly chain's links in the order it runs them (scheduler's chain),
# each with the name the records band prints for it.
CHAIN_LINKS = (
    ("senate", "Senate", PipelineRun),
    ("supplementary", "Supplementary", SupplementaryPipelineRun),
    ("house", "House", HousePipelineRun),
    ("stock_trades", "Stock trades", StockTradesPipelineRun),
    ("election", "Election", ElectionPipelineRun),
)


def _is_pipeline_running(db: Session) -> bool:
    """Check the shared database for a currently running pipeline.

    A leftover row a dead run left is ignored (run_tracker.live_run), so it
    can't wedge the "is a pipeline already running?" guard.
    """
    return run_in_progress(db, PipelineRun)


def _link_state(db: Session, model, run) -> str:
    """The band's word for a link's latest row. A RUNNING row counts as
    running only while live_run speaks for it; a dead one never finished,
    so it reads as failed rather than as a run still going."""
    if run.status == PipelineStatus.RUNNING:
        live = live_run(db, model)
        return "running" if live is not None and live.id == run.id else "failed"
    if run.status in (PipelineStatus.COMPLETED, PipelineStatus.PARTIAL):
        return run.status
    return "failed"


def chain_links(db: Session) -> list[PipelineLinkSchema]:
    """Each chain link's latest run, links that never ran left out."""
    links = []
    for key, label, model in CHAIN_LINKS:
        run = db.query(model).order_by(model.started_at.desc()).first()
        if run is None:
            continue
        links.append(PipelineLinkSchema(
            pipeline=key,
            label=label,
            state=_link_state(db, model, run),
            run_id=run.id,
            started_at=run.started_at,
            completed_at=run.completed_at,
        ))
    return links


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

    links = chain_links(db)
    completed = [link for link in links if link.state == "completed" and link.completed_at]
    return PipelineStatusSchema(
        last_run=last_run_schema,
        next_scheduled=next_time,
        is_running=_is_pipeline_running(db),
        links=links,
        links_completed=len(completed),
        filed_at=max((link.completed_at for link in completed), default=None),
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

    from app.api.admin import start_pipeline_trigger

    start_pipeline_trigger(db, senator, fetch_only, error_label="Pipeline run failed")
    return {"message": "Pipeline run triggered", "senator_filter": senator, "fetch_only": fetch_only}
