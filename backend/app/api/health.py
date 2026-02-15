import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import PipelineRun
from app.schemas import HealthSchema

router = APIRouter()


@router.get("/health", response_model=HealthSchema)
async def health_check(db: Session = Depends(get_db)) -> HealthSchema:
    # Check database
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "unavailable"

    # Check Ollama
    ollama_status = "ok"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            if resp.status_code != 200:
                ollama_status = "unavailable"
    except Exception:
        ollama_status = "unavailable"

    # Last pipeline run
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
        ollama=ollama_status,
        last_pipeline_run=last_pipeline_ts,
    )
