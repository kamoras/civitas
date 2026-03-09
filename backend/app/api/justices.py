"""Supreme Court justice API endpoints."""

import asyncio
import logging
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, get_db
from app.services.justice_service import (
    get_all_justices,
    get_justice,
    get_justice_leaderboard,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/justices")


def _cached_json(data, max_age: int = 300) -> JSONResponse:
    return JSONResponse(
        content=data,
        headers={"Cache-Control": f"public, max-age={max_age}, stale-while-revalidate={max_age}"},
    )


@router.get("/leaderboard")
def leaderboard(db: Session = Depends(get_db)):
    """All active justices ranked by weighted impartiality score."""
    data = get_justice_leaderboard(db)
    return _cached_json([e.model_dump(by_alias=True) for e in data], max_age=300)


@router.get("/weights")
def weights():
    """Score weight breakdown for the justice scorecard."""
    return _cached_json({
        "consistency": {"weight": 0.35, "label": "Ideological Consistency", "description": "How unpredictable are their votes? Low bloc-alignment = high consistency (follows law, not party)."},
        "independence": {"weight": 0.30, "label": "Independence", "description": "How often they break from their appointing-party's expected voting bloc in split decisions."},
        "bipartisanAgreement": {"weight": 0.15, "label": "Bipartisan Agreement", "description": "Fraction of cases decided unanimously or near-unanimously (broad consensus)."},
        "judicialRestraint": {"weight": 0.20, "label": "Judicial Restraint", "description": "Balanced dissent patterns — neither rubber-stamping everything nor constant ideological dissent."},
    }, max_age=3600)


@router.post("/pipeline/trigger")
async def trigger_pipeline(
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
):
    """Trigger a justice data pipeline run (fetches from Oyez API)."""
    if settings.PIPELINE_TRIGGER_TOKEN:
        expected = f"Bearer {settings.PIPELINE_TRIGGER_TOKEN}"
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=403, detail="Invalid token")

    background_tasks.add_task(_run_pipeline_background)
    return {"status": "started", "message": "Justice pipeline triggered"}


def _run_pipeline_background():
    from app.pipeline.justice_pipeline import run_justice_pipeline

    db = SessionLocal()
    try:
        asyncio.run(run_justice_pipeline(db))
    except Exception as e:
        logger.error("Justice pipeline failed: %s", e, exc_info=True)
    finally:
        db.close()


@router.get("/{justice_id}")
def detail(justice_id: str, db: Session = Depends(get_db)):
    """Single justice detail by id (e.g. 'john_g_roberts_jr')."""
    result = get_justice(db, justice_id)
    if not result:
        raise HTTPException(status_code=404, detail="Justice not found")
    return _cached_json(result.model_dump(by_alias=True), max_age=120)


@router.get("")
def list_all(db: Session = Depends(get_db)):
    """All active justices."""
    data = get_all_justices(db)
    return _cached_json([j.model_dump(by_alias=True) for j in data], max_age=300)
