"""Rate limits and once-per-period rules that hold across every API worker.

These used to be dicts in each module (the write limiter, the public API's
read limiter, the pulse vote dedup, the Explore summary cooldown), which was
correct only while the backend ran as one process. With several uvicorn
workers each would keep its own copy: a client could vote twice on the same
issue by landing on the other worker, and every per-IP limit would stretch
to its value times the number of workers.

The shared state lives in the visits database — its own SQLite file, already
the home of the other per-request writes (database._derive_visits_database_url)
— as two small tables:

  ThrottleWindow  a request count per fixed window. `hit` estimates a
                  sliding window from the current and previous counts
                  (previous × the share of it still inside the window, plus
                  current), which holds a limit across a window boundary
                  where a plain fixed window would allow twice the limit.
  ThrottleClaim   when something was last claimed. `claim` succeeds only
                  when the previous claim is at least `period` old, in one
                  conditional upsert, so two workers can't both win it.

Each operation is one short transaction whose first statement is the write,
so SQLite's writer lock makes the read-modify-write atomic across processes.
Keys are never IP addresses: per-client callers pass the daily-salted
visitor hash (rate_limit.client_key), and rows are purged once expired.

Every function fails open. A limiter that can't reach its store lets the
request through and logs it, rather than turning a locked database into an
outage of every endpoint behind it.
"""

import logging
import math
import threading
import time
from dataclasses import dataclass

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import SQLITE_BUSY_TIMEOUT_S, VisitsSessionLocal
from app.models import ThrottleClaim, ThrottleWindow

logger = logging.getLogger(__name__)

# Swapped for an isolated store by the tests' `throttle_store` fixture.
_session_factory = VisitsSessionLocal

# These run on the request path. A client held for the default 30 s busy
# timeout, because the visit consumer happens to be committing a batch, is
# worse than a limit that lets one request through.
_BUSY_TIMEOUT_MS = 2000

# Expired rows are deleted every this-many operations per process.
_PURGE_EVERY = 500
_ops_lock = threading.Lock()
_ops = 0


@dataclass(frozen=True)
class Decision:
    allowed: bool
    remaining: int
    reset_at: int  # epoch seconds at which the current window ends


def _open() -> Session:
    db = _session_factory()
    db.execute(text(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}"))
    return db


def _close(db: Session) -> None:
    try:
        # The connection goes back to the pool: restore the engine's own
        # timeout for whoever takes it next.
        db.execute(text(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_S * 1000}"))
    except SQLAlchemyError:
        pass
    finally:
        db.close()


def _purge_due() -> bool:
    global _ops
    with _ops_lock:
        _ops += 1
        return _ops % _PURGE_EVERY == 0


def hit(bucket: str, key: str, *, limit: int, period: float) -> Decision:
    """Count one request against `limit` per `period` seconds for `key`.

    A refused request is not counted, so a client that keeps retrying
    through a 429 is let back in as its earlier requests age out, as with
    the per-process limiters this replaced.
    """
    now = time.time()
    window = int(now // period)
    elapsed = (now - window * period) / period
    reset_at = int((window + 1) * period)
    db = _open()
    try:
        current = db.execute(
            sqlite_insert(ThrottleWindow)
            .values(bucket=bucket, key=key, window=window, count=1)
            .on_conflict_do_update(
                index_elements=["bucket", "key", "window"],
                set_={"count": ThrottleWindow.count + 1},
            )
            .returning(ThrottleWindow.count)
        ).scalar_one()
        previous = db.execute(
            select(ThrottleWindow.count).where(
                ThrottleWindow.bucket == bucket,
                ThrottleWindow.key == key,
                ThrottleWindow.window == window - 1,
            )
        ).scalar() or 0
        estimate = previous * (1 - elapsed) + current
        allowed = estimate <= limit
        if not allowed:
            db.execute(
                update(ThrottleWindow)
                .where(
                    ThrottleWindow.bucket == bucket,
                    ThrottleWindow.key == key,
                    ThrottleWindow.window == window,
                )
                .values(count=ThrottleWindow.count - 1)
            )
        if _purge_due():
            db.execute(delete(ThrottleWindow).where(
                ThrottleWindow.bucket == bucket, ThrottleWindow.window < window - 1,
            ))
        db.commit()
        remaining = max(0, math.floor(limit - estimate)) if allowed else 0
        return Decision(allowed, remaining, reset_at)
    except SQLAlchemyError:
        db.rollback()
        logger.warning("Throttle %r unavailable — allowing the request", bucket, exc_info=True)
        return Decision(True, limit, reset_at)
    finally:
        _close(db)


def claim(bucket: str, key: str, *, period: float) -> bool:
    """Claim `key` unless it was claimed less than `period` seconds ago.
    True when this caller got it."""
    now = time.time()
    db = _open()
    try:
        won = db.execute(
            sqlite_insert(ThrottleClaim)
            .values(bucket=bucket, key=key, claimed_at=now)
            .on_conflict_do_update(
                index_elements=["bucket", "key"],
                set_={"claimed_at": now},
                where=ThrottleClaim.claimed_at <= now - period,
            )
            .returning(ThrottleClaim.claimed_at)
        ).first() is not None
        if _purge_due():
            db.execute(delete(ThrottleClaim).where(
                ThrottleClaim.bucket == bucket, ThrottleClaim.claimed_at < now - period,
            ))
        db.commit()
        return won
    except SQLAlchemyError:
        db.rollback()
        logger.warning("Throttle %r unavailable — allowing the claim", bucket, exc_info=True)
        return True
    finally:
        _close(db)


def release(bucket: str, key: str) -> None:
    """Give back a claim whose work didn't happen (the issue voted on didn't
    exist), so it doesn't hold the next attempt off."""
    db = _open()
    try:
        db.execute(delete(ThrottleClaim).where(ThrottleClaim.bucket == bucket, ThrottleClaim.key == key))
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.warning("Throttle %r release failed", bucket, exc_info=True)
    finally:
        _close(db)
