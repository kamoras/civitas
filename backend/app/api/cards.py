"""Record cards (app/services/record_cards.py): the data an image of one
official record shows, and its alt text. Read by the frontend's /api/og
renderer; id-based, so any post or page can show the same record the same
way."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.response_helpers import cached_json
from app.database import get_db
from app.services.record_cards import card_for_key

router = APIRouter(prefix="/cards")


@router.get("/{key:path}")
def record_card(
    key: str = Path(max_length=80, pattern=r"^(vote|bill|event|document):[A-Za-z0-9./]+$"),
    title: int = Query(1, ge=0, le=1, description="0 leaves a bill's or vote's title out"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    card = card_for_key(db, key, show_title=bool(title))
    if card is None:
        raise HTTPException(status_code=404, detail="No such record")
    return cached_json(card.as_json(), max_age=3600)
