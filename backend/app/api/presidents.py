"""President API endpoints."""

import asyncio
import logging
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.config_definitions import PRESIDENT_SCORE_WEIGHTS
from app.database import SessionLocal, get_db
from app.api.response_helpers import cached_json as _cached_json
from app.services.president_service import (
    get_all_presidents,
    get_president,
    get_president_leaderboard,
    get_president_score_breakdown,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/presidents")


@router.get("/leaderboard")
def leaderboard(db: Session = Depends(get_db)):
    """All presidents ranked by weighted score."""
    data = get_president_leaderboard(db)
    return _cached_json([e.model_dump(by_alias=True) for e in data], max_age=300)


@router.get("/weights")
def weights():
    """Score weights for the presidential scorecard."""
    return _cached_json(PRESIDENT_SCORE_WEIGHTS, max_age=3600)


@router.post("/pipeline/trigger")
async def trigger_pipeline(
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
):
    """Trigger a president data pipeline run (fetches live data from APIs)."""
    if not settings.PIPELINE_TRIGGER_TOKEN:
        raise HTTPException(status_code=503, detail="Pipeline trigger token not configured")
    expected = f"Bearer {settings.PIPELINE_TRIGGER_TOKEN}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=403, detail="Invalid token")

    background_tasks.add_task(_run_pipeline_background)
    return {"status": "started", "message": "President pipeline triggered"}


def _run_pipeline_background():
    from app.pipeline.president_pipeline import run_president_pipeline

    db = SessionLocal()
    try:
        asyncio.run(run_president_pipeline(db))
    except Exception as e:
        logger.error("President pipeline failed: %s", e, exc_info=True)
    finally:
        db.close()


@router.get("/{president_id}")
def detail(president_id: str, db: Session = Depends(get_db)):
    """Single president detail by id (e.g. 'obama-44')."""
    result = get_president(db, president_id)
    if not result:
        raise HTTPException(status_code=404, detail="President not found")
    return _cached_json(result.model_dump(by_alias=True), max_age=120)


@router.get("/{president_id}/score-breakdown")
def score_breakdown(president_id: str, db: Session = Depends(get_db)):
    """Full component-level derivation behind each of a president's scored
    dimensions — the "show the math" panel's data source. Independence/
    Follow-Through/Public Mandate are always pure editorial estimates
    ({"seedOnly": true}); Competence/Effectiveness/Agency Alignment are a
    live formula only for presidents with fetched data (see
    president_pipeline.DYNAMIC_PRESIDENTS/ECONOMICS_ONLY_PRESIDENTS)."""
    breakdown = get_president_score_breakdown(db, president_id)
    if breakdown is None:
        raise HTTPException(status_code=404, detail="President not found")
    return _cached_json(breakdown, max_age=120)


@router.get("")
def list_all(db: Session = Depends(get_db)):
    """All presidents, newest first."""
    data = get_all_presidents(db)
    return _cached_json([p.model_dump(by_alias=True) for p in data], max_age=300)
