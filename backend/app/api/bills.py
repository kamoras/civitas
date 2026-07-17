"""
Bills-in-flight API — all bills currently moving through Congress,
unioned across the Senate and House, for the process-flow visualization.
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.response_helpers import CACHE_TTL_DETAIL_S, PARTY_QUERY_PATTERN, cached_json
from app.database import get_db
from app.services.bill_service import get_bills_in_flight

router = APIRouter()


def _cached_json(data, max_age: int = CACHE_TTL_DETAIL_S) -> JSONResponse:
    return cached_json(data, max_age=max_age)


@router.get("/bills")
def list_bills_in_flight(
    stage: str | None = Query(None, max_length=32),
    chamber: str | None = Query(None, pattern="^(senate|house)$"),
    party: str | None = Query(None, pattern=PARTY_QUERY_PATTERN),
    q: str | None = Query(None, max_length=100),
    sort: str = Query("recent", pattern="^(recent|hot|stale)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Return bills currently moving through Congress, paginated and filterable.

    sort=hot restricts to bills currently referenced by a live Action
    Center issue, ranked by mention count. sort=stale orders oldest-action-
    first — pass `stage` alongside it, since "stuck" is stage-relative.
    """
    data = get_bills_in_flight(
        db, stage=stage, chamber=chamber, party=party, q=q, sort=sort, page=page, per_page=per_page,
    )
    return _cached_json(data.model_dump(by_alias=True), max_age=CACHE_TTL_DETAIL_S)
