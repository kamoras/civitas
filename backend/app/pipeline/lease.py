"""Cross-process leases: one row in api_cache per lease, owned by one holder
at a time, in any process.

Acquiring is a plain INSERT, and api_cache's (tier, cache_key) primary key
makes a second acquirer's insert fail atomically — no check-then-insert
race, no schema of its own. The holder rewrites the row's cached_at every
`beat_s` while it holds it, and a row that has gone STALE_S without a beat
belongs to a dead holder, whoever it was, and is taken over. Heartbeat and
release touch only the row carrying the holder's token, so a holder that
stalled past the window and lost the lease can never renew or delete its
successor's.

Used by the Action Center refresh (action_center.hold_refresh_lock) and the
admin data reset (api/admin.py), and nothing else should hand-roll another.
"""

import json
import logging
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.time_utils import utcnow

logger = logging.getLogger(__name__)

# The admin data reset's lease (api/admin.py). Every job that takes a lock of
# its own — a pipeline's run lock, the refresh lease — checks it only after
# taking that lock, and the reset checks theirs only after taking this one:
# each writes its own claim before reading the other's, and SQLite
# serializes the writes, so at least one of any two sees the other.
DATA_RESET = "data-reset-lock"

# Ten missed beats ride out a SQLite writer holding the database for
# minutes, while a killed holder costs at most ten minutes.
BEAT_S = 60
STALE_S = 10 * 60


def acquire(db: Session, tier: str) -> str | None:
    """Take the lease; the holder's token, or None when it is held."""
    from app.models import ApiCache

    now = utcnow()
    db.query(ApiCache).filter(
        ApiCache.tier == tier, ApiCache.cache_key == "lock",
        ApiCache.cached_at < now - timedelta(seconds=STALE_S),
    ).delete()
    db.commit()

    token = uuid.uuid4().hex
    try:
        db.add(ApiCache(tier=tier, cache_key="lock", data_json=json.dumps({"holder": token}), cached_at=now))
        db.commit()
        return token
    except IntegrityError:
        db.rollback()
        return None


def held(db: Session, tier: str) -> bool:
    """Whether a live holder has the lease — one that has beaten within
    STALE_S."""
    from app.models import ApiCache

    return db.query(ApiCache).filter(
        ApiCache.tier == tier, ApiCache.cache_key == "lock",
        ApiCache.cached_at >= utcnow() - timedelta(seconds=STALE_S),
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
        except Exception:
            logger.exception("The %s lease's heartbeat failed", tier)
            db.rollback()
        finally:
            db.close()


@contextmanager
def holding(db: Session, tier: str, *, beat_s: float = BEAT_S) -> Iterator[str | None]:
    """Hold the lease for the enclosed work, beating it throughout; yields
    the token, or None (and holds nothing) when another holder has it. The
    heartbeat is stopped and joined before the release, so no beat in
    flight meets a row that is already gone."""
    token = acquire(db, tier)
    if token is None:
        yield None
        return
    stop = threading.Event()
    heartbeat = threading.Thread(
        target=_keep, args=(db.get_bind(), tier, token, stop, beat_s), name=f"{tier}-beat", daemon=True,
    )
    heartbeat.start()
    try:
        yield token
    finally:
        stop.set()
        heartbeat.join()
        release(db, tier, token)
