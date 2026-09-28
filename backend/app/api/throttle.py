"""Rate limits and once-per-period rules that hold across every API worker.

These used to be dicts in each module (the write limiter, the public API's
read limiter, the pulse vote dedup, the Explore summary cooldown, the
upstream lookup budget), which was correct only while the backend ran as
one process. With several uvicorn workers each would keep its own copy: a
client could vote twice on the same issue by landing on the other worker,
and every per-IP limit would stretch to its value times the number of
workers.

Where it lives. A small SQLite file in RAM (THROTTLE_DB_PATH, /dev/shm by
default): shared by every worker process in the container, and never on
disk. That is the same lifetime the dicts had — it resets when the
container restarts — and the same exposure: what it records about visitors
(which issue a visitor voted on today) can be read only from the running
container's memory, as the dicts could, never from the data volume.

Keys. Per-client keys are an HMAC of the client IP (with a purpose and a
scope) under a random salt this store makes for each UTC day (client_key).
The IP itself is never stored, a key can't be joined to SiteVisit's visitor
hash (a different salt), and once a day's salt is deleted no key made with
it can be recomputed from an address. A day's salt is kept through the
next day, not deleted at midnight: a client's limits and claims are
counted under both its keys, so a rule doesn't restart at 00:00 UTC — a
pulse vote at 23:59 must still hold off a second one a minute later, as
the per-process dicts' rolling 24 hours did (and a rate window straddling
midnight must still count both halves). No rule here is longer than a day,
so that is as long as an old key can matter. derived_salt uses a salt of
its own that is deleted when its day ends: what it salts (the visit
counter's fallback) promises no longer.

Tables:

  windows  a request count per fixed window. `hit` estimates a sliding
           window from the current and previous counts (previous × the
           share of it still inside the window, plus current), which holds
           a limit across a window boundary where a plain fixed window
           would allow twice the limit.
  claims   when something was last claimed. `claim` succeeds only when the
           previous claim is at least `period` old, in one conditional
           upsert, so two workers can't both win it.

Each operation is one `BEGIN IMMEDIATE` transaction, so SQLite's writer
lock makes the read-modify-write atomic across processes. Every row
carries the time it stops mattering; expired rows are deleted at most once
a minute per process.

Cost: plain sqlite3 on a per-thread connection — measured at ~40 µs a `hit`
and ~15 µs a `client_key` on a development container (SQLAlchemy sessions
cost ~1.4 ms for the same work). Paid by every request that
reaches the backend on a limited route: every mutation (WriteRateLimit), the
public API, Explore search and /api/qa (public.RateLimit), and the two
live-lookup routes, bills/{id}/record and explore/{id}/comments
(UpstreamRouteLimit, plus the shared hourly budget on a cache miss).

Every function fails open. A limiter that can't reach its store lets the
request through and logs it, rather than turning a locked database into an
outage of every endpoint behind it.
"""

import hashlib
import hmac
import logging
import math
import os
import secrets
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Where state shared by the processes of one container, and by nothing else,
# lives: /dev/shm is tmpfs on Linux (and per container); elsewhere, the temp
# directory — a development machine without /dev/shm runs one process. Also
# where main.py keeps the pipeline-process lock.
# CIVITAS_RAM_DIR overrides it (the test suite gives each run its own).
RAM_DIR = os.environ.get("CIVITAS_RAM_DIR") or (
    "/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir()
)
_path = os.environ.get("THROTTLE_DB_PATH") or os.path.join(RAM_DIR, "civitas_throttle.db")

