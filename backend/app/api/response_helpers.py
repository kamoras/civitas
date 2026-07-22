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

# Party-filter query param validation, shared across every router that
# exposes a ?party= filter — previously each repeated the same regex
# literal independently.
PARTY_QUERY_PATTERN = r"^[DRI]$"


def cached_json(data, max_age: int = CACHE_TTL_LIST_S) -> JSONResponse:
    """Wrap data in a JSONResponse with Cache-Control headers."""
    return JSONResponse(
        content=data,
        headers={"Cache-Control": f"public, max-age={max_age}, stale-while-revalidate={max_age}"},
    )


# Default score_1..score_5 -> dimension-name mapping (senators/reps share
# this shape). Presidents use a different set of dimensions on the same
# generic score_1..score_5 slots (see PRESIDENT_DIMENSION_LABELS below) —
# score_history_json takes a labels map instead of hardcoding one, so it
# stays the single shared implementation rather than forking a near-
# identical copy per entity type.
SENATOR_DIMENSION_LABELS = {
    "score_1": "fundingIndependence",
    "score_2": "promisePersistence",
    "score_3": "independentVoting",
    "score_4": "fundingDiversity",
    "score_5": "legislativeEffectiveness",
}

# score_5 = Historical Legacy (added 2026-07) — see
# president_pipeline._record_president_snapshots.
PRESIDENT_DIMENSION_LABELS = {
    "score_1": "publicMandate",
    "score_2": "effectiveness",
    "score_3": "competence",
    "score_4": "agencyAlignment",
    "score_5": "historicalLegacy",
}


def score_history_json(
    db: Session,
    entity_type: str,
    entity_id: str,
    max_age: int = CACHE_TTL_CONFIG_S,
    dimension_labels: dict[str, str] = SENATOR_DIMENSION_LABELS,
) -> JSONResponse:
    """Historical score snapshots for any entity type backed by the
    generic ScoreSnapshot table (senator/representative/president).

    Was byte-identical between GET /senators/{id}/history and
    GET /representatives/{id}/history apart from the entity_type filter;
    generalized (2026-07) to take a dimension_labels map so
    GET /presidents/{id}/history — a different set of dimension names on
    the same score_1..score_5 slots — reuses this instead of forking a
    near-identical copy.
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
                    label: round(getattr(s, slot), 1)
                    for slot, label in dimension_labels.items()
                },
            }
            for s in snapshots
        ]
    }, max_age=max_age)
