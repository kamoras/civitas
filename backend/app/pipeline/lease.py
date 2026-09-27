"""Cross-process leases: one row in api_cache per lease, owned by one holder
at a time, in any process.

Acquiring is a plain INSERT, and api_cache's (tier, cache_key) primary key
makes a second acquirer's insert fail atomically — no check-then-insert
race, no schema of its own. The holder rewrites the row's cached_at every
BEAT_S seconds while it holds it, and a row that has gone its tier's stale
window without a beat belongs to a dead holder, whoever it was, and is taken
over. Heartbeat and release touch only the row carrying the holder's token,
so a holder that stalled past the window and lost the lease can never renew
or delete its successor's.

Every lease is one of TIERS below; nothing should hand-roll another.
"""

import json
import logging
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import timedelta

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.time_utils import utcnow

logger = logging.getLogger(__name__)

# The admin data reset's lease (api/admin.py). Every other writer holds a lock
# of its own — a pipeline's run lock, or one of the leases below — and checks
# this inside that lock's own insert, before committing (acquire's
# `yield_to`, run_tracker.acquire_pipeline_lock); the reset checks theirs
# only after committing this one. Each writes its claim before reading the
# other's, and SQLite serializes writers, so at least one of any two sees the
# other. A job that yields just rolls back: backing out writes nothing.
DATA_RESET = "data-reset-lock"
ACTION_REFRESH = "action-refresh-lock"
# A Senate run holds this for its duration, so a process can tell a Senate
# run that is live elsewhere from one a dead process left behind
# (main._invalidate_orphaned_pipelines).
SENATE_RUN = "senate-run-lock"
STARTUP_RESCORE = "startup-rescore-lock"
JUSTICE_PIPELINE = "justice-pipeline-lock"
PRESIDENT_PIPELINE = "president-pipeline-lock"
# The explore ingest (trigger and startup bootstrap) and the re-embed: all
# rewrite the explore tables and their indexes, so one at a time.
EXPLORE = "explore-lock"
BILL_REFRESH = "bill-refresh-lock"
BALLOT_SYNC = "ballot-sync-lock"
COVERAGE_REFRESH = "coverage-refresh-lock"
# Every lease, which the data reset's wipe leaves in api_cache — its own, and
# any other that may be live — with what each one's holder is.
TIERS = {
    DATA_RESET: "Another data reset",
    ACTION_REFRESH: "Action Center refresh",
    SENATE_RUN: "Senate run",
    STARTUP_RESCORE: "Startup rescore",
    JUSTICE_PIPELINE: "Justice pipeline",
    PRESIDENT_PIPELINE: "President pipeline",
    EXPLORE: "Explore ingest or re-embed",
    BILL_REFRESH: "Bill status refresh",
    BALLOT_SYNC: "Ballot sync",
    COVERAGE_REFRESH: "Election coverage refresh",
}

# Ten missed beats ride out a SQLite writer holding the database for
# minutes, while a killed holder costs at most ten minutes.
BEAT_S = 60
STALE_S = 10 * 60
# The reset's wipe is one transaction: its own heartbeat can't write until
# it commits, so its lease must outlast the wipe unbeaten — a few table
# deletes, well inside this. A reset whose process died holds writers off
# this long, and each it holds off says so (run_tracker.acquire_pipeline_lock).
_STALE_S_BY_TIER = {DATA_RESET: 30 * 60}


def stale_after(tier: str) -> timedelta:
    return timedelta(seconds=_STALE_S_BY_TIER.get(tier, STALE_S))


def acquire(db: Session, tier: str, *, yield_to: str | None = None, take_over: bool = False) -> str | None:
    """Take the lease; the holder's token, or None when it is held — or, with
    `yield_to`, when that lease is held once this one's row is in (checked
    before committing, so yielding just rolls back; see DATA_RESET), or when
    the database stays locked past the busy timeout (a writer holding it:
    busy, not a failure). `take_over` replaces a live holder's row: for a
    caller that already holds the lock the lease stands for, whose previous
    holder must therefore be dead."""
    from app.models import ApiCache

    now = utcnow()
    token = uuid.uuid4().hex
    try:
        stale = db.query(ApiCache).filter(ApiCache.tier == tier, ApiCache.cache_key == "lock")
        if not take_over:
            stale = stale.filter(ApiCache.cached_at < now - stale_after(tier))
        stale.delete()
        db.add(ApiCache(tier=tier, cache_key="lock", data_json=json.dumps({"holder": token}), cached_at=now))
        db.flush()
        if yield_to is not None and held(db, yield_to):
            db.rollback()
            return None
        db.commit()
        return token
    except IntegrityError:
        db.rollback()
        return None
    except OperationalError as error:
        db.rollback()
        if not is_locked(error):
            raise
        logger.warning("The %s lease wasn't taken: the database is locked by another writer", tier)
        return None


def is_locked(error: OperationalError) -> bool:
    """SQLite's busy timeout ran out: another writer held the database.
    Any other OperationalError (a missing table, a corrupt file) is not
    'busy' and must not be treated as one."""
    return "locked" in str(error.orig).lower()


def held(db: Session, tier: str) -> bool:
    """Whether a live holder has the lease — one that has beaten within its
    tier's stale window."""
    from app.models import ApiCache

    return db.query(ApiCache).filter(
        ApiCache.tier == tier, ApiCache.cache_key == "lock",
        ApiCache.cached_at >= utcnow() - stale_after(tier),
    ).first() is not None


