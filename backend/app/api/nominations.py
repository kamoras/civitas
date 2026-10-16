"""Presidential nominations (services/nomination_service.py): the list, the
Congress's counts and time to confirm, and one nomination."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.response_helpers import cached_json
from app.config import settings
from app.database import get_db
from app.services import nomination_service

router = APIRouter(prefix="/nominations")

# The sync is daily; an hour keeps a page close to it.
_TTL_S = 3600


@router.get("")
def list_nominations(
    status: str | None = Query(None, pattern="^(pending|confirmed|returned|withdrawn)$"),
    kind: str | None = Query(None, pattern="^(civilian|judicial|military)$"),
    agency: str | None = Query(None, max_length=300, description="The organization, as Congress.gov names it"),
    q: str | None = Query(None, max_length=200, description="Words in the nomination's description"),
    congress: int | None = Query(None, ge=93, le=200),
    page: int = Query(1, ge=1, le=1000),
    per_page: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """The Congress's nominations, newest action first, narrowed by status,
    kind, organization and words."""
    return cached_json(nomination_service.list_nominations(
        db, congress or settings.CURRENT_CONGRESS, status=status, kind=kind, agency=agency, q=q,
        page=page, per_page=per_page,
    ), max_age=_TTL_S)


@router.get("/stats")
def nomination_stats(
    congress: int | None = Query(None, ge=93, le=200), db: Session = Depends(get_db),
) -> JSONResponse:
    """Counts by kind and status, and the days from receipt to confirmation
    as a histogram with its quartiles."""
    return cached_json(nomination_service.stats(db, congress or settings.CURRENT_CONGRESS), max_age=_TTL_S)


@router.get("/{congress}/{citation}")
def nomination(
    congress: int = Path(ge=93, le=200),
    citation: str = Path(pattern=r"^PN[1-9]\d{0,4}(-[1-9]\d{0,2})?$"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """One nomination: its record, actions, hearings and the roll calls that
    name it, with every senator's position on the vote to confirm."""
    found = nomination_service.detail(db, congress, citation)
    if found is None:
        raise HTTPException(status_code=404, detail="Nomination not recorded")
    return cached_json(found, max_age=_TTL_S)
