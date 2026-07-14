"""
Bills-in-flight API — all bills currently moving through Congress,
unioned across the Senate and House, for the process-flow visualization.
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.bill_service import get_bills_in_flight

router = APIRouter()


def _cached_json(data, max_age: int = 120) -> JSONResponse:
    return JSONResponse(
        content=data,
        headers={"Cache-Control": f"public, max-age={max_age}, stale-while-revalidate={max_age}"},
    )


@router.get("/bills")
def list_bills_in_flight(
    stage: str | None = Query(None, max_length=32),
    chamber: str | None = Query(None, pattern="^(senate|house)$"),
    party: str | None = Query(None, pattern="^[DRI]$"),
    q: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Return bills currently moving through Congress, paginated and filterable."""
    data = get_bills_in_flight(
        db, stage=stage, chamber=chamber, party=party, q=q, page=page, per_page=per_page,
    )
    return _cached_json(data.model_dump(by_alias=True), max_age=120)
