"""Privacy-respecting unique-visitor tracking.

No raw IP or User-Agent is ever stored. `POST /api/track-visit` is fired by
the frontend's middleware on real page views and records only a salted,
daily-rotating hash — see SiteVisit in models.py for why the same visitor
is unrecoverable across days and why this table can't grow per-request.
"""

import hashlib
import hmac
import logging
from datetime import datetime, UTC

from fastapi import APIRouter, Depends, Request
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import SiteVisit

logger = logging.getLogger(__name__)

router = APIRouter()


def _hash_key() -> bytes:
    # Derived from ADMIN_TOKEN rather than a separate secret so there's
    # nothing new to configure — same bar as admin-panel access already
    # requires, and unique per self-hosted deployment.
    token = settings.ADMIN_TOKEN or settings.PIPELINE_TRIGGER_TOKEN or "civitas"
    return hashlib.sha256(f"{token}:visitor-salt".encode()).digest()


def _visitor_hash(ip: str, user_agent: str, date: str) -> str:
    msg = f"{ip}:{user_agent}:{date}".encode()
    return hmac.new(_hash_key(), msg, hashlib.sha256).hexdigest()[:32]


def _track_ip(request: Request) -> str:
    # NOT app.api.rate_limit.client_ip(): that function only trusts
    # X-Forwarded-For when the direct TCP peer is nginx (127.0.0.1), which
    # is right for rate-limiting but wrong here. This endpoint is called
    # by the frontend's own middleware directly over the internal Docker
    # network (frontend -> backend:8000), bypassing nginx entirely, so the
    # TCP peer is the frontend container, never 127.0.0.1. The frontend
    # middleware already received a trustworthy X-Real-IP from nginx for
    # the original browser request and relays it unchanged — trusting it
    # here is reasonable because both hops (nginx->frontend, frontend-
    # >backend) are on infra this deployment controls, not the public
    # internet (backend:8000 isn't reachable outside the Docker network).
    forwarded = request.headers.get("X-Real-IP")
    if forwarded:
        return forwarded
    peer = request.client.host if request.client else None
    return peer or "unknown"


@router.post("/track-visit", status_code=204)
async def track_visit(request: Request, db: Session = Depends(get_db)) -> None:
    ip = _track_ip(request)
    user_agent = request.headers.get("User-Agent", "")
    date = datetime.now(UTC).date().isoformat()
    visitor_hash = _visitor_hash(ip, user_agent, date)

    # INSERT OR IGNORE on the (date, visitor_hash) primary key — a repeat
    # visit the same day is a cheap no-op, not a duplicate row.
    stmt = sqlite_insert(SiteVisit).values(date=date, visitor_hash=visitor_hash)
    stmt = stmt.on_conflict_do_nothing(index_elements=["date", "visitor_hash"])
    db.execute(stmt)
    db.commit()