def _own_row(db: Session, tier: str, token: str):
    from app.models import ApiCache

    return db.query(ApiCache).filter(
        ApiCache.tier == tier, ApiCache.cache_key == "lock",
        ApiCache.data_json == json.dumps({"holder": token}),
    )


def beat(db: Session, tier: str, token: str) -> bool:
    """Renew the lease. False once the row is no longer this holder's."""
    renewed = _own_row(db, tier, token).update({"cached_at": utcnow()})
    db.commit()
    return renewed == 1


def release(db: Session, tier: str, token: str) -> None:
    try:
        _own_row(db, tier, token).delete()
        db.commit()
    except Exception:
        logger.exception("Failed to release the %s lease (it will expire as stale)", tier)
        db.rollback()


def _keep(bind, tier: str, token: str, stop: threading.Event, beat_s: float, until: float | None) -> None:
    """Heartbeat thread: beats on its own session (a Session is not
    thread-safe) until stopped, or until `until` (time.monotonic()) — past
    which a holder still running is presumed hung, and its lease lapses so
    the job's own hung-run handling can take over. A beat that meets a
    locked database is only logged — the lease has ten beats of slack."""
    import time

    while not stop.wait(beat_s):
        if until is not None and time.monotonic() >= until:
            logger.warning("The %s lease's holder has run past its limit — letting the lease lapse", tier)
            return
        db = Session(bind=bind)
        try:
            if not beat(db, tier, token):
                logger.warning("The %s lease was taken over — its holder is no longer exclusive", tier)
                return
        except OperationalError as error:
            db.rollback()
            if is_locked(error):
                logger.warning("The %s lease's heartbeat waited out a locked database; the next beat retries", tier)
            else:
                logger.exception("The %s lease's heartbeat failed", tier)
        except Exception:
            logger.exception("The %s lease's heartbeat failed", tier)
            db.rollback()
        finally:
            db.close()


class _Held:
    """A taken lease and its heartbeat, from _take until _let_go."""

    def __init__(self, db: Session, tier: str, token: str, max_hold: timedelta | None) -> None:
        import time

        self.db, self.tier, self.token = db, tier, token
        self.stop = threading.Event()
        until = time.monotonic() + max_hold.total_seconds() if max_hold is not None else None
        self.heartbeat = threading.Thread(
            target=_keep, args=(db.get_bind(), tier, token, self.stop, BEAT_S, until),
            name=f"{tier}-beat", daemon=True,
        )
        self.heartbeat.start()


def _take(db: Session, tier: str, yield_to: str | None, take_over: bool, max_hold: timedelta | None) -> _Held | None:
    token = acquire(db, tier, yield_to=yield_to, take_over=take_over)
    return _Held(db, tier, token, max_hold) if token is not None else None


def _let_go(held_lease: _Held) -> None:
    """Stop the heartbeat and join it before the release, so no beat in
    flight meets a row that is already gone."""
    held_lease.stop.set()
    held_lease.heartbeat.join()
    release(held_lease.db, held_lease.tier, held_lease.token)


@contextmanager
def holding(
    db: Session, tier: str, *, yield_to: str | None = None, take_over: bool = False,
    max_hold: timedelta | None = None,
) -> Iterator[str | None]:
    """Hold the lease for the enclosed work, beating it throughout (up to
    `max_hold`, see _keep); yields the token, or None (and holds nothing)
    when acquire refused."""
    held_lease = _take(db, tier, yield_to, take_over, max_hold)
    if held_lease is None:
        yield None
        return
    try:
        yield held_lease.token
    finally:
        _let_go(held_lease)


def _refusal(db: Session, tier: str) -> str:
    if held(db, DATA_RESET):
        return "a data reset is running"
    if held(db, tier):
        return "it is running elsewhere"
    return "the database was busy"


@contextmanager
def job(tier: str, *, max_hold: timedelta | None = None) -> Iterator[bool]:
    """A background job's lease, on its own session, yielding to the data
    reset: yields True while held, False — hold nothing, skip the work —
    when a reset is running, another process runs the same job, or the
    database stayed busy. `max_hold`: see _keep."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        with holding(db, tier, yield_to=DATA_RESET, max_hold=max_hold) as token:
            if token is None:
                logger.info("%s skipped: %s", TIERS[tier], _refusal(db, tier))
            yield token is not None
    finally:
        db.close()


@asynccontextmanager
async def job_async(tier: str, *, max_hold: timedelta | None = None) -> AsyncIterator[bool]:
    """job() for async code: the same lease, its database work in a thread,
    off the event loop, which serves every request. A cancellation while the
    lease is being taken waits for the taking to finish, and lets go of what
    it took, before the session closes."""
    import asyncio

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        taking = asyncio.ensure_future(asyncio.to_thread(_take, db, tier, DATA_RESET, False, max_hold))
        try:
            held_lease = await asyncio.shield(taking)
        except asyncio.CancelledError:
            held_lease = await taking
            if held_lease is not None:
                await asyncio.to_thread(_let_go, held_lease)
            raise
        if held_lease is None:
            logger.info("%s skipped: %s", TIERS[tier], await asyncio.to_thread(_refusal, db, tier))
            yield False
            return
        try:
            yield True
        finally:
            await asyncio.to_thread(_let_go, held_lease)
    finally:
        db.close()
