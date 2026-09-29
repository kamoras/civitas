from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.http_client import make_async_client
from app.models import PipelineRun
from app.schemas import HealthSchema

router = APIRouter()


@router.get("/live")
async def live() -> dict:
    """Liveness for the container health check: answered by the event
    loop itself, with no database read and no call to llama-server. A loop
    frozen by blocking work still fails it (the 2026-09-20 outage), but a
    slow llama-server no longer does: /health waits up to 5s on
    llama-server, the check's own timeout is 5s, so three slow answers from
    a sibling service marked this one unhealthy and Swarm replaced it,
    killing any pipeline run in progress (suspected 2026-09-29, a House run
    7 minutes in)."""
    return {"status": "ok"}


@router.get("/health", response_model=HealthSchema)
async def health_check(db: Session = Depends(get_db)) -> HealthSchema:
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "unavailable"

    llm_status = "ok"
    try:
        async with make_async_client(timeout=5.0) as client:
            if settings.LLM_BACKEND == "llama-server":
                resp = await client.get(f"{settings.LLAMA_SERVER_URL}/health")
            else:
                resp = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            if resp.status_code != 200:
                llm_status = "unavailable"
    except Exception:
        llm_status = "unavailable"

    last_run = (
        db.query(PipelineRun)
        .order_by(PipelineRun.started_at.desc())
        .first()
    )
    last_pipeline_ts = last_run.started_at if last_run else None

    overall = "ok" if db_status == "ok" else "degraded"

    return HealthSchema(
        status=overall,
        database=db_status,
        ollama=llm_status,
        last_pipeline_run=last_pipeline_ts,
    )