# On the request path: a client held for long because another worker holds
# the lock is worse than a limit that lets one request through.
_BUSY_TIMEOUT_S = 2.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS windows (
    bucket TEXT NOT NULL, key TEXT NOT NULL, window INTEGER NOT NULL,
    count INTEGER NOT NULL, expires_at REAL NOT NULL,
    PRIMARY KEY (bucket, key, window)
);
CREATE TABLE IF NOT EXISTS claims (
    bucket TEXT NOT NULL, key TEXT NOT NULL,
    claimed_at REAL NOT NULL, expires_at REAL NOT NULL,
    PRIMARY KEY (bucket, key)
);
CREATE TABLE IF NOT EXISTS salt_days (
    kind TEXT NOT NULL, date TEXT NOT NULL, salt BLOB NOT NULL,
    PRIMARY KEY (kind, date)
);
CREATE INDEX IF NOT EXISTS windows_expiry ON windows (expires_at);
CREATE INDEX IF NOT EXISTS claims_expiry ON claims (expires_at);
"""

_local = threading.local()
# Every connection opened, so use_path can close them all — including those
# of threads that won't come back to notice the path moved.
_conns: list[sqlite3.Connection] = []
_conns_lock = threading.Lock()
_generation = 0  # bumped by use_path: every thread reconnects

_PURGE_INTERVAL_S = 60.0
_purge_lock = threading.Lock()
_last_purge = 0.0


def use_path(path: str) -> None:
    """Point the store at `path` (tests; the path is otherwise fixed),
    closing every connection to the previous one."""
    global _path, _generation
    with _conns_lock:
        _path = path
        _generation += 1
        _salt_cache.clear()
        for conn in _conns:
            conn.close()
        _conns.clear()


def _enable_wal(conn: sqlite3.Connection) -> None:
    """Switch the store to WAL. Two workers creating it at the same moment
    race for the switch, and SQLite can refuse the loser at once rather than
    wait on the busy timeout — so retry for as long as that timeout, and
    accept a file another process has already switched."""
    deadline = time.monotonic() + _BUSY_TIMEOUT_S
    while True:
        try:
            if conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal":
                return
        except sqlite3.OperationalError:
            pass
        if time.monotonic() >= deadline:
            raise sqlite3.OperationalError("throttle store: could not switch to WAL")
        time.sleep(0.01)


def _conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None or _local.generation != _generation:
        # check_same_thread off only so use_path can close it; each
        # connection is still used by the one thread that opened it.
        conn = sqlite3.connect(_path, timeout=_BUSY_TIMEOUT_S, isolation_level=None, check_same_thread=False)
        try:
            _enable_wal(conn)
            # In RAM already: a sync would buy nothing.
            conn.execute("PRAGMA synchronous=OFF")
            # A deleted salt must be gone, not left in a freed page
            # (_drop_salts also truncates the WAL, the other place it lingers).
            conn.execute("PRAGMA secure_delete=ON")
            conn.executescript(_SCHEMA)
        except sqlite3.Error:
            # Not kept, so the next call retries — close it, or each failed
            # setup under contention would leave one open.
            conn.close()
            raise
        with _conns_lock:
            _conns.append(conn)
        _local.conn, _local.generation = conn, _generation
    return conn


def _discard(conn: sqlite3.Connection) -> None:
    """Close a connection that can't be trusted any more, and forget it, so
    this thread's next call opens a fresh one."""
    with _conns_lock:
        if conn in _conns:
            _conns.remove(conn)
    try:
        conn.close()
    except sqlite3.Error:
        pass
    if getattr(_local, "conn", None) is conn:
        _local.conn = None


class _Txn:
    """BEGIN IMMEDIATE ... COMMIT, rolled back on any error.

    A COMMIT that fails (busy) leaves the transaction open, holding the
    store's write lock: it is rolled back, and a connection that still can't
    leave its transaction is discarded — reused, its next BEGIN would fail
    and every other worker would wait on the lock it holds."""

    def __enter__(self) -> sqlite3.Connection:
        self.conn = _conn()
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, *_exc) -> None:
        try:
            self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        except sqlite3.Error:
            try:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            if self.conn.in_transaction:
                _discard(self.conn)
            if exc_type is None:
                raise


@dataclass(frozen=True)
class Decision:
    allowed: bool
    remaining: int
    # Epoch seconds. Allowed: when everything counted so far has aged out
    # of the sliding window (the limit is whole again). Refused: the
    # earliest moment the same request would be let through — the end of
    # the current fixed window is not it, because most of that window
    # still counts for a while after.
    reset_at: int
    # False when the store couldn't count the request (it was let through
    # uncounted): `remaining` then describes nothing.
    counted: bool = True


