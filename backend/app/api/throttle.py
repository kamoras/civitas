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
Keys are never IP addresses (rate_limit.client_key), and every row carries
the time it stops mattering; expired rows are deleted at most once a
minute per process, across every bucket.

Cost: a `hit` is one small WAL write transaction — measured at ~1.4 ms
(~700/s, serialized across processes) on a development container, against
~1 µs for the in-process deque it replaced. Only mutations and
/api/public/ pay it; every other read goes through nginx's cache and per-IP
limit without touching this store.

Every function fails open. A limiter that can't reach its store lets the
request through and logs it, rather than turning a locked database into an
outage of every endpoint behind it.
"""

import logging
import math
import threading
import time
from dataclasses import dataclass

from sqlalchemy import create_engine, delete, event, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.models import ThrottleClaim, ThrottleWindow

logger = logging.getLogger(__name__)

# These run on the request path. A client held for the engine-wide 30 s busy
# timeout, because the visit consumer happens to be committing a batch, is
# worse than a limit that lets one request through — so the throttle has an
# engine of its own with a short one, rather than changing the timeout on a
# connection it shares with everything else in the visits database.
_BUSY_TIMEOUT_S = 2.0

# Built on first use; the tests' `throttle_store` fixture sets it instead.
_session_factory: "sessionmaker[Session] | None" = None
_factory_lock = threading.Lock()

_PURGE_INTERVAL_S = 60.0
_purge_lock = threading.Lock()
_last_purge = 0.0


def make_session_factory(url: str, busy_timeout_s: float = _BUSY_TIMEOUT_S) -> "sessionmaker[Session]":
    from app.database import _set_sqlite_pragmas

    engine = create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": busy_timeout_s},
        pool_pre_ping=True,
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    return sessionmaker(bind=engine, autoflush=False)


def _sessions() -> "sessionmaker[Session]":
    global _session_factory
    with _factory_lock:
        if _session_factory is None:
            from app.database import VISITS_DATABASE_URL

            _session_factory = make_session_factory(VISITS_DATABASE_URL)
        return _session_factory


@dataclass(frozen=True)
class Decision:
    allowed: bool
    remaining: int
    reset_at: int  # epoch seconds at which the current window ends


def _purge_expired(db: Session, now: float) -> None:
    """Delete every expired row, in every bucket — at most once a minute per
    process, so a quiet bucket's rows go as surely as a busy one's."""
    global _last_purge
    with _purge_lock:
        if now - _last_purge < _PURGE_INTERVAL_S:
            return
        _last_purge = now
    db.execute(delete(ThrottleWindow).where(ThrottleWindow.expires_at < now))
    db.execute(delete(ThrottleClaim).where(ThrottleClaim.expires_at < now))


def hit(bucket: str, key: str, *, limit: int, period: float, cost: int = 1) -> Decision:
    """Count `cost` units (one request, by default) against `limit` per
    `period` seconds for `key`.

    A refused request is not counted, so a client that keeps retrying
    through a 429 is let back in as its earlier requests age out, as with
    the per-process limiters this replaced.
    """
    now = time.time()
    window = int(now // period)
    elapsed = (now - window * period) / period
    reset_at = int((window + 1) * period)
    db = None
    try:
        db = _sessions()()
        current = db.execute(
            sqlite_insert(ThrottleWindow)
            .values(bucket=bucket, key=key, window=window, count=cost, expires_at=(window + 2) * period)
            .on_conflict_do_update(
                index_elements=["bucket", "key", "window"],
                set_={"count": ThrottleWindow.count + cost},
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
                .values(count=ThrottleWindow.count - cost)
            )
        _purge_expired(db, now)
        db.commit()
        remaining = max(0, math.floor(limit - estimate)) if allowed else 0
        return Decision(allowed, remaining, reset_at)
    except SQLAlchemyError:
        _rollback(db)
        logger.warning("Throttle %r unavailable — allowing the request", bucket, exc_info=True)
        return Decision(True, limit, reset_at)
    finally:
        if db is not None:
            db.close()


def claim(bucket: str, key: str, *, period: float) -> bool:
    """Claim `key` unless it was claimed less than `period` seconds ago.
    True when this caller got it."""
    now = time.time()
    db = None
    try:
        db = _sessions()()
        won = db.execute(
            sqlite_insert(ThrottleClaim)
            .values(bucket=bucket, key=key, claimed_at=now, expires_at=now + period)
            .on_conflict_do_update(
                index_elements=["bucket", "key"],
                set_={"claimed_at": now, "expires_at": now + period},
                where=ThrottleClaim.claimed_at <= now - period,
            )
            .returning(ThrottleClaim.claimed_at)
        ).first() is not None
        _purge_expired(db, now)
        db.commit()
        return won
    except SQLAlchemyError:
        _rollback(db)
        logger.warning("Throttle %r unavailable — allowing the claim", bucket, exc_info=True)
        return True
    finally:
        if db is not None:
            db.close()


def clear(*buckets: str) -> None:
    """Forget everything counted in `buckets` (tests reset limits this way)."""
    db = None
    try:
        db = _sessions()()
        db.execute(delete(ThrottleWindow).where(ThrottleWindow.bucket.in_(buckets)))
        db.execute(delete(ThrottleClaim).where(ThrottleClaim.bucket.in_(buckets)))
        db.commit()
    except SQLAlchemyError:
        _rollback(db)
        logger.warning("Throttle clear failed", exc_info=True)
    finally:
        if db is not None:
            db.close()


def release(bucket: str, key: str) -> None:
    """Give back a claim whose work didn't happen (the issue voted on didn't
    exist), so it doesn't hold the next attempt off."""
    db = None
    try:
        db = _sessions()()
        db.execute(delete(ThrottleClaim).where(ThrottleClaim.bucket == bucket, ThrottleClaim.key == key))
        db.commit()
    except SQLAlchemyError:
        _rollback(db)
        logger.warning("Throttle %r release failed", bucket, exc_info=True)
    finally:
        if db is not None:
            db.close()


def _rollback(db: Session | None) -> None:
    if db is None:
        return
    try:
        db.rollback()
    except SQLAlchemyError:
        pass
