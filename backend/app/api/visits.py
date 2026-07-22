"""Privacy-respecting unique-visitor tracking.

No raw IP or User-Agent is ever stored. `POST /api/track-visit` is fired by
the frontend's middleware on real page views and records only a salted,
daily-rotating hash — see SiteVisit in models.py for why the same visitor
is unrecoverable across days and why this table can't grow per-request.
"""

import asyncio
import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from datetime import datetime, UTC

from fastapi import APIRouter, Query, Request
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError, TimeoutError as SATimeoutError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import VisitsSessionLocal
from app.models import PageView, SiteVisit

logger = logging.getLogger(__name__)

router = APIRouter()

# 2026-07 incident: this endpoint fires on every real page view — by far
# the highest-frequency write in the app — and used to write directly to
# the DB inline. Two compounding problems, both real:
#
# 1. SiteVisit/PageView shared the main SQLite file (and connection pool)
#    with the nightly pipeline, which can hold SQLite's single writer
#    lock for extended stretches while processing a batch between
#    commits. A blocked write here held a pool connection for the full
#    30s default busy-timeout; under real concurrent traffic during a
#    pipeline run, that exhausted the pool fast, piling up blocked
#    requests until the container hit its memory limit and got OOM-killed.
# 2. Separately (caught in review before this shipped): the route was
#    `async def` but did its DB write synchronously inline — a blocking
#    SQLite call inside `async def` runs directly on the event loop, so
#    it doesn't just hold up ITS OWN request, it freezes every other
#    concurrent request the whole process is serving, for however long
#    the write takes. Moving the write off the request path entirely
#    (below) fixes this at the root rather than just shortening how long
#    each block lasts.
#
# Fix: track_visit no longer touches the database at all. It computes
# the (already-fast, non-blocking) hash/normalization work and enqueues
# a plain event; a single background consumer (run_visit_consumer,
# started from app.main's lifespan) drains the queue and writes in small
# batches via asyncio.to_thread, off the event loop entirely. This also
# means SiteVisit/PageView only ever have ONE writer (the consumer), so
# there's no concurrent-writer contention left to guard against even
# without the separate database file below — that split (see
# database.py's _derive_visits_database_url) is kept anyway as
# defense-in-depth: it means even a pathological consumer stall can't
# contend with the nightly pipeline's own writes to the main database.
#
# Bounded, not unbounded: under sustained overload the queue fills and
# new events are dropped (logged, never blocking the producer) rather
# than growing memory without limit — the same "best-effort, not
# guaranteed" contract this table already had.
_VISIT_QUEUE_MAXSIZE = 1000
_VISIT_BATCH_MAX = 50
_visit_queue: "asyncio.Queue[_VisitEvent]" = asyncio.Queue(maxsize=_VISIT_QUEUE_MAXSIZE)


@dataclass(frozen=True)
class _VisitEvent:
    date: str
    visitor_hash: str
    browser: str
    os: str
    device_type: str
    normalized_path: str


def _write_visit_batch(batch: list["_VisitEvent"], db: Session) -> None:
    """Write a batch of queued visit events in one transaction.

    Called by run_visit_consumer (via asyncio.to_thread, with a fresh
    VisitsSessionLocal()) and directly by tests (with the shared
    db_session fixture) — kept as a plain, session-injected function
    rather than owning its own session, the same pattern every other
    DB-touching function in this codebase follows.
    """
    try:
        for event in batch:
            stmt = sqlite_insert(SiteVisit).values(
                date=event.date,
                visitor_hash=event.visitor_hash,
                browser=event.browser,
                os=event.os,
                device_type=event.device_type,
            ).on_conflict_do_nothing(index_elements=["date", "visitor_hash"])
            db.execute(stmt)

            page_stmt = sqlite_insert(PageView).values(
                date=event.date, path=event.normalized_path, count=1,
            ).on_conflict_do_update(
                index_elements=["date", "path"],
                set_={"count": PageView.count + 1},
            )
            db.execute(page_stmt)
        db.commit()
    except (OperationalError, SATimeoutError):
        # Best-effort (see module docstring) — drop this batch rather
        # than retry indefinitely; the consumer loop moves on to the
        # next batch regardless.
        logger.warning("Visit batch write failed (%d events dropped)", len(batch))
        db.rollback()


def _write_visit_batch_with_own_session(batch: list["_VisitEvent"]) -> None:
    """Entry point for asyncio.to_thread — owns the session lifecycle
    since, unlike _write_visit_batch, there's no request-scoped session
    to inject here."""
    db = VisitsSessionLocal()
    try:
        _write_visit_batch(batch, db)
    finally:
        db.close()


async def run_visit_consumer() -> None:
    """Background consumer, started once from app.main's lifespan and
    cancelled on shutdown. Drains the queue and writes in batches (up to
    _VISIT_BATCH_MAX per commit) entirely off the event loop, via
    asyncio.to_thread — the whole point of this consumer disappears if
    its own write blocks the loop the same way the old inline write did.
    """
    while True:
        event = await _visit_queue.get()
        batch = [event]
        while len(batch) < _VISIT_BATCH_MAX:
            try:
                batch.append(_visit_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        await asyncio.to_thread(_write_visit_batch_with_own_session, batch)


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
async def track_visit(request: Request, path: str = Query("/")) -> None:
    """Enqueues a visit event and returns immediately — see the module
    docstring for why this never touches the database directly.

    Must stay `async def`, not `def`: asyncio.Queue is explicitly not
    thread-safe, and `_visit_queue.put_nowait` below has to run on the
    same event loop thread run_visit_consumer's `await _visit_queue.get()`
    is running on. A plain `def` here would get dispatched to Starlette's
    thread pool instead, calling put_nowait from the wrong thread. This
    is safe specifically because there's no actual blocking I/O left in
    this body at all (hashing/regex only) — the original event-loop-
    blocking problem was the inline DB call, which is gone, not the
    async keyword itself.
    """
    ip = _track_ip(request)
    user_agent = request.headers.get("User-Agent", "")
    date = datetime.now(UTC).date().isoformat()

    event = _VisitEvent(
        date=date,
        visitor_hash=_visitor_hash(ip, user_agent, date),
        browser=_parse_browser(user_agent),
        os=_parse_os(user_agent),
        device_type=_parse_device(user_agent),
        normalized_path=_normalize_path(path),
    )
    try:
        _visit_queue.put_nowait(event)
    except asyncio.QueueFull:
        logger.warning("Visit queue full (%d) — dropping visit event", _VISIT_QUEUE_MAXSIZE)