def _retry_at(window: int, period: float, current: int, previous: int, limit: int, cost: int) -> int:
    """When a request of `cost` refused now would next be allowed, given
    `current` counted in this window and `previous` in the last one."""
    start = window * period
    room = limit - current - cost
    if room >= 0 and previous > 0:
        # Later in this window, once enough of the previous one has aged.
        return math.ceil(start + (1 - room / previous) * period)
    if limit - cost < 0:
        return int(start + 2 * period)
    # In the next window, where this one becomes the previous.
    if current == 0:
        return int(start + period)
    fraction = max(0.0, 1 - (limit - cost) / current)
    return math.ceil(start + (1 + fraction) * period)


def _purge_expired(conn: sqlite3.Connection, now: float) -> None:
    global _last_purge
    with _purge_lock:
        if now - _last_purge < _PURGE_INTERVAL_S:
            return
        _last_purge = now
    conn.execute("DELETE FROM windows WHERE expires_at < ?", (now,))
    conn.execute("DELETE FROM claims WHERE expires_at < ?", (now,))


# The store's salts, one table for both kinds, each kept for its own time:
#   key  client_key's: kept through the day after its own, so a rule can
#        count a client's previous-day key and nothing restarts at midnight.
#   day  derived_salt's (the visit counter's fallback): deleted when its day
#        ends, the promise AGENTS.md §8 makes for visit hashes.
_KEY_SALT, _DAY_SALT = "key", "day"
_SALT_KEPT_DAYS_AFTER = {_KEY_SALT: 1, _DAY_SALT: 0}

# kind -> (date, that day's salt, the previous day's or None) as last read
# from the store, per process. Only a new day costs a write transaction;
# every other key is made without one.
_salt_cache: dict[str, tuple[str, bytes, bytes | None]] = {}
_salt_lock = threading.Lock()
_last_forget = -_PURGE_INTERVAL_S  # the first call always checks


def _days_before(day: str, n: int) -> str:
    return (date.fromisoformat(day) - timedelta(days=n)).isoformat()


def _salts_for(kind: str, today: str) -> tuple[bytes | None, bytes | None]:
    """`kind`'s salt for `today` (made if missing) and, for a kind kept past
    its day, yesterday's if the store has it — never made: a key under a
    salt nobody counted with would only find nothing.

    (None, None) when another worker has already moved past `today`: this
    one read the clock just before midnight. Making the day's salt again
    then would bring back a salt that was deleted — so it doesn't, and the
    caller treats it as the store being unavailable."""
    with _salt_lock:
        cached = _salt_cache.get(kind)
        if cached is not None and cached[0] == today:
            return cached[1], cached[2]
    keep = _SALT_KEPT_DAYS_AFTER[kind]
    yesterday = _days_before(today, 1)
    with _Txn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO salt_days (kind, date, salt) SELECT ?, ?, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM salt_days WHERE kind = ? AND date > ?)",
            (kind, today, secrets.token_bytes(32), kind, today),
        )
        # Earlier days only, never "any other": a worker behind midnight
        # must not delete the new day's salt another already made.
        dropped = conn.execute(
            "DELETE FROM salt_days WHERE kind = ? AND date < ?", (kind, _days_before(today, keep)),
        ).rowcount
        row = conn.execute("SELECT salt FROM salt_days WHERE kind = ? AND date = ?", (kind, today)).fetchone()
        previous = None
        if keep:
            prior = conn.execute(
                "SELECT salt FROM salt_days WHERE kind = ? AND date = ?", (kind, yesterday),
            ).fetchone()
            previous = prior[0] if prior else None
    if dropped:
        # Truncating can wait on another worker's read, so it never runs on
        # a request: the minute tick (forget_stale_salt) does it.
        global _truncate_pending
        _truncate_pending = True
    if row is None:
        return None, None
    with _salt_lock:
        _salt_cache[kind] = (today, row[0], previous)
    return row[0], previous


_truncate_pending = False


def _truncate_wal() -> None:
    """Checkpoint the WAL and truncate it to nothing: its frames still hold
    the page a deleted salt was on. Another worker's open read blocks it —
    reported in the result, not raised — in which case the next
    forget_stale_salt tick tries again."""
    global _truncate_pending
    try:
        busy = _conn().execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
    except sqlite3.Error:
        busy = 1
        logger.warning("Could not truncate the throttle store's WAL", exc_info=True)
    _truncate_pending = bool(busy)
    if busy:
        logger.info("Throttle store WAL still in use — truncating it on the next tick")


