"""Privacy-respecting unique-visitor tracking.

No raw IP or User-Agent is ever stored. `POST /api/track-visit` is fired by
the frontend's middleware on a page load, and by the browser (through nginx,
frontend NavigationBeacon) on a navigation inside the app — never on a
prefetch (frontend lib/pageLoad.ts) — and records only an HMAC of
the IP under a random salt that exists for the current UTC day and is then
deleted — see SiteVisit in models.py for why that makes past hashes
unrecoverable and why this table can't grow per-request.
"""

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, UTC

from fastapi import APIRouter, Query, Request
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError, TimeoutError as SATimeoutError
from sqlalchemy.orm import Session

from app.database import VisitsSessionLocal
from app.issue_ids import from_public_id
from app.models import IssueView, PageLoadTiming, PageView, SiteVisit, VisitSalt

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
_visit_queue: "asyncio.Queue[_VisitEvent | _TimingEvent]" = asyncio.Queue(maxsize=_VISIT_QUEUE_MAXSIZE)

# Upper bounds (ms) of the page-load histogram buckets PageLoadTiming counts
# into. Roughly geometric, so the relative precision of an interpolated
# percentile is about the same at 150ms as at 8s. A duration past the last
# bound is dropped rather than clamped: a minute-long "load" is a tab that was
# backgrounded or a laptop that slept mid-load, not a page that took a minute,
# and clamping it into the top bucket would drag p95 toward a number no reader
# actually waited for.
LOAD_TIMING_BUCKETS_MS: tuple[int, ...] = (
    50, 100, 150, 200, 300, 400, 500, 750, 1000, 1500, 2000,
    2500, 3000, 4000, 5000, 7500, 10000, 15000, 20000, 30000,
)
LOAD_TIMING_METRICS: tuple[str, ...] = ("ttfb", "fcp", "load")


@dataclass(frozen=True)
class _VisitEvent:
    date: str
    visitor_hash: str
    browser: str
    os: str
    device_type: str
    normalized_path: str
    # Set only when the raw path was /issue/{a well-formed public_id} — see
    # _extract_issue_public_id. None for every other page, including a
    # malformed /issue/ path, which still counts toward normalized_path.
    issue_public_id: str | None = None


@dataclass(frozen=True)
class _TimingEvent:
    """One page load's Navigation Timing, already bucketed — see PageLoadTiming."""
    date: str
    normalized_path: str
    # metric -> bucket upper bound (ms); only metrics the browser reported.
    buckets: tuple[tuple[str, int], ...]


def _bucket_for(ms: float) -> int | None:
    """The LOAD_TIMING_BUCKETS_MS bound `ms` falls under, or None when it is
    out of range (negative, NaN, or past the last bucket — see the ladder)."""
    if not ms >= 0:  # also rejects NaN
        return None
    for bound in LOAD_TIMING_BUCKETS_MS:
        if ms <= bound:
            return bound
    return None


def _write_visit_batch(batch: list["_VisitEvent | _TimingEvent"], db: Session) -> None:
    """Write a batch of queued visit events in one transaction.

    Called by run_visit_consumer (via asyncio.to_thread, with a fresh
    VisitsSessionLocal()) and directly by tests (with the shared
    db_session fixture) — kept as a plain, session-injected function
    rather than owning its own session, the same pattern every other
    DB-touching function in this codebase follows.
    """
    try:
        for event in batch:
            if isinstance(event, _TimingEvent):
                for metric, bucket in event.buckets:
                    db.execute(
                        sqlite_insert(PageLoadTiming).values(
                            date=event.date, path=event.normalized_path,
                            metric=metric, bucket_ms=bucket, count=1,
                        ).on_conflict_do_update(
                            index_elements=["date", "path", "metric", "bucket_ms"],
                            set_={"count": PageLoadTiming.count + 1},
                        )
                    )
                continue

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

            if event.issue_public_id:
                issue_stmt = sqlite_insert(IssueView).values(
                    date=event.date, issue_public_id=event.issue_public_id, count=1,
                ).on_conflict_do_update(
                    index_elements=["date", "issue_public_id"],
                    set_={"count": IssueView.count + 1},
                )
                db.execute(issue_stmt)
        db.commit()
    except (OperationalError, SATimeoutError):
        # Best-effort (see module docstring) — drop this batch rather
        # than retry indefinitely; the consumer loop moves on to the
        # next batch regardless.
        logger.warning("Visit batch write failed (%d events dropped)", len(batch))
        db.rollback()


def _write_visit_batch_with_own_session(batch: list["_VisitEvent | _TimingEvent"]) -> None:
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
    last_check = -_STALE_SALT_CHECK_S
    while True:
        now = time.monotonic()
        if now - last_check >= _STALE_SALT_CHECK_S:
            last_check = now
            # Off the loop: it deletes an ended day's salt row too.
            await asyncio.to_thread(_forget_stale_salts)
        try:
            event = await asyncio.wait_for(_visit_queue.get(), timeout=_STALE_SALT_CHECK_S)
        except TimeoutError:
            continue
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


