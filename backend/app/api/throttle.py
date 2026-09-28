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
scope) under a random salt this store keeps for the current UTC day only
(client_key). The IP itself is never stored, a key can't be joined to
SiteVisit's visitor hash (a different salt), and once the day's salt is
replaced no key can be recomputed from an address.

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
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Where state shared by the processes of one container, and by nothing else,
# lives: /dev/shm is tmpfs on Linux (and per container); elsewhere, the temp
# directory — a development machine without /dev/shm runs one process. Also
# where main.py keeps the pipeline-process lock.
RAM_DIR = "/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir()
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
CREATE TABLE IF NOT EXISTS salts (date TEXT PRIMARY KEY, salt BLOB NOT NULL);
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
    global _path, _generation, _salt_cache
    with _conns_lock:
        _path = path
        _generation += 1
        _salt_cache = None
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


# (date, salt) as last read from the store, per process. Only a new day
# costs a write transaction; every other key is made without one.
_salt_cache: tuple[str, bytes] | None = None
_salt_lock = threading.Lock()
_last_forget = -_PURGE_INTERVAL_S  # the first call always checks


def _salt_for(today: str) -> bytes:
    global _salt_cache
    with _salt_lock:
        if _salt_cache is not None and _salt_cache[0] == today:
            return _salt_cache[1]
    with _Txn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO salts (date, salt) VALUES (?, ?)", (today, secrets.token_bytes(32)),
        )
        dropped = conn.execute("DELETE FROM salts WHERE date != ?", (today,)).rowcount
        salt = conn.execute("SELECT salt FROM salts WHERE date = ?", (today,)).fetchone()[0]
    if dropped:
        # Truncating can wait on another worker's read, so it never runs on
        # a request: the minute tick (forget_stale_salt) does it.
        global _truncate_pending
        _truncate_pending = True
    with _salt_lock:
        _salt_cache = (today, salt)
    return salt


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
    """Drop a salt for a day that has ended — from this process's memory,
    and from the store — without waiting for the new day's first key.
    Called about once a minute (visits.run_visit_consumer's idle tick): with
    the old salt gone, no key from that day can be recomputed from an
    address. Creates nothing where the store doesn't exist yet."""
    global _salt_cache, _last_forget
    today = datetime.now(timezone.utc).date().isoformat()
    with _salt_lock:
        if _salt_cache is not None and _salt_cache[0] != today:
            _salt_cache = None
        now = time.monotonic()
        if now - _last_forget < _PURGE_INTERVAL_S:
            return
        _last_forget = now
    if not os.path.exists(_path):
        return
    try:
        with _Txn() as conn:
            dropped = conn.execute("DELETE FROM salts WHERE date != ?", (today,)).rowcount
        if dropped or _truncate_pending:
            _truncate_wal()
    except sqlite3.Error:
        logger.warning("Could not drop a stale throttle salt", exc_info=True)


def derived_salt(purpose: str) -> bytes | None:
    """A salt for today, the same in every process of this container, that
    lives only as long as the store's own (RAM, this UTC day): for a caller
    whose shared salt is unavailable (visits' fallback). None if the store
    can't be read either."""
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        salt = _salt_for(today)
    except sqlite3.Error:
        return None
    return hmac.new(salt, f"derived\x00{purpose}".encode(), hashlib.sha256).digest()


def client_key(ip: str, purpose: str, scope: str = "") -> str | None:
    """The key a per-client limit counts `ip` under, for `purpose` (and
    `scope` within it: the issue a pulse vote is on). Keyed by a salt that
    exists for the current UTC day only; the previous day's is deleted when
    the first key of a new day is made.

    None when the store can't be read: hit and claim then let the request
    through, as they would on their own failure — never one shared key,
    which would count every affected client as one."""
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        salt = _salt_for(today)
    except sqlite3.Error:
        logger.warning("Throttle salt unavailable — not limiting this request", exc_info=True)
        return None
    return hmac.new(salt, f"{purpose}\x00{ip}\x00{scope}".encode(), hashlib.sha256).hexdigest()[:32]


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
        return Decision(True, limit, int((window + 1) * period))
    try:
        with _Txn() as conn:
            current = conn.execute(
                "INSERT INTO windows (bucket, key, window, count, expires_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (bucket, key, window) DO UPDATE SET count = count + excluded.count "
                "RETURNING count",
                (bucket, key, window, cost, (window + 2) * period),
            ).fetchone()[0]
            row = conn.execute(
                "SELECT count FROM windows WHERE bucket = ? AND key = ? AND window = ?",
                (bucket, key, window - 1),
            ).fetchone()
            previous = row[0] if row else 0
            estimate = previous * (1 - elapsed) + current
            allowed = estimate <= limit
            if not allowed:
                reset_at = _retry_at(window, period, current - cost, previous, limit, cost)
                conn.execute(
                    "UPDATE windows SET count = count - ? WHERE bucket = ? AND key = ? AND window = ?",
                    (cost, bucket, key, window),
                )
            _purge_expired(conn, now)
    except sqlite3.Error:
        logger.warning("Throttle %r unavailable — allowing the request", bucket, exc_info=True)
        return Decision(True, limit, int((window + 1) * period))
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
    try:
        with _Txn() as conn:
            won = conn.execute(
                "INSERT INTO claims (bucket, key, claimed_at, expires_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (bucket, key) DO UPDATE SET claimed_at = excluded.claimed_at, "
                "expires_at = excluded.expires_at WHERE claims.claimed_at <= ? "
                "RETURNING claimed_at",
                (bucket, key, now, now + period, now - period),
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
            conn.execute("DELETE FROM claims WHERE bucket = ? AND key = ?", (bucket, key))
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