def forget_stale_salt() -> None:
    """Drop the salts nothing needs any more, each kind after its own time —
    from this process's memory, and from the store — without waiting for a
    new day's first key: with a salt gone, nothing made with it can be
    recomputed from an address. At most once a minute (run_maintenance).
    Creates nothing where the store doesn't exist yet."""
    global _last_forget
    today = datetime.now(timezone.utc).date().isoformat()
    with _salt_lock:
        # A cache made yesterday also holds the day before's salt.
        for kind in [k for k, cached in _salt_cache.items() if cached[0] != today]:
            del _salt_cache[kind]
        now = time.monotonic()
        if now - _last_forget < _PURGE_INTERVAL_S:
            return
        _last_forget = now
    if not os.path.exists(_path):
        return
    try:
        with _Txn() as conn:
            dropped = sum(
                conn.execute(
                    "DELETE FROM salt_days WHERE kind = ? AND date < ?", (kind, _days_before(today, keep)),
                ).rowcount
                for kind, keep in _SALT_KEPT_DAYS_AFTER.items()
            )
        if dropped or _truncate_pending:
            _truncate_wal()
    except sqlite3.Error:
        logger.warning("Could not drop a stale throttle salt", exc_info=True)


async def run_maintenance() -> None:
    """Drop stale salts about once a minute, for the process's lifetime
    (main.lifespan starts it in every role). Without it, a store with no
    traffic after midnight would keep a salt past its time: the deletions
    in _salts_for run only when a new day's first key is made."""
    import asyncio

    while True:
        try:
            await asyncio.to_thread(forget_stale_salt)
        except Exception:
            logger.warning("Throttle maintenance failed", exc_info=True)
        await asyncio.sleep(_PURGE_INTERVAL_S)


def derived_salt(purpose: str) -> bytes | None:
    """A salt for today, the same in every process of this container, in RAM
    and deleted when this UTC day ends: for a caller whose shared salt is
    unavailable (visits' fallback). Not derived from the key salts, which
    outlive their day. None if the store can't give one either."""
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        salt, _previous = _salts_for(_DAY_SALT, today)
    except sqlite3.Error:
        return None
    if salt is None:
        return None
    return hmac.new(salt, f"derived\x00{purpose}".encode(), hashlib.sha256).digest()


class ClientKey(str):
    """A client's key under today's salt, carrying its key under
    yesterday's (`previous`, None when the store has no salt for
    yesterday): hit and claim count both, so a rule holds across midnight."""

    previous: str | None = None


def _hmac_key(salt: bytes, message: bytes) -> str:
    return hmac.new(salt, message, hashlib.sha256).hexdigest()[:32]


def client_key(ip: str, purpose: str, scope: str = "") -> ClientKey | None:
    """The key a per-client limit counts `ip` under, for `purpose` (and
    `scope` within it: the issue a pulse vote is on), with its key under
    yesterday's salt. A day's salt is deleted once the day after it ends
    (the first key of the day after that, or the minute tick). Only
    earlier days are deleted, never "any other": a worker that read the
    clock just before midnight must not delete the new day's salt another
    worker already made (and cached).

    None when the store can't be read: hit and claim then let the request
    through, as they would on their own failure — never one shared key,
    which would count every affected client as one."""
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        salt, previous = _salts_for(_KEY_SALT, today)
    except sqlite3.Error:
        logger.warning("Throttle salt unavailable — not limiting this request", exc_info=True)
        return None
    if salt is None:
        return None
    message = f"{purpose}\x00{ip}\x00{scope}".encode()
    key = ClientKey(_hmac_key(salt, message))
    key.previous = _hmac_key(previous, message) if previous is not None else None
    return key


def _previous_key(key: str) -> str | None:
    return getattr(key, "previous", None)


