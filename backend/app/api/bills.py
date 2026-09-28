"""
Bills-in-flight API — all bills currently moving through Congress,
unioned across the Senate and House, for the process-flow visualization.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.rate_limit import UpstreamRouteLimit, spend_upstream
from app.api.response_helpers import CACHE_TTL_DETAIL_S, PARTY_QUERY_PATTERN, cached_json
from app.database import get_db
from app.http_client import make_async_client
from app.pipeline.fetch.congress import expected_current_congress
from app.services.bill_record import fetch_bill_record, parse_bill_id, shape_record
from app.services.bill_service import get_bill_detail, get_bills_in_flight

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


@router.get("/bills/{bill_id}")
def get_bill(
    bill_id: str,
    congress: int | None = Query(None, ge=93, le=200),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Return full detail for a single bill by its bill_id (e.g. "S.4967"):
    of `congress` when given, else the newest one held."""
    detail = get_bill_detail(db, bill_id, congress)
    if detail is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    return _cached_json(detail.model_dump(by_alias=True), max_age=CACHE_TTL_DETAIL_S)


@router.get("/bills/{bill_id}/record")
async def get_bill_record(
    _rl: UpstreamRouteLimit,
    bill_id: str,
    congress: int | None = Query(None, ge=93, le=200),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Any bill's public record: Congress.gov's summary, sponsors, actions
    and text versions, and every stored roll call on it with each party's
    split. `congress` defaults to the current one.

    A cache miss fetches from Congress.gov on the pipeline's own key, so the
    route is rate-limited and its upstream calls budgeted (rate_limit.py);
    a congress that hasn't begun is refused before anything is fetched."""
    if parse_bill_id(bill_id) is None:
        raise HTTPException(status_code=404, detail="Not a bill id")
    current = expected_current_congress()
    congress = congress or current
    if congress > current:
        raise HTTPException(status_code=404, detail="That Congress hasn't convened")
    async with make_async_client() as client:
        raw = await fetch_bill_record(client, db, congress, bill_id, spend=spend_upstream)
    if raw["not_found"]:
        raise HTTPException(status_code=404, detail="Bill not found")
    return _cached_json(shape_record(db, congress, bill_id, raw), max_age=CACHE_TTL_DETAIL_S)
