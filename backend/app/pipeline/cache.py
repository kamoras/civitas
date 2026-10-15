"""
Pipeline caching layer — two independent stores with different invalidation strategies.

API Cache (ApiCache table)
--------------------------
TTL-based. Caches raw HTTP responses from external APIs (FEC, Congress.gov, etc.)
so repeated pipeline runs don't re-fetch unchanged data. Controlled by
PIPELINE_CACHE_TTL_HOURS (default 72h). Safe to clear at any time; the next
pipeline run will re-fetch. No manual intervention needed on code changes.

LLM / Analysis Cache (AnalysisCache table)
-------------------------------------------
Version-based, no TTL. Caches parsed LLM output keyed by (prompt_version, input_hash).
Old entries for a given prompt_version remain in the table but are never read once
the version changes — they are dead weight and can be pruned with:

    DELETE FROM analysis_cache WHERE prompt_version != '<current_version>';

When to bump prompt_version in prompts.py:
  - Any change to the system prompt or user prompt template text
  - Any change to the output schema (new/removed fields)
  - Any change to how input_data is constructed (affects what gets hashed)
  - Model change (model name is included in the hash, so this auto-invalidates)

When NOT to bump:
  - Whitespace-only changes inside prompt strings
  - Changes to surrounding Python code that don't affect the prompt text

Bumping the version (e.g. "explore-doc-summary-v3" → "v4") causes all cached
results for that prompt to be ignored on the next pipeline run. The LLM is called
fresh for every document and results are stored under the new version key.
Old version rows remain in the DB until pruned — they do not affect correctness.
"""
import hashlib
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import execute_retrying_lock
from app.models import AnalysisCache, ApiCache
from app.time_utils import utcnow

logger = logging.getLogger(__name__)


# How much earlier than its TTL a `spread` entry may expire, as a share of
# the TTL, decided per key. The nightly fetches thousands of entries of one
# kind in one run; with one TTL for all of them they all expired on the same
# later night, and that night refetched every one. Through the rate limits
# (Congress.gov 1.2/s, the FEC 0.25/s) that made one night in three or four
# take 4-6 hours longer than the rest: 23,533 Congress.gov and 3,124 FEC
# entries were refetched on 2026-10-14, a few hundred on the nights before.
# Expiring each key somewhere in [TTL x (1 - spread), TTL] gives keys
# different periods, so entries written together fall out of step after a
# cycle and each night refetches about its share. Earlier, never later: no
# TTL a caller chose is stretched.
EXPIRY_SPREAD = 1 / 3


def spread_ttl_hours(key: str, ttl_hours: float) -> float:
    """`key`'s own expiry within [ttl x (1 - EXPIRY_SPREAD), ttl]: the same
    for a key every time, so a key's refresh period is fixed."""
    unit = int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], "big") / 2**32
    return ttl_hours * (1 - EXPIRY_SPREAD * unit)


def api_cache_get(
    db: Session, tier: str, key: str, max_age_hours: int | None = None,
    *, spread: bool = False, written_since: datetime | None = None,
) -> dict | None:
    """Get cached API response, return None if expired or missing.

    `spread`: expire this key a little early, by its own amount
    (spread_ttl_hours) — for the high-volume per-entity fetches the nightly
    run makes. An empty payload keeps its exact TTL, so the short life
    api_cache_set gives it stays what it is. `written_since`: an entry
    written before then counts as expired."""
    entry = (
        db.query(ApiCache)
        .filter(ApiCache.tier == tier, ApiCache.cache_key == key)
        .first()
    )
    if not entry:
        return None
    if written_since is not None and entry.cached_at < written_since:
        return None
    age = utcnow() - entry.cached_at
    ttl = max_age_hours if max_age_hours is not None else settings.PIPELINE_CACHE_TTL_HOURS
    if spread and entry.data_json not in _EMPTY_JSON:
        ttl = spread_ttl_hours(key, ttl)
    if age > timedelta(hours=ttl):
        return None
    return json.loads(entry.data_json)


