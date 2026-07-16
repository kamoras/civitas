from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.highlights import build_highlights
from app.api.response_helpers import cached_json as _cached_json, score_history_json
from app.database import get_db

from app.services.senator_service import (
    get_leaderboard,
    get_senator_by_id,
    get_senator_stock_trades,
    get_senator_votes,
    get_senators_by_state,
    get_states_with_counts,
)

router = APIRouter()


@router.get("/config")
def get_config() -> JSONResponse:
    """Return all dynamic configuration for the frontend.

    Serves industries, platform categories, score weights, and policy areas
    so the frontend never needs to hardcode these values.
    """
    from app.config_definitions import (
        BILL_STAGES,
        INDUSTRIES,
        PLATFORM_CATEGORIES,
        POLICY_AREAS,
        PRESIDENT_SCORE_WEIGHTS,
        SCORE_WEIGHTS,
    )

    return _cached_json({
        "scoreWeights": SCORE_WEIGHTS,
        "presidentScoreWeights": PRESIDENT_SCORE_WEIGHTS,
        "industries": INDUSTRIES,
        "platformCategories": PLATFORM_CATEGORIES,
        "policyAreas": POLICY_AREAS,
        "billStages": BILL_STAGES,
    }, max_age=3600)


@router.get("/senators/states")
def list_states(db: Session = Depends(get_db)) -> JSONResponse:
    """Return all states that have senator data, with counts."""
    data = get_states_with_counts(db)
    return _cached_json([s.model_dump(by_alias=True) for s in data], max_age=300)


@router.get("/senators/leaderboard")
def list_leaderboard(db: Session = Depends(get_db)) -> JSONResponse:
    """Return all senators ranked by representation score."""
    data = get_leaderboard(db)
    return _cached_json([e.model_dump(by_alias=True) for e in data], max_age=300)


@router.get("/senators/{senator_id}/highlights")
async def get_highlights(senator_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    """Return data-driven highlights for a senator — no LLM, pure data."""
    senator = get_senator_by_id(db, senator_id)
    if senator is None:
        raise HTTPException(status_code=404, detail="Senator not found")

    highlights = build_highlights(senator.model_dump(by_alias=True))
    return _cached_json({"highlights": highlights[:5]}, max_age=120)


@router.get("/senators/{senator_id}/history")
def get_senator_history(senator_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    """Return historical score snapshots for a senator."""
    return score_history_json(db, "senator", senator_id)


@router.get("/senators/{senator_id}/votes")
def get_votes(
    senator_id: str,
    category: str = Query("recent", pattern="^(recent|key)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(15, ge=1, le=100),
    filter: str = Query("all", pattern="^(all|yea|nay|against-party)$"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Return paginated votes for a senator."""
    result = get_senator_votes(db, senator_id, category, page, per_page, filter)
    if result is None:
        raise HTTPException(status_code=404, detail="Senator not found")
    return _cached_json(result.model_dump(by_alias=True), max_age=120)


@router.get("/senators/{senator_id}/stock-trades")
def get_stock_trades(
    senator_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(15, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Return paginated STOCK Act trade disclosures for a senator."""
    result = get_senator_stock_trades(db, senator_id, page, per_page)
    if result is None:
        raise HTTPException(status_code=404, detail="Senator not found")
    return _cached_json(result.model_dump(by_alias=True), max_age=120)


@router.get("/senators/{senator_id}")
def get_senator(senator_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    """Return a single senator by ID."""
    result = get_senator_by_id(db, senator_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Senator not found")
    return _cached_json(result.model_dump(by_alias=True), max_age=120)


@router.get("/senators")
def list_senators(
    state: str = Query(..., min_length=2, max_length=2, description="Two-letter state code"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Return senators filtered by state."""
    data = get_senators_by_state(db, state)
    return _cached_json([s.model_dump(by_alias=True) for s in data], max_age=120)
