import logging

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session

from app.api.auth import check_pipeline_token
from app.database import get_db
from app.models import PipelineRun
from app.schemas import PipelineRunSchema, PipelineStatusSchema

logger = logging.getLogger(__name__)

router = APIRouter()

def _is_pipeline_running(db: Session) -> bool:
    """Check the shared database for a currently running Senate pipeline.

    A leftover row a dead run left is ignored (run_tracker.live_run), so it
    can't wedge the "is a pipeline already running?" guard.
    """
    from app.pipeline.run_tracker import run_in_progress

    return run_in_progress(db, PipelineRun)


def queue_chain(links, *, kind: str, name: str, error_label: str) -> bool:
    """Start a chain of pipelines (app.pipeline_chain) in a thread of its
    own, reported under "Triggered"; True when it waits behind a chain in
    progress (the response says it is queued, not started). A 409 when it
    would repeat what a live chain will do — a full run during a full run,
    a pipeline another chain has yet to start — rather than run it twice."""
    import asyncio

    from app.background import start_waiting_writer
    from app.pipeline_chain import chain_running, leave, reserve
    from app.scheduler import chain_of

    queued = chain_running()
    reserved, why = reserve(kind, [link.label for link in links])
    if reserved is None:
        raise HTTPException(status_code=409, detail=f"Not started: {why}")
    chain = chain_of(links, "Triggered", kind=kind, reserved=reserved)

    def _run() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(chain())
        except BaseException:
            logger.exception(error_label)
        finally:
            leave(reserved)
            loop.close()

    try:
        start_waiting_writer(_run, name=name)
    except BaseException:
        leave(reserved)  # never started
        raise
    return queued


def start_triggered_chain(senator: str | None, fetch_only: bool, error_label: str) -> bool:
    """What both pipeline triggers run: the nightly chain's five pipelines,
    each whatever the one before it did — so a trigger recovers any of
    them, not only the first — refused while another full run is in
    progress. A single senator or a fetch-only run is that Senate run
    alone. True when it is queued behind a chain in progress."""
    from app.pipeline_chain import FULL
    from app.scheduler import filtered_senate_link, nightly_links

    if senator is not None or fetch_only:
        return queue_chain([filtered_senate_link(senator, fetch_only)], kind="", name="pipeline-run",
                           error_label=error_label)
    return queue_chain(nightly_links(), kind=FULL, name="pipeline-run", error_label=error_label)


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
    check_pipeline_token(authorization)
    queued = start_triggered_chain(senator, fetch_only, "Pipeline run failed")
    return {
        "message": "Pipeline run queued behind the one in progress" if queued else "Pipeline run triggered",
        "queued": queued, "senator_filter": senator, "fetch_only": fetch_only,
    }
