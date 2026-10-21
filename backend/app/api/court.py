"""The Supreme Court as a whole (/court): records and measurements, never a
score or a ranking (justice v3). See services/court_service.py."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.response_helpers import CACHE_TTL_LIST_S, cached_json
from app.database import get_db
from app.services import court_service

router = APIRouter(prefix="/court")


@router.get("")
def court(db: Session = Depends(get_db)):
    """The sitting justices by Martin-Quinn position, agreement between
    every pair, the Court's splits by term and the latest decisions."""
    return cached_json(court_service.court(db), max_age=CACHE_TTL_LIST_S)


@router.get("/finding")
def finding():
    """The Court-level finding on votes under the appointing president
    (app/data/court_finding.json); null when it can't be read."""
    return cached_json(court_service.appointer_finding(), max_age=CACHE_TTL_LIST_S)


@router.get("/decisions")
def decisions(
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=court_service.MAX_PER_PAGE),
    term: str | None = Query(None, pattern=r"^\d{4}$"),
    db: Session = Depends(get_db),
):
    """Decisions newest first, each with its lineup and sources."""
    return cached_json(court_service.decisions(db, page, per_page, term), max_age=CACHE_TTL_LIST_S)
