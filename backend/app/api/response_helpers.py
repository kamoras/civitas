"""Shared response helpers for the read-only politician/entity API routers.

cached_json was copy-pasted into six router modules (senators, representatives,
presidents, justices, bills, politicians) rather than written once — not a
deliberate per-router choice, just accretion as each entity type's routes
were added over time.
"""

from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session


def cached_json(data, max_age: int = 300) -> JSONResponse:
    """Wrap data in a JSONResponse with Cache-Control headers."""
    return JSONResponse(
        content=data,
        headers={"Cache-Control": f"public, max-age={max_age}, stale-while-revalidate={max_age}"},
    )


def score_history_json(db: Session, entity_type: str, entity_id: str, max_age: int = 3600) -> JSONResponse:
    """Historical score snapshots for a senator or representative.

    Was byte-identical between GET /senators/{id}/history and
    GET /representatives/{id}/history apart from the entity_type filter.
    """
    from app.models import ScoreSnapshot

    snapshots = (
        db.query(ScoreSnapshot)
        .filter(ScoreSnapshot.entity_type == entity_type, ScoreSnapshot.entity_id == entity_id)
        .order_by(ScoreSnapshot.date)
        .all()
    )
    return cached_json({
        "snapshots": [
            {
                "date": s.date,
                "overallScore": round(s.overall_score, 1),
                "algorithmVersion": s.algorithm_version,
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
    }, max_age=max_age)