def hit(bucket: str, key: str | None, *, limit: int, period: float, cost: int = 1) -> Decision:
    """Count `cost` units (one request, by default) against `limit` per
    `period` seconds for `key` — or, for a None key (client_key failed),
    let it through uncounted.

    A refused request is not counted, so a client that keeps retrying
    through a 429 is let back in as its earlier requests age out, as with
    the per-process limiters this replaced.
    """
    now = time.time()
    window = int(now // period)
    elapsed = (now - window * period) / period
    reset_at = int((window + 2) * period)
    if key is None:
        return Decision(True, limit, int((window + 1) * period), counted=False)
    # Counted under today's key; read under yesterday's too (ClientKey).
    keys = (str(key), _previous_key(key) or str(key))
    try:
        with _Txn() as conn:
            conn.execute(
                "INSERT INTO windows (bucket, key, window, count, expires_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (bucket, key, window) DO UPDATE SET count = count + excluded.count",
                (bucket, str(key), window, cost, (window + 2) * period),
            )
            current, previous = conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN window = ? THEN count END), 0), "
                "COALESCE(SUM(CASE WHEN window = ? THEN count END), 0) "
                "FROM windows WHERE bucket = ? AND key IN (?, ?) AND window IN (?, ?)",
                (window, window - 1, bucket, *keys, window, window - 1),
            ).fetchone()
            estimate = previous * (1 - elapsed) + current
            allowed = estimate <= limit
            if not allowed:
                reset_at = _retry_at(window, period, current - cost, previous, limit, cost)
                conn.execute(
                    "UPDATE windows SET count = count - ? WHERE bucket = ? AND key = ? AND window = ?",
                    (cost, bucket, str(key), window),
                )
            _purge_expired(conn, now)
    except sqlite3.Error:
        logger.warning("Throttle %r unavailable — allowing the request", bucket, exc_info=True)
        return Decision(True, limit, int((window + 1) * period), counted=False)
    remaining = max(0, math.floor(limit - estimate)) if allowed else 0
    return Decision(allowed, remaining, reset_at)


class Unavailable(RuntimeError):
    """The store couldn't answer, for a caller that asked not to fail open."""


def claim(bucket: str, key: str | None, *, period: float, fail_open: bool = True) -> bool:
    """Claim `key` unless it was claimed less than `period` seconds ago.
    True when this caller got it. When the store can't answer (or `key` is
    None: client_key failed), True — or, with fail_open=False, Unavailable
    for a caller whose rule matters more than its availability."""
    if key is None:
        if not fail_open:
            raise Unavailable(bucket)
        return True
    now = time.time()
    previous = _previous_key(key)
    try:
        with _Txn() as conn:
            # A claim made under yesterday's key (ClientKey) still holds.
            held = previous is not None and conn.execute(
                "SELECT 1 FROM claims WHERE bucket = ? AND key = ? AND claimed_at > ?",
                (bucket, previous, now - period),
            ).fetchone() is not None
            won = not held and conn.execute(
                "INSERT INTO claims (bucket, key, claimed_at, expires_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (bucket, key) DO UPDATE SET claimed_at = excluded.claimed_at, "
                "expires_at = excluded.expires_at WHERE claims.claimed_at <= ? "
                "RETURNING claimed_at",
                (bucket, str(key), now, now + period, now - period),
            ).fetchone() is not None
            _purge_expired(conn, now)
    except sqlite3.Error as error:
        if not fail_open:
            raise Unavailable(bucket) from error
        logger.warning("Throttle %r unavailable — allowing the claim", bucket, exc_info=True)
        return True
    return won


def release(bucket: str, key: str | None) -> None:
    """Give back a claim whose work didn't happen (the issue voted on didn't
    exist), so it doesn't hold the next attempt off."""
    if key is None:
        return
    try:
        with _Txn() as conn:
            conn.execute("DELETE FROM claims WHERE bucket = ? AND key = ?", (bucket, str(key)))
    except sqlite3.Error:
        logger.warning("Throttle %r release failed", bucket, exc_info=True)


def clear(*buckets: str) -> None:
    """Forget everything counted in `buckets` (tests reset limits this way)."""
    marks = ",".join("?" * len(buckets))
    try:
        with _Txn() as conn:
            conn.execute(f"DELETE FROM windows WHERE bucket IN ({marks})", buckets)
            conn.execute(f"DELETE FROM claims WHERE bucket IN ({marks})", buckets)
    except sqlite3.Error:
        logger.warning("Throttle clear failed", exc_info=True)
