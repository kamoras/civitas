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

# /dev/shm is tmpfs on Linux (and in every container); elsewhere, the temp
# directory — a development machine without /dev/shm runs one process.
_DEFAULT_DIR = "/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir()
_path = os.environ.get("THROTTLE_DB_PATH") or os.path.join(_DEFAULT_DIR, "civitas_throttle.db")

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
    global _path, _generation
    with _conns_lock:
        _path = path
        _generation += 1
        for conn in _conns:
            conn.close()
        _conns.clear()


def _conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None or _local.generation != _generation:
        # check_same_thread off only so use_path can close it; each
        # connection is still used by the one thread that opened it.
        conn = sqlite3.connect(_path, timeout=_BUSY_TIMEOUT_S, isolation_level=None, check_same_thread=False)
        with _conns_lock:
            _conns.append(conn)
        conn.execute("PRAGMA journal_mode=WAL")
        # In RAM already: a sync would buy nothing.
        conn.execute("PRAGMA synchronous=OFF")
        conn.executescript(_SCHEMA)
        _local.conn, _local.generation = conn, _generation
    return conn


class _Txn:
    """BEGIN IMMEDIATE ... COMMIT, rolled back on any error."""

    def __enter__(self) -> sqlite3.Connection:
        self.conn = _conn()
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, *_exc) -> None:
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    remaining: int
    reset_at: int  # epoch seconds at which the current window ends


def _purge_expired(conn: sqlite3.Connection, now: float) -> None:
    global _last_purge
    with _purge_lock:
        if now - _last_purge < _PURGE_INTERVAL_S:
            return
        _last_purge = now
    conn.execute("DELETE FROM windows WHERE expires_at < ?", (now,))
    conn.execute("DELETE FROM claims WHERE expires_at < ?", (now,))


def client_key(ip: str, purpose: str, scope: str = "") -> str:
    """The key a per-client limit counts `ip` under, for `purpose` (and
    `scope` within it: the issue a pulse vote is on). Keyed by a salt that
    exists for the current UTC day only; the previous day's is deleted when
    the first key of a new day is made."""
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        with _Txn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO salts (date, salt) VALUES (?, ?)", (today, secrets.token_bytes(32)),
            )
            conn.execute("DELETE FROM salts WHERE date != ?", (today,))
            salt = conn.execute("SELECT salt FROM salts WHERE date = ?", (today,)).fetchone()[0]
    except sqlite3.Error:
        # hit/claim fail open on the same store, so the key isn't used.
        logger.warning("Throttle salt unavailable", exc_info=True)
        return "unavailable"
    return hmac.new(salt, f"{purpose}\x00{ip}\x00{scope}".encode(), hashlib.sha256).hexdigest()[:32]


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
            estimate = (row[0] if row else 0) * (1 - elapsed) + current
            allowed = estimate <= limit
            if not allowed:
                conn.execute(
                    "UPDATE windows SET count = count - ? WHERE bucket = ? AND key = ? AND window = ?",
                    (cost, bucket, key, window),
                )
            _purge_expired(conn, now)
    except sqlite3.Error:
        logger.warning("Throttle %r unavailable — allowing the request", bucket, exc_info=True)
        return Decision(True, limit, reset_at)
    remaining = max(0, math.floor(limit - estimate)) if allowed else 0
    return Decision(allowed, remaining, reset_at)


def claim(bucket: str, key: str, *, period: float) -> bool:
    """Claim `key` unless it was claimed less than `period` seconds ago.
    True when this caller got it."""
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
    except sqlite3.Error:
        logger.warning("Throttle %r unavailable — allowing the claim", bucket, exc_info=True)
        return True
    return won


def release(bucket: str, key: str) -> None:
    """Give back a claim whose work didn't happen (the issue voted on didn't
    exist), so it doesn't hold the next attempt off."""
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