# (date, salt) for the current UTC day, per process. The salt itself lives
# in the visits database (VisitSalt) so every backend process shares it.
_salt_cache: tuple[str, bytes] | None = None


def _load_or_create_salt(date: str) -> bytes | None:
    """The shared salt for `date`, creating it if this is the first visit
    of the day. Every other day's salt is deleted in the same transaction —
    once a day's salt is gone, that day's hashes can't be recomputed from
    an IP by anyone.

    None when another worker has already made a later day's salt: this one
    read the clock just before midnight, and making `date`'s again would
    bring back a salt that was deleted."""
    from sqlalchemy import exists, literal, select

    db = VisitsSessionLocal()
    try:
        db.execute(
            sqlite_insert(VisitSalt)
            .from_select(
                ["date", "salt"],
                select(literal(date), literal(secrets.token_hex(32))).where(
                    ~exists().where(VisitSalt.date > date),
                ),
            )
            .on_conflict_do_nothing(index_elements=["date"])
        )
        # Earlier days only: with several API workers, one that read the
        # clock just before midnight must not delete the new day's salt
        # another already made and cached — the two would then hash the
        # same visitor differently all day.
        db.query(VisitSalt).filter(VisitSalt.date < date).delete()
        db.commit()
        salt = db.query(VisitSalt.salt).filter(VisitSalt.date == date).scalar()
        return bytes.fromhex(salt) if salt is not None else None
    finally:
        db.close()


async def _daily_salt(date: str) -> bytes:
    global _salt_cache
    if _salt_cache is not None and _salt_cache[0] == date:
        return _salt_cache[1]
    try:
        salt = await asyncio.to_thread(_load_or_create_salt, date)
    except Exception:
        # Degrade to a fallback salt; nothing reversible is ever stored. The
        # shared salt is retried on the next call (this isn't cached as it).
        # One fallback for the whole container for the day where possible —
        # derived from a RAM salt every worker shares (throttle.derived_salt,
        # deleted when the day ends) — so a visitor during the outage counts
        # once, not once per worker (a fresh salt per call made every visit a
        # new unique). Visitors
        # who span the outage and the recovery still count twice: no salt
        # matching the shared one exists while it can't be read.
        logger.warning("Visit salt unavailable — using a fallback salt", exc_info=True)
        # Reads the RAM store on first use in a day: off the event loop.
        return await asyncio.to_thread(_fallback_salt_for, date)
    if salt is None:
        # The day ended a moment ago and its salt is gone: this visit, from
        # its last instant, is hashed with a salt nobody keeps — it may count
        # as one more unique for that day, never as a recoverable address.
        return secrets.token_bytes(32)
    _salt_cache = (date, salt)
    return salt


# The UTC day this process last made sure the database holds no earlier
# day's salt (_forget_stale_salts).
_salt_swept_day: str | None = None


# (date, salt, whether it is the container-shared one)
_fallback_salt: tuple[str, bytes, bool] | None = None


# How often an idle worker checks for a salt left over from a previous day.
_STALE_SALT_CHECK_S = 60.0


def _forget_stale_salts() -> None:
    """Drop any salt for a day that has ended — from this process's memory,
    and the shared row from the visits database — without waiting for a new
    day's first visit: until then, anyone holding it could recompute
    yesterday's visitor hashes (AGENTS.md §8)."""
    global _salt_cache, _fallback_salt
    today = datetime.now(UTC).date().isoformat()
    if _salt_cache is not None and _salt_cache[0] != today:
        _salt_cache = None
    if _fallback_salt is not None and _fallback_salt[0] != today:
        _fallback_salt = None
    # And from the database, once per new day: otherwise an ended day's row
    # stays until the next day's first visit, hours later on a quiet night.
    # By making today's salt — the one step that deletes earlier days
    # (_load_or_create_salt) — never by deleting alone: a worker that read
    # the clock just before midnight refuses to recreate an ended day's salt
    # only because a later day's row exists.
    global _salt_swept_day
    if _salt_swept_day != today:
        try:
            _load_or_create_salt(today)
            _salt_swept_day = today
        except Exception:
            logger.warning("Couldn't drop an ended day's visit salt — retried next tick", exc_info=True)
    # The RAM store's salts, including the one the fallback derives from,
    # are dropped by the store's own maintenance (throttle.run_maintenance).


def _fallback_salt_for(date: str) -> bytes:
    """The container's shared fallback (throttle.derived_salt: a RAM salt
    deleted when the day ends) — or, while that store is down too, this process's own, kept
    only until the shared one can be made: a private salt kept all day
    would count this worker's visitors apart from every other worker's."""
    from datetime import datetime, timezone

    global _fallback_salt
    if date != datetime.now(timezone.utc).date().isoformat():
        # A visit from a day that has ended: hashed with a salt nobody keeps,
        # as _daily_salt does once the day's salt is gone — never with a
        # salt that outlives its day.
        return secrets.token_bytes(32)
    if _fallback_salt is None or _fallback_salt[0] != date or not _fallback_salt[2]:
        from app.api import throttle

        shared = throttle.derived_salt(f"visits:{date}", date)
        if shared is not None:
            _fallback_salt = (date, shared, True)
        elif _fallback_salt is None or _fallback_salt[0] != date:
            _fallback_salt = (date, secrets.token_bytes(32), False)
    return _fallback_salt[1]


