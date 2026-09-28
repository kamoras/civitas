"""api_cache rows one process writes and another reads.

The backend runs as separate processes (settings.PROCESS_ROLE): the pipeline
process recalibrates, refreshes and schedules; the API processes serve reads
from what it wrote. A value one process caches in memory has to notice when
another replaced the row it came from. These are the one way to read and
write such a row, so every such check treats a missing row, an unreadable
database and a replaced row the same way. (File-backed caches have the same
rule through file_cache.files_stamp.)

Rows are read whatever their age: these are current values that stand until
replaced (a calibration, a heartbeat), not responses with a freshness TTL —
api_cache_get's 72-hour default would silently drop a calibration the
pipeline hasn't had occasion to redo.
"""

import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# What read_row returns when the database couldn't be asked. Never a
# change: a moment's lock mustn't make a caller drop the value it holds.
UNREADABLE = object()


def read_row(tier: str, key: str, db=None) -> "tuple[datetime, Any] | None | object":
    """(when it was written, its decoded value) for the row, None when there
    is none, UNREADABLE when the database couldn't be read. One query, on
    `db` or a session of its own."""
    from app.models import ApiCache

    def query(session):
        return (
            session.query(ApiCache.cached_at, ApiCache.data_json)
            .filter(ApiCache.tier == tier, ApiCache.cache_key == key)
            .first()
        )

    try:
        if db is None:
            from app.database import session_scope

            with session_scope() as own:
                row = query(own)
        else:
            row = query(db)
    except Exception:
        logger.debug("Couldn't read api_cache %s/%s", tier, key, exc_info=True)
        return UNREADABLE
    if row is None:
        return None
    try:
        return row.cached_at, json.loads(row.data_json)
    except (TypeError, ValueError):
        return row.cached_at, None


def write_row(db, tier: str, key: str, value: Any, *, at: datetime) -> None:
    """Upsert the row with `value` (JSON) written `at`. Not committed. A
    plain write — unlike api_cache_set, which keeps an older non-empty value
    over an empty one and backdates empties, rules for cached responses."""
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from app.models import ApiCache

    data = json.dumps(value)
    db.execute(
        sqlite_insert(ApiCache)
        .values(tier=tier, cache_key=key, data_json=data, cached_at=at)
        .on_conflict_do_update(index_elements=["tier", "cache_key"], set_={"data_json": data, "cached_at": at})
    )


class PolledRow:
    """A value another process writes to one row, as this process last read
    it — reloaded when the row's written-at stamp moves, checked at most
    every `every_s` seconds. The one rule for every such cache:

    - an unreadable database (a moment's lock) keeps the value in hand;
    - a row that changed but won't decode keeps it too, unstamped, so the
      next check tries again;
    - no row at all is None: the caller falls back (a bundled file, a
      bootstrap value).

    `decode` turns the stored value into the cached one, or None when it
    can't. `reader(db)` reads the row; it defaults to read_row, and exists so
    a module can route the read through a function of its own. The database
    read happens outside the lock: a slow one must not hold up every thread
    serving from the value.
    """

    def __init__(self, tier: str, key: str, *, every_s: float, decode, reader=None):
        import threading

        self.tier, self.key, self.every_s, self.decode = tier, key, every_s, decode
        self._reader = reader or (lambda db: read_row(tier, key, db))
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        """Forget the value: the next get() reads the row."""
        with self._lock:
            self._value, self._stamp, self._checked_at = None, None, None

    def expire(self) -> None:
        """Make the next get() check the row (tests; a forced reload)."""
        with self._lock:
            self._checked_at = None

    def current(self):
        """The value in hand, without checking the row."""
        with self._lock:
            return self._value

    def get(self, db=None, *, force: bool = False):
        import time

        now = time.monotonic()
        with self._lock:
            due = force or self._checked_at is None or now - self._checked_at >= self.every_s
            if due:
                self._checked_at = now  # one thread checks; the rest keep serving
            value, stamp = self._value, self._stamp
        if not due:
            return value

        row = self._reader(db)
        if row is UNREADABLE:
            return value
        if row is None:
            value, stamp = None, None
        elif row[0] != stamp or value is None:
            decoded = self.decode(row[1])
            if decoded is None:
                logger.warning("Stored %s/%s couldn't be decoded — keeping the value in hand", self.tier, self.key)
            else:
                value, stamp = decoded, row[0]
        with self._lock:
            self._value, self._stamp = value, stamp
        return value


def decode_json_dict(value) -> dict | None:
    """A stored value as a dict. Values written through api_cache_set as a
    JSON string arrive encoded twice: read_row decodes one layer."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None
