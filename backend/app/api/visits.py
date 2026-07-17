"""Privacy-respecting unique-visitor tracking.

No raw IP or User-Agent is ever stored. `POST /api/track-visit` is fired by
the frontend's middleware on real page views and records only a salted,
daily-rotating hash — see SiteVisit in models.py for why the same visitor
is unrecoverable across days and why this table can't grow per-request.
"""

import hashlib
import hmac
import logging
import re
from datetime import datetime, UTC

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import PageView, SiteVisit

logger = logging.getLogger(__name__)

router = APIRouter()

# Structural User-Agent parsing (RFC-shaped, not a classification decision —
# UA tokens like "Chrome/" or "Firefox/" are a fixed, documented format, not
# ambiguous real-world data). Order matters: Edge and Opera UAs also contain
# "Chrome/" and "Safari/" boilerplate, so the more specific token must be
# checked first.
def _parse_browser(ua: str) -> str:
    if re.search(r"Edg/", ua):
        return "Edge"
    if re.search(r"OPR/|Opera", ua):
        return "Opera"
    if re.search(r"Firefox/", ua):
        return "Firefox"
    if re.search(r"Chrome/", ua):
        return "Chrome"
    if re.search(r"Safari/", ua):
        return "Safari"
    return "Other"


def _parse_os(ua: str) -> str:
    if re.search(r"Windows", ua):
        return "Windows"
    if re.search(r"Android", ua):
        return "Android"
    if re.search(r"iPhone|iPad|iPod|iOS", ua):
        return "iOS"
    if re.search(r"Macintosh|Mac OS X", ua):
        return "macOS"
    if re.search(r"Linux", ua):
        return "Linux"
    return "Other"


def _parse_device(ua: str) -> str:
    if re.search(r"iPad|Tablet", ua):
        return "tablet"
    if re.search(r"Mobile|iPhone|Android", ua):
        return "mobile"
    return "desktop"


def _hash_key() -> bytes:
    # Derived from ADMIN_TOKEN rather than a separate secret so there's
    # nothing new to configure — same bar as admin-panel access already
    # requires, and unique per self-hosted deployment.
    token = settings.ADMIN_TOKEN or settings.PIPELINE_TRIGGER_TOKEN or "civitas"
    return hashlib.sha256(f"{token}:visitor-salt".encode()).digest()


def _visitor_hash(ip: str, user_agent: str, date: str) -> str:
    msg = f"{ip}:{user_agent}:{date}".encode()
    return hmac.new(_hash_key(), msg, hashlib.sha256).hexdigest()[:32]


_KNOWN_STATIC_PATHS = {
    "/", "/about", "/accessibility", "/action", "/bills", "/changelog",
    "/compare", "/environmental", "/explore", "/feedback", "/leaderboard",
    "/politicians",
    # /scorecard has no page anymore (renamed) but old Bluesky posts still
    # link to it — kept here so that 404 traffic stays visibly labeled
    # "/scorecard" instead of draining into the unlabeled "/other" bucket.
    "/scorecard",
}
_DYNAMIC_PREFIXES = ("/politicians/", "/issue/", "/explore/")


def _normalize_path(raw: str) -> str:
    """Collapse a request path to a stable route template for aggregation.

    /politicians/chuck-grassley -> /politicians/[id], not its own row per
    politician — otherwise "most visited pages" would fragment across
    every individual id instead of showing which routes get read. Anything
    outside the known route set (bad input, a since-removed route) buckets
    to "/other" rather than growing the table with arbitrary strings from
    an unauthenticated, public endpoint.
    """
    path = (raw or "/").split("?")[0].rstrip("/") or "/"
    if path in _KNOWN_STATIC_PATHS:
        return path
    for prefix in _DYNAMIC_PREFIXES:
        if path.startswith(prefix) and len(path) > len(prefix):
            return f"{prefix}[id]"
    return "/other"


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
async def track_visit(
    request: Request,
    path: str = Query("/"),
    db: Session = Depends(get_db),
) -> None:
    ip = _track_ip(request)
    user_agent = request.headers.get("User-Agent", "")
    date = datetime.now(UTC).date().isoformat()
    visitor_hash = _visitor_hash(ip, user_agent, date)

    # INSERT OR IGNORE on the (date, visitor_hash) primary key — a repeat
    # visit the same day is a cheap no-op, not a duplicate row.
    stmt = sqlite_insert(SiteVisit).values(
        date=date,
        visitor_hash=visitor_hash,
        browser=_parse_browser(user_agent),
        os=_parse_os(user_agent),
        device_type=_parse_device(user_agent),
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["date", "visitor_hash"])
    db.execute(stmt)

    # Unlike SiteVisit, this counts every page view, not unique visitors —
    # a repeat request the same day increments count rather than no-op'ing.
    normalized_path = _normalize_path(path)
    page_stmt = sqlite_insert(PageView).values(date=date, path=normalized_path, count=1)
    page_stmt = page_stmt.on_conflict_do_update(
        index_elements=["date", "path"],
        set_={"count": PageView.count + 1},
    )
    db.execute(page_stmt)
    db.commit()