def api_cache_mark_stale(db: Session, tier: str, keys: list[str], age: timedelta) -> int:
    """Make the entries for `keys` read as `age` old, keeping their data: they
    fail every freshness check shorter than that, and are still there for a
    caller that falls back to a stale copy when the source can't be read.
    Returns how many entries were marked."""
    marked = 0
    for i in range(0, len(keys), 500):  # stay under SQLite's bind-parameter limit
        marked += (
            db.query(ApiCache)
            .filter(ApiCache.tier == tier, ApiCache.cache_key.in_(keys[i:i + 500]))
            .update({ApiCache.cached_at: utcnow() - age}, synchronize_session=False)
        )
    db.commit()
    return marked


def api_cache_stamp(
    db: Session, tier: str, key: str, max_age_hours: int | None = None,
) -> datetime | None:
    """When a live (unexpired) cache entry was written, without reading its
    payload; None if it is missing or expired. Lets a caller that derives
    something expensive from an entry keep the result for exactly as long as
    the entry itself is unchanged."""
    cached_at = (
        db.query(ApiCache.cached_at)
        .filter(ApiCache.tier == tier, ApiCache.cache_key == key)
        .scalar()
    )
    if cached_at is None:
        return None
    ttl = max_age_hours if max_age_hours is not None else settings.PIPELINE_CACHE_TTL_HOURS
    return cached_at if utcnow() - cached_at <= timedelta(hours=ttl) else None


# Empty responses ([] / {} / None) are cached for at most this long. A
# transient API error or index lag that returns an empty list must not
# poison the cache for the full TTL — senators went without sponsored
# bills for days because an empty response persisted for 72h.
EMPTY_RESPONSE_TTL_HOURS = 6


# How an empty payload is stored (json.dumps of the falsy values a caller
# passes): what an empty write may replace.
_EMPTY_JSON = ("null", "[]", "{}", '""', "0", "false", "0.0")