def _visitor_hash(ip: str, salt: bytes) -> str:
    # Keyed on IP only (2026-07): the User-Agent used to be in this key,
    # which meant one client rotating the User-Agent header produced
    # unlimited distinct hashes — a new SiteVisit row per request, i.e. a
    # disk-fill vector on an unauthenticated endpoint. Dropping it bounds
    # daily rows to distinct source IPs (the natural unique-visitor unit).
    # The day is carried by the salt, which rotates daily.
    #
    # The salt replaces a key derived from ADMIN_TOKEN (falling back to the
    # literal "civitas" when no token was set). That key never changed, and
    # the IPv4 space is small enough to enumerate, so anyone holding the key
    # could turn every stored hash back into an IP.
    return hmac.new(salt, ip.encode(), hashlib.sha256).hexdigest()[:32]


_KNOWN_STATIC_PATHS = {
    "/", "/about", "/accessibility", "/action", "/bills", "/changelog",
    "/compare", "/environmental", "/explore", "/feedback", "/leaderboard",
    "/politicians", "/elections", "/congress", "/congress/bills",
    # /scorecard has no page anymore (renamed) but old Bluesky posts still
    # link to it — kept here so that 404 traffic stays visibly labeled
    # "/scorecard" instead of draining into the unlabeled "/other" bucket.
    "/scorecard",
    # The About chapters (frontend/src/lib/aboutPages.ts). Listed rather
    # than prefix-collapsed: which chapter people read is the useful part.
    "/about/scores", "/about/presidents-and-justices", "/about/elections",
    "/about/news", "/about/data", "/about/limitations", "/about/references",
}
# Longest first: "/congress/bills/S.1" is a bill page, "/congress/2026-09-24"
# a day report.
_DYNAMIC_PREFIXES = ("/congress/bills/", "/politicians/", "/issue/", "/explore/", "/elections/", "/congress/")


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


def _extract_issue_public_id(raw: str) -> str | None:
    """The issue public_id out of a raw /issue/{id} path, or None.

    Deliberately not folded into _normalize_path: that function's whole
    job is collapsing per-id routes away (see its docstring) — this is
    the one place that per-id detail is kept, for IssueView, and only
    ever for a well-formed id (from_public_id's own validation), so a
    malformed or made-up path segment can't grow that table with junk.
    """
    path = (raw or "/").split("?")[0].rstrip("/") or "/"
    if not path.startswith("/issue/"):
        return None
    candidate = path[len("/issue/"):]
    return candidate if from_public_id(candidate) is not None else None


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
        visitor_hash=_visitor_hash(ip, await _daily_salt(date)),
        browser=_parse_browser(user_agent),
        os=_parse_os(user_agent),
        device_type=_parse_device(user_agent),
        normalized_path=_normalize_path(path),
        issue_public_id=_extract_issue_public_id(path),
    )
    try:
        _visit_queue.put_nowait(event)
    except asyncio.QueueFull:
        logger.warning("Visit queue full (%d) — dropping visit event", _VISIT_QUEUE_MAXSIZE)


@router.post("/track-timing", status_code=204)
async def track_timing(
    path: str = Query("/"),
    ttfb: float | None = Query(None),
    fcp: float | None = Query(None),
    load: float | None = Query(None),
) -> None:
    """Records one hard page load's Navigation Timing (milliseconds), sent by
    the browser's LoadTimingBeacon after the load event.

    Unlike track-visit this is called by the browser itself, through nginx's
    rate-limited /api/ location, so it reads nothing about the caller — no
    IP, no User-Agent. The route is normalized exactly as track-visit's is and
    each duration is reduced to its histogram bucket before it is queued (see
    PageLoadTiming), so an arbitrary client can at most add counts to a
    bounded set of (route, metric, bucket) cells — never grow the table with
    strings of its own. Same queue and consumer as track-visit, for the same
    reason: this must never write to the database on the request path.
    Must stay `async def` — see track_visit.
    """
    buckets = tuple(
        (metric, bucket)
        for metric, value in (("ttfb", ttfb), ("fcp", fcp), ("load", load))
        if value is not None and (bucket := _bucket_for(value)) is not None
    )
    if not buckets:
        return
    event = _TimingEvent(
        date=datetime.now(UTC).date().isoformat(),
        normalized_path=_normalize_path(path),
        buckets=buckets,
    )
    try:
        _visit_queue.put_nowait(event)
    except asyncio.QueueFull:
        logger.warning("Visit queue full (%d) — dropping timing event", _VISIT_QUEUE_MAXSIZE)
