"""The source of a stored photo, for the frontend's photo route.

`/photo/justice/<id>` and `/photo/issue/<id>` (see app/photos.py) serve a
picture whose original URL the pipeline stored. The route asks here for
it, by kind and id, and fetches it server-side; nothing a client sends ever
becomes the URL fetched. Only a source on its kind's hosts is returned
(app/photos.py's PHOTO_SOURCE_HOSTS).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ActionIssue, Justice
from app.photos import photo_source_allowed

router = APIRouter()

# Long enough for any real id, short enough that a made-up one costs nothing.
_MAX_ID_LEN = 64


def _stored_source(kind: str, photo_id: str, db: Session) -> str | None:
    if kind == "justice":
        row = db.get(Justice, photo_id)
        return row.thumbnail_url if row else None
    if kind == "issue" and photo_id.isdigit() and len(photo_id) <= 18:
        row = db.get(ActionIssue, int(photo_id))
        return row.image_url if row else None
    return None


@router.get("/photo-sources/{kind}/{photo_id}")
def get_photo_source(kind: str, photo_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    source = _stored_source(kind, photo_id, db) if len(photo_id) <= _MAX_ID_LEN else None
    if not photo_source_allowed(kind, source):
        raise HTTPException(status_code=404, detail="No such photo")
    return {"url": source}
