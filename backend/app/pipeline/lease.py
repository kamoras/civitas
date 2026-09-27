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
from collections.abc import Iterator
from contextlib import contextmanager
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
# it commits, so its lease must outlast the wipe unbeaten.
_STALE_S_BY_TIER = {DATA_RESET: 60 * 60}


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
    except OperationalError:
        db.rollback()
        logger.warning("The %s lease wasn't taken: the database is locked by another writer", tier)
        return None


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


def _keep(bind, tier: str, token: str, stop: threading.Event, beat_s: float) -> None:
    """Heartbeat thread: beats on its own session (a Session is not
    thread-safe) until stopped. A failed beat is only logged — the lease
    has ten beats of slack."""
    while not stop.wait(beat_s):
        db = Session(bind=bind)
        try:
            if not beat(db, tier, token):
                logger.warning("The %s lease was taken over — its holder is no longer exclusive", tier)
                return
        except OperationalError:
            logger.warning("The %s lease's heartbeat waited out a locked database; the next beat retries", tier)
            db.rollback()
        except Exception:
            logger.exception("The %s lease's heartbeat failed", tier)
            db.rollback()
        finally:
            db.close()


@contextmanager
def holding(
    db: Session, tier: str, *, yield_to: str | None = None, take_over: bool = False,
) -> Iterator[str | None]:
    """Hold the lease for the enclosed work, beating it throughout; yields
    the token, or None (and holds nothing) when acquire refused. The
    heartbeat is stopped and joined before the release, so no beat in
    flight meets a row that is already gone."""
    token = acquire(db, tier, yield_to=yield_to, take_over=take_over)
    if token is None:
        yield None
        return
    stop = threading.Event()
    heartbeat = threading.Thread(
        target=_keep, args=(db.get_bind(), tier, token, stop, BEAT_S), name=f"{tier}-beat", daemon=True,
    )
    heartbeat.start()
    try:
        yield token
    finally:
        stop.set()
        heartbeat.join()
        release(db, tier, token)


@contextmanager
def job(tier: str) -> Iterator[bool]:
    """A background job's lease, on its own session, yielding to the data
    reset: yields True while held, False — hold nothing, skip the work —
    when a reset is running or another process runs the same job."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        with holding(db, tier, yield_to=DATA_RESET) as token:
            if token is None:
                logger.info("%s skipped: %s", TIERS[tier],
                            "a data reset is running" if held(db, DATA_RESET) else "it is running elsewhere")
            yield token is not None
    finally:
        db.close()
