"""Record cards (app/services/record_cards.py): the data an image of one
official record shows, and its alt text. Read by the frontend's /api/og
renderer; id-based, so any post or page can show the same record the same
way.

`/api/cards/posts/<id>.jpg` is a stored post's own picture as a JPEG, for
Instagram, which takes no PNG and fetches the picture itself
(broadcast.post_image_jpeg). It takes a post id, never a URL.
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from app.api.response_helpers import JSONResponse
from sqlalchemy.orm import Session

from app.api.response_helpers import cached_json
from app import broadcast
from app.database import get_db
from app.models import BroadcastPost
from app.services.record_cards import card_for_key

router = APIRouter(prefix="/cards")


@router.get("/posts/{post_id}.jpg")
def post_picture(post_id: int = Path(ge=1), db: Session = Depends(get_db)) -> Response:
    post = db.get(BroadcastPost, post_id)
    data = None
    if post is not None and post.subject not in broadcast.withdrawn_subjects():
        data = broadcast.post_image_jpeg(post)
    if data is None:
        raise HTTPException(status_code=404, detail="No such picture")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


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