def api_cache_set(
    db: Session, tier: str, key: str, data, *, normal_ttl_hours: int | None = None, commit: bool = True,
    retry_locked: bool = True,
) -> None:
    """Store API response in cache (upsert).

    Empty payloads never overwrite existing non-empty data, and are
    stored with a shortened effective TTL (implemented by backdating
    cached_at, since expiry is computed from it at read time).

    `normal_ttl_hours` must match whatever max_age_hours the caller's own
    api_cache_get uses for a real (non-empty) hit — the backdate offset is
    computed relative to it, defaulting to the global PIPELINE_CACHE_TTL_HOURS
    when omitted. A caller reading with a DIFFERENT custom max_age_hours
    without also passing the matching value here silently defeats the
    short-TTL safety net below: confirmed live 2026-07-25, house_ptr.py/
    senate_ptr.py read with a 30-day max_age_hours (deliberately long-lived
    for a successfully-parsed filing — PDFs don't change) while this
    function's backdate math assumed the 72h default, so a single transient
    parse failure (a PDF that failed to parse once, e.g. under CPU
    contention) stuck that filing's cache empty for a month instead of
    retrying within EMPTY_RESPONSE_TTL_HOURS.

    A "database is locked" is retried (database.execute_retrying_lock): a
    pipeline step caching what it fetched must not die on one refused write
    (the House sponsorship step, 2026-10-15). `retry_locked=False` for a
    reader's best-effort write, which shouldn't keep its request waiting
    through the retries.
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    is_empty = not data
    cached_at = utcnow()
    if is_empty:
        normal_ttl = normal_ttl_hours if normal_ttl_hours is not None else settings.PIPELINE_CACHE_TTL_HOURS
        shorten = max(normal_ttl - EMPTY_RESPONSE_TTL_HOURS, 0)
        cached_at -= timedelta(hours=shorten)

    # One statement, so the rule is decided by the database at write time:
    # another process (the API workers, the pipeline) may write this key
    # between any read here and the write. An empty payload replaces only
    # an empty one — never a non-empty payload, however recently written.
    upsert = sqlite_insert(ApiCache).values(
        tier=tier, cache_key=key, data_json=json.dumps(data, default=str), cached_at=cached_at,
    )
    statement = upsert.on_conflict_do_update(
        index_elements=["tier", "cache_key"],
        set_={"data_json": upsert.excluded.data_json, "cached_at": upsert.excluded.cached_at},
        where=ApiCache.data_json.in_(_EMPTY_JSON) if is_empty else None,
    )
    if retry_locked:
        execute_retrying_lock(db, statement)
    else:
        db.execute(statement)
    if commit:
        db.commit()


async def api_cache_set_many_async(db: Session, tier: str, items: dict, **kwargs) -> None:
    """Several api_cache_set writes in one transaction on one thread hop,
    best-effort like api_cache_set_async: a request that fetched several
    parts commits once rather than once per part."""
    from app.database import off_loop

    def write(session):
        for key, data in items.items():
            api_cache_set(session, tier, key, data, commit=False, retry_locked=False, **kwargs)
        session.commit()

    if not items:
        return
    try:
        await off_loop(db, write)
    except Exception:
        logger.warning("Cache writes to %s failed — not cached", tier, exc_info=True)


async def api_cache_set_async(db: Session, tier: str, key: str, data, **kwargs) -> None:
    """api_cache_set for a request path: off the event loop on a session of
    its own (database.off_loop), and best-effort — a cache write only saves
    later work, so one that fails (the pipeline holding the write lock past
    the busy timeout) is logged, never turned into a failed request."""
    from app.database import off_loop

    try:
        await off_loop(db, lambda session: api_cache_set(session, tier, key, data, retry_locked=False, **kwargs))
    except Exception:
        logger.warning("Cache write %s/%s failed — not cached", tier, key, exc_info=True)


def analysis_cache_get(
    db: Session, version: str, input_hash: str, *, raise_db_errors: bool = False,
) -> dict | None:
    """Get cached analysis result (no TTL - invalidated by version change).

    `raise_db_errors`: a database that can't be read (locked past its
    timeout) raises instead of reading as a miss — for a caller that would
    otherwise redo work that may well be stored. Corrupt JSON is a miss
    either way: redoing it is what replaces it."""
    try:
        entry = (
            db.query(AnalysisCache)
            .filter(
                AnalysisCache.prompt_version == version,
                AnalysisCache.input_hash == input_hash,
            )
            .first()
        )
        if not entry:
            return None
        return json.loads(entry.result_json)
    except SQLAlchemyError:
        if raise_db_errors:
            raise
        logger.debug("analysis_cache_get failed for %s/%s", version, input_hash, exc_info=True)
        return None
    except Exception:
        # Treat any failure (corrupt cached JSON, DB error) as a miss so the
        # caller recomputes rather than crashing — but log it, since a silent
        # permanent miss would otherwise rerun expensive analysis forever.
        logger.debug("analysis_cache_get failed for %s/%s", version, input_hash, exc_info=True)
        return None


def analysis_cache_set(
    db: Session, version: str, input_hash: str, data: dict
) -> None:
    """Store analysis result in cache (upsert)."""
    entry = (
        db.query(AnalysisCache)
        .filter(
            AnalysisCache.prompt_version == version,
            AnalysisCache.input_hash == input_hash,
        )
        .first()
    )
    result_json = json.dumps(data, default=str)
    if entry:
        entry.result_json = result_json
        entry.created_at = utcnow()
    else:
        entry = AnalysisCache(
            prompt_version=version,
            input_hash=input_hash,
            result_json=result_json,
            created_at=utcnow(),
        )
        db.add(entry)
    db.commit()
