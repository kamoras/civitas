"""How often members vote with the president's stated positions (OMB
Statements of Administration Policy). Rewritten nightly by the
Supplementary pipeline; informational, not part of any score."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.response_helpers import CACHE_TTL_DETAIL_S, cached_json
from app.database import get_db
from app.member_ids import resolve_member_id
from app.services.presidential_support_service import chamber_view, member_view

router = APIRouter(prefix="/presidential-support")


@router.get("/member/{member_id}")
def member(member_id: str = Path(max_length=80), db: Session = Depends(get_db)) -> JSONResponse:
    view = member_view(db, resolve_member_id(db, member_id))
    if view is None:
        raise HTTPException(status_code=404, detail="No counted votes for this member")
    return cached_json(view, max_age=CACHE_TTL_DETAIL_S)


@router.get("/{chamber}")
def chamber(chamber: Literal["senate", "house"], db: Session = Depends(get_db)) -> JSONResponse:
    view = chamber_view(db, chamber)
    if view is None:
        raise HTTPException(status_code=404, detail="Not measured yet")
    return cached_json(view, max_age=CACHE_TTL_DETAIL_S)
