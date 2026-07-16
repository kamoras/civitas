"""Shared response helpers for the read-only politician/entity API routers.

cached_json was copy-pasted into six router modules (senators, representatives,
presidents, justices, bills, politicians) rather than written once — not a
deliberate per-router choice, just accretion as each entity type's routes
were added over time.
"""

from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

# Cache-Control max-age tiers shared across the read-only API routers —
# previously each router repeated its own bare-number max_age= literal
# (120, 300, 600, 3600...) with no indication of why that particular
# tier applied to that particular endpoint.
CACHE_TTL_SEARCH_S = 60          # rate-limited, CPU-intensive search endpoints
CACHE_TTL_DETAIL_S = 120         # single-entity detail/breakdown/history/highlights
CACHE_TTL_LIST_S = 300           # leaderboards and other paginated/aggregate lists
CACHE_TTL_REFERENCE_S = 600      # rarely-changing reference lists (e.g. states index)
CACHE_TTL_CONFIG_S = 3600        # config/weights — changes only on a deploy


def cached_json(data, max_age: int = CACHE_TTL_LIST_S) -> JSONResponse:
    """Wrap data in a JSONResponse with Cache-Control headers."""
    return JSONResponse(
        content=data,
        headers={"Cache-Control": f"public, max-age={max_age}, stale-while-revalidate={max_age}"},
    )


def score_history_json(db: Session, entity_type: str, entity_id: str, max_age: int = CACHE_TTL_CONFIG_S) -> JSONResponse:
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
