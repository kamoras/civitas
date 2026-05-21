from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.representative_service import (
    get_rep_leaderboard,
    get_rep_states_with_counts,
    get_rep_votes,
    get_representative_by_id,
    get_representatives_by_state,
)

router = APIRouter()


def _cached_json(data, max_age: int = 300) -> JSONResponse:
    return JSONResponse(
        content=data,
        headers={"Cache-Control": f"public, max-age={max_age}, stale-while-revalidate={max_age}"},
    )


@router.get("/representatives/states")
def list_rep_states(db: Session = Depends(get_db)) -> JSONResponse:
    data = get_rep_states_with_counts(db)
    return _cached_json(data, max_age=300)


@router.get("/representatives/leaderboard")
def list_rep_leaderboard(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    party: str | None = Query(None, pattern="^[DRI]$"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    data = get_rep_leaderboard(db, page=page, per_page=per_page, party=party)
    return _cached_json(data, max_age=300)


@router.get("/representatives/{rep_id}/history")
def get_rep_history(rep_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    """Return historical score snapshots for a representative."""
    from app.models import ScoreSnapshot
    snapshots = (
        db.query(ScoreSnapshot)
        .filter(ScoreSnapshot.entity_type == "representative", ScoreSnapshot.entity_id == rep_id)
        .order_by(ScoreSnapshot.date)
        .all()
    )
    return _cached_json({
        "snapshots": [
            {
                "date": s.date,
                "overallScore": round(s.overall_score, 1),
                "scores": {
                    "fundingIndependence": round(s.score_1, 1),
                    "promisePersistence": round(s.score_2, 1),
                    "independentVoting": round(s.score_3, 1),
                    "fundingDiversity": round(s.score_4, 1),
                    "legislativeEffectiveness": round(s.score_5, 1),
                },
            }
            for s in snapshots
        ]
    }, max_age=3600)


@router.get("/representatives/{rep_id}/votes")
def get_votes(
    rep_id: str,
    category: str = Query("recent", pattern="^(recent|key)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(15, ge=1, le=100),
    filter: str = Query("all", pattern="^(all|yea|nay|against-party)$"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = get_rep_votes(db, rep_id, category, page, per_page, filter)
    if result is None:
        raise HTTPException(status_code=404, detail="Representative not found")
    return _cached_json(result, max_age=120)


@router.get("/representatives/{rep_id}")
def get_representative(rep_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    result = get_representative_by_id(db, rep_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Representative not found")
    return _cached_json(result, max_age=120)


@router.get("/representatives")
def list_representatives(
    state: str = Query(..., min_length=2, max_length=2, description="Two-letter state code"),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> JSONResponse:
    data = get_representatives_by_state(db, state, page=page, per_page=per_page)
    return _cached_json(data, max_age=120)
