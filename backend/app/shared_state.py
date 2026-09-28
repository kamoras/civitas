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
