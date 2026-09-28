"""api/throttle.py: the limits every API worker process shares."""

import multiprocessing
import sqlite3
import time
from datetime import datetime, timezone

import pytest

from app.api import throttle


def _rows(path: str, sql: str) -> list:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _worker(path: str, calls: int, start, results) -> None:
    """One API worker process: its own interpreter and module state, the
    same store file."""
    throttle.use_path(path)
    start.wait()
    allowed = sum(throttle.hit("write", "k", limit=20, period=3600).allowed for _ in range(calls))
    claimed = sum(throttle.claim("pulse", "k:1", period=3600) for _ in range(calls))
    results.put((allowed, claimed, throttle.client_key("203.0.113.1", "pulse", "1")))


def test_limits_claims_and_keys_hold_across_processes(tmp_path):
    # The reason this module exists: with per-process state, two workers
    # would each allow 20 (40 total), each grant the claim once, and — with
    # a salt of their own — key one client differently.
    path = str(tmp_path / "throttle.db")
    ctx = multiprocessing.get_context("spawn")
    start, results = ctx.Event(), ctx.Queue()
    procs = [ctx.Process(target=_worker, args=(path, 15, start, results)) for _ in range(2)]
    for p in procs:
        p.start()
    start.set()
    totals = [results.get(timeout=60) for _ in procs]
    for p in procs:
        p.join(timeout=60)

    assert sum(allowed for allowed, _, _ in totals) == 20
    assert sum(claimed for _, claimed, _ in totals) == 1
    assert totals[0][2] == totals[1][2]


@pytest.mark.usefixtures("throttle_store")
class TestHit:
    def test_remaining_counts_down_and_refusals_are_not_counted(self):
        first = throttle.hit("b", "k", limit=3, period=3600)
        assert (first.allowed, first.remaining) == (True, 2)
        throttle.hit("b", "k", limit=3, period=3600)
        throttle.hit("b", "k", limit=3, period=3600)
        for _ in range(5):
            refused = throttle.hit("b", "k", limit=3, period=3600)
            assert (refused.allowed, refused.remaining) == (False, 0)

    def test_a_cost_counts_as_that_many(self):
        assert throttle.hit("b", "k", limit=7, period=3600, cost=5).allowed
        assert not throttle.hit("b", "k", limit=7, period=3600, cost=5).allowed
        assert throttle.hit("b", "k", limit=7, period=3600, cost=2).allowed  # the refusal wasn't counted

    def test_the_previous_window_still_counts_across_a_boundary(self, monkeypatch):
        # A fixed window would reset at the boundary and allow 2x the limit.
        period = 60.0
        at = [1_000_000 * period + period - 1]  # one second before a boundary
        monkeypatch.setattr(throttle.time, "time", lambda: at[0])
        for _ in range(3):
            assert throttle.hit("b", "k", limit=3, period=period).allowed
        at[0] += 2  # one second into the next window: ~98% of the last one still counts
        assert not throttle.hit("b", "k", limit=3, period=period).allowed
        at[0] += period  # the old window has aged out entirely
        assert throttle.hit("b", "k", limit=3, period=period).allowed

    def test_allowed_reset_is_when_everything_counted_has_aged_out(self, monkeypatch):
        monkeypatch.setattr(throttle.time, "time", lambda: 125.0)
        assert throttle.hit("b", "k", limit=3, period=60).reset_at == 240

    @pytest.mark.parametrize("burst_at,limit", [(59.0, 3), (30.0, 3), (1.0, 5), (59.9, 60)])
    def test_a_refusal_names_the_first_moment_that_works(self, monkeypatch, burst_at, limit):
        # Waiting until the advertised moment must be enough, and a moment
        # sooner must not: the end of the fixed window was neither.
        period = 60.0
        at = [120 * period + burst_at]
        monkeypatch.setattr(throttle.time, "time", lambda: at[0])
        for _ in range(limit):
            assert throttle.hit("b", "k", limit=limit, period=period).allowed
        refused = throttle.hit("b", "k", limit=limit, period=period)
        assert not refused.allowed
        at[0] = refused.reset_at - 0.5
        assert not throttle.hit("b", "k", limit=limit, period=period).allowed
        at[0] = refused.reset_at
        assert throttle.hit("b", "k", limit=limit, period=period).allowed

    def test_expired_windows_are_purged(self, throttle_store, monkeypatch):
        at = [0.0]
        monkeypatch.setattr(throttle.time, "time", lambda: at[0])
        monkeypatch.setattr(throttle, "_last_purge", -1e9)
        throttle.hit("b", "old", limit=3, period=60)
        at[0] = 600.0
        throttle.hit("b", "new", limit=3, period=60)
        assert _rows(throttle_store, "SELECT key FROM windows") == [("new",)]

    def test_clear(self, throttle_store):
        throttle.hit("b", "k", limit=3, period=60)
        throttle.hit("c", "k", limit=3, period=60)
        throttle.clear("b")
        assert _rows(throttle_store, "SELECT bucket FROM windows") == [("c",)]


@pytest.mark.usefixtures("throttle_store")
class TestClaim:
    def test_once_per_period(self, monkeypatch):
        at = [1000.0]
        monkeypatch.setattr(throttle.time, "time", lambda: at[0])
        assert throttle.claim("b", "k", period=30)
        assert not throttle.claim("b", "k", period=30)
        at[0] += 29
        assert not throttle.claim("b", "k", period=30)
        at[0] += 1
        assert throttle.claim("b", "k", period=30)

    def test_keys_and_buckets_are_independent(self):
        assert throttle.claim("b", "k", period=30)
        assert throttle.claim("b", "other", period=30)
        assert throttle.claim("c", "k", period=30)

    def test_release_frees_the_claim(self):
        assert throttle.claim("b", "k", period=30)
        throttle.release("b", "k")
        assert throttle.claim("b", "k", period=30)

    def test_expired_claims_are_purged_in_every_bucket(self, throttle_store, monkeypatch):
        # A quiet bucket's rows (a day's pulse claims) go as surely as a
        # busy one's: the purge is by expiry, across buckets, on a timer.
        at = [0.0]
        monkeypatch.setattr(throttle.time, "time", lambda: at[0])
        monkeypatch.setattr(throttle, "_last_purge", 0.0)
        throttle.claim("pulse", "old", period=86400)
        at[0] = 86400 + 61
        throttle.claim("summary", "new", period=30)
        assert _rows(throttle_store, "SELECT key FROM claims") == [("new",)]

    def test_the_purge_runs_at_most_once_a_minute(self, throttle_store, monkeypatch):
        at = [1000.0]
        monkeypatch.setattr(throttle.time, "time", lambda: at[0])
        monkeypatch.setattr(throttle, "_last_purge", 1000.0)
        throttle.claim("b", "old", period=1)
        at[0] += 30
        throttle.claim("b", "new", period=1)
        assert _rows(throttle_store, "SELECT COUNT(*) FROM claims") == [(2,)]


@pytest.mark.usefixtures("throttle_store")
class TestClientKey:
    def test_stable_within_a_day_and_distinct_by_purpose_and_scope(self):
        key = throttle.client_key("203.0.113.1", "pulse", "1")
        assert key == throttle.client_key("203.0.113.1", "pulse", "1")
        others = {
            throttle.client_key("203.0.113.1", "pulse", "2"),
            throttle.client_key("203.0.113.1", "write"),
            throttle.client_key("203.0.113.2", "pulse", "1"),
        }
        assert key not in others and len(others) == 3
        assert len(key) == 32 and "203.0.113.1" not in key

    def test_a_new_day_replaces_the_salt(self, throttle_store, monkeypatch):
        key = throttle.client_key("203.0.113.1", "pulse", "1")

        class _Tomorrow(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2099, 1, 2, tzinfo=timezone.utc)

        monkeypatch.setattr(throttle, "datetime", _Tomorrow)
        assert throttle.client_key("203.0.113.1", "pulse", "1") != key
        assert _rows(throttle_store, "SELECT date FROM salt_days WHERE kind = 'key'") == [("2099-01-02",)]

    def test_not_the_visitor_hash_a_visit_stores(self):
        from app.api.visits import _visitor_hash

        key = throttle.client_key("203.0.113.1", "write")
        for (visit_salt,) in _rows(throttle._path, "SELECT salt FROM salt_days WHERE kind = 'key'"):
            assert _visitor_hash("203.0.113.1", visit_salt) != key


class TestFailsOpen:
    """A locked or unusable store lets requests through rather than turning
    every limited endpoint into an outage."""

    @pytest.fixture(autouse=True)
    def _unusable(self, tmp_path):
        previous = throttle._path
        throttle.use_path(str(tmp_path / "no-such-dir" / "throttle.db"))
        yield
        throttle.use_path(previous)

    def test_hit(self):
        decision = throttle.hit("b", "k", limit=3, period=60)
        assert decision.allowed and decision.remaining == 3
        assert not decision.counted

    def test_claim(self):
        assert throttle.claim("b", "k", period=30)

    def test_release_and_clear(self):
        throttle.release("b", "k")  # logs, doesn't raise
        throttle.clear("b")

    def test_client_key(self):
        assert throttle.client_key("203.0.113.1", "write") is None


@pytest.mark.usefixtures("throttle_store")
class TestNoKey:
    """A request whose key couldn't be made is let through, not counted
    under one key every such client would share."""

    def test_hit_and_claim_let_it_through(self):
        for _ in range(5):
            assert throttle.hit("write", None, limit=1, period=60).allowed
            assert throttle.claim("pulse", None, period=86400)
        throttle.release("pulse", None)

    def test_the_store_is_not_touched(self, throttle_store):
        import os

        throttle.hit("write", None, limit=1, period=60)
        throttle.claim("pulse", None, period=86400)
        assert not os.path.exists(throttle_store)


def test_the_salt_is_read_once_a_day_per_process(throttle_store, monkeypatch):
    # Every key would otherwise take the store's write lock a second time.
    throttle.client_key("203.0.113.1", "write")
    began = []
    real_enter = throttle._Txn.__enter__
    monkeypatch.setattr(throttle._Txn, "__enter__", lambda self: (began.append(1), real_enter(self))[1])
    for _ in range(5):
        throttle.client_key("203.0.113.1", "write")
    assert began == []


def test_a_held_write_lock_is_waited_on_briefly(throttle_store, monkeypatch):
    monkeypatch.setattr(throttle, "_BUSY_TIMEOUT_S", 0.2)
    throttle.use_path(throttle_store + "x")  # a fresh connection, with the short timeout
    throttle.hit("b", "k", limit=3, period=60)  # creates the schema
    blocker = sqlite3.connect(throttle_store + "x", isolation_level=None)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        assert throttle.hit("b", "k", limit=3, period=60).allowed  # failed open
        assert time.monotonic() - started < 5
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()


def test_pointing_at_the_same_path_again_reconnects(throttle_store):
    throttle.hit("b", "k", limit=3, period=60)
    throttle.use_path(throttle_store)  # closes this thread's connection
    assert not throttle.hit("b", "k", limit=1, period=60).allowed  # still counting, not failing open


def test_a_connection_whose_setup_fails_is_closed(throttle_store, monkeypatch):
    # Not kept for the next call, so it must not stay open either.
    opened = []
    real_connect = sqlite3.connect

    class _Failing:
        def __init__(self, conn):
            self.conn, self.closed = conn, False

        def execute(self, sql, *args):
            raise sqlite3.OperationalError("database is locked")

        def close(self):
            self.closed = True
            self.conn.close()

    def connect(*args, **kwargs):
        wrapped = _Failing(real_connect(*args, **kwargs))
        opened.append(wrapped)
        return wrapped

    monkeypatch.setattr(throttle.sqlite3, "connect", connect)
    monkeypatch.setattr(throttle, "_BUSY_TIMEOUT_S", 0.05)  # the WAL switch's retry window
    throttle.use_path(throttle_store)
    assert throttle.hit("b", "k", limit=1, period=60).allowed  # fails open
    assert opened and all(c.closed for c in opened) and throttle._conns == []


class TestForgetStaleSalt:
    def test_yesterdays_salt_is_dropped_without_a_new_key(self, throttle_store, monkeypatch):
        throttle.client_key("203.0.113.1", "write")

        class _Tomorrow(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2099, 1, 2, tzinfo=timezone.utc)

        monkeypatch.setattr(throttle, "datetime", _Tomorrow)
        monkeypatch.setattr(throttle, "_last_forget", -1e9)
        throttle.forget_stale_salt()
        assert throttle._salt_cache == {}
        assert _rows(throttle_store, "SELECT COUNT(*) FROM salt_days WHERE kind = 'key'") == [(0,)]

    def test_todays_is_kept(self, throttle_store, monkeypatch):
        key = throttle.client_key("203.0.113.1", "write")
        monkeypatch.setattr(throttle, "_last_forget", -1e9)
        throttle.forget_stale_salt()
        assert throttle.client_key("203.0.113.1", "write") == key

    def test_creates_no_store(self, throttle_store, monkeypatch):
        import os

        monkeypatch.setattr(throttle, "_last_forget", -1e9)
        throttle.forget_stale_salt()
        assert not os.path.exists(throttle_store)

    def test_at_most_once_a_minute(self, throttle_store, monkeypatch):
        throttle.client_key("203.0.113.1", "write")
        began = []
        real_enter = throttle._Txn.__enter__
        monkeypatch.setattr(throttle._Txn, "__enter__", lambda self: (began.append(1), real_enter(self))[1])
        monkeypatch.setattr(throttle, "_last_forget", -1e9)
        for _ in range(5):
            throttle.forget_stale_salt()
        assert began == [1]


def test_a_claim_that_must_not_fail_open_raises(monkeypatch, tmp_path):
    previous = throttle._path
    throttle.use_path(str(tmp_path / "no-such-dir" / "t.db"))
    try:
        with pytest.raises(throttle.Unavailable):
            throttle.claim("pulse", "k", period=60, fail_open=False)
        with pytest.raises(throttle.Unavailable):
            throttle.claim("pulse", None, period=60, fail_open=False)
    finally:
        throttle.use_path(previous)


def test_a_dropped_salt_leaves_no_bytes_behind(throttle_store, monkeypatch):
    # Not in a freed page, not in the WAL: anyone who could read /dev/shm
    # could otherwise still recompute yesterday's keys.
    import os

    throttle.client_key("203.0.113.1", "write")
    old_salt = _rows(throttle_store, "SELECT salt FROM salt_days WHERE kind = 'key'")[0][0]

    class _Tomorrow(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2099, 1, 2, tzinfo=timezone.utc)

    monkeypatch.setattr(throttle, "datetime", _Tomorrow)
    throttle.client_key("203.0.113.1", "write")
    monkeypatch.setattr(throttle, "_last_forget", -1e9)
    throttle.forget_stale_salt()  # the minute tick truncates the WAL
    for suffix in ("", "-wal"):
        path = throttle_store + suffix
        if os.path.exists(path):
            with open(path, "rb") as fh:
                assert old_salt not in fh.read(), path


def test_a_failed_commit_does_not_leave_the_connection_in_a_transaction(throttle_store, monkeypatch):
    # Reused mid-transaction, the connection's next BEGIN would fail and
    # the write lock it holds would stall every other worker.
    throttle.hit("b", "k", limit=10, period=60)
    conn, _lock = throttle._conn()

    class _CommitFails:
        def __init__(self, inner):
            self.inner = inner

        def execute(self, sql, *args):
            if sql == "COMMIT":
                raise sqlite3.OperationalError("database is locked")
            return self.inner.execute(sql, *args)

        @property
        def in_transaction(self):
            return self.inner.in_transaction

        def close(self):
            self.inner.close()

    monkeypatch.setattr(throttle._local, "conn", _CommitFails(conn))
    assert throttle.hit("b", "k", limit=10, period=60).allowed  # fails open
    assert not conn.in_transaction
    monkeypatch.undo()
    throttle.use_path(throttle_store)
    assert throttle.hit("b", "k", limit=10, period=60).remaining == 8  # the store still works


def test_a_truncation_blocked_by_a_reader_is_retried(throttle_store, monkeypatch):
    import os

    throttle.client_key("203.0.113.1", "write")
    reader = sqlite3.connect(throttle_store, isolation_level=None)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM salt_days").fetchall()  # holds a read snapshot

    class _Tomorrow(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2099, 1, 2, tzinfo=timezone.utc)

    monkeypatch.setattr(throttle, "datetime", _Tomorrow)
    monkeypatch.setattr(throttle, "_BUSY_TIMEOUT_S", 0.1)
    throttle.use_path(throttle_store)  # reconnect with the short timeout
    throttle.client_key("203.0.113.1", "write")  # drops yesterday's salt
    monkeypatch.setattr(throttle, "_last_forget", -1e9)
    throttle.forget_stale_salt()  # blocked by the reader
    assert throttle._truncate_pending
    reader.execute("COMMIT")
    reader.close()
    monkeypatch.setattr(throttle, "_last_forget", -1e9)
    throttle.forget_stale_salt()
    assert not throttle._truncate_pending
    assert os.path.getsize(throttle_store + "-wal") == 0


def test_a_new_days_first_key_never_waits_on_a_checkpoint(throttle_store, monkeypatch):
    # The request path: truncation is left to the minute tick.
    throttle.client_key("203.0.113.1", "write")

    class _Tomorrow(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2099, 1, 2, tzinfo=timezone.utc)

    monkeypatch.setattr(throttle, "datetime", _Tomorrow)
    checkpoints = []
    monkeypatch.setattr(throttle, "_truncate_wal", lambda: checkpoints.append(1))
    throttle.client_key("203.0.113.1", "write")
    assert checkpoints == [] and throttle._truncate_pending


def test_a_worker_behind_midnight_keeps_the_new_days_salt(throttle_store, monkeypatch):
    # Worker B made (and cached) tomorrow's salt; worker A read the clock
    # just before midnight. A must not delete it, or the workers would key
    # the same client differently all day.
    def on(day):
        class _At(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2099, 1, day, 23 if day == 1 else 0, tzinfo=timezone.utc)
        return _At

    monkeypatch.setattr(throttle, "datetime", on(2))
    tomorrow = throttle.client_key("203.0.113.1", "write")
    throttle.use_path(throttle_store)  # another process: no cached salt
    monkeypatch.setattr(throttle, "datetime", on(1))
    throttle.client_key("203.0.113.1", "write")  # the lagging worker
    throttle.use_path(throttle_store)
    monkeypatch.setattr(throttle, "datetime", on(2))
    assert throttle.client_key("203.0.113.1", "write") == tomorrow


class TestAcrossMidnight:
    """A day's salt outlives its day by one, so nothing restarts at 00:00 UTC."""

    @staticmethod
    def _at(monkeypatch, day, hour, minute=0, second=0):
        moment = datetime(2099, 1, day, hour, minute, second, tzinfo=timezone.utc)

        class _At(datetime):
            @classmethod
            def now(cls, tz=None):
                return moment

        monkeypatch.setattr(throttle, "datetime", _At)
        monkeypatch.setattr(throttle.time, "time", lambda: moment.timestamp())

    def test_a_claim_before_midnight_still_holds_after_it(self, throttle_store, monkeypatch):
        self._at(monkeypatch, 1, 23, 59)
        assert throttle.claim("pulse", throttle.client_key("203.0.113.1", "pulse", "7"), period=86400)
        self._at(monkeypatch, 2, 0, 1)
        assert not throttle.claim("pulse", throttle.client_key("203.0.113.1", "pulse", "7"), period=86400)
        self._at(monkeypatch, 2, 23, 59, 30)  # a full day on
        assert throttle.claim("pulse", throttle.client_key("203.0.113.1", "pulse", "7"), period=86400)

    def test_a_window_straddling_midnight_counts_both_halves(self, throttle_store, monkeypatch):
        self._at(monkeypatch, 1, 23, 59, 50)
        for _ in range(5):
            assert throttle.hit("write", throttle.client_key("203.0.113.1", "write"), limit=5, period=60).allowed
        self._at(monkeypatch, 2, 0, 0, 5)
        assert not throttle.hit("write", throttle.client_key("203.0.113.1", "write"), limit=5, period=60).allowed

    def test_yesterdays_salt_is_kept_and_the_day_befores_dropped(self, throttle_store, monkeypatch):
        self._at(monkeypatch, 1, 12)
        throttle.client_key("203.0.113.1", "write")
        self._at(monkeypatch, 2, 12)
        key = throttle.client_key("203.0.113.1", "write")
        assert key.previous is not None
        assert _rows(throttle_store, "SELECT date FROM salt_days WHERE kind = 'key' ORDER BY date") == [("2099-01-01",), ("2099-01-02",)]
        self._at(monkeypatch, 3, 0, 0, 30)
        monkeypatch.setattr(throttle, "_last_forget", -1e9)
        throttle.forget_stale_salt()
        assert _rows(throttle_store, "SELECT date FROM salt_days WHERE kind = 'key'") == [("2099-01-02",)]
        assert throttle.client_key("203.0.113.1", "write").previous == key

    def test_a_first_day_has_no_previous_key(self):
        assert throttle.client_key("203.0.113.1", "write").previous is None


def test_the_visit_fallbacks_salt_is_gone_at_midnight_though_key_salts_stay(throttle_store, monkeypatch):
    """derived_salt salts the visit counter's fallback hashes, which AGENTS.md
    §8 promises can't be recomputed once their day ends — so it can't share
    the key salts' extra day."""
    TestAcrossMidnight._at(monkeypatch, 1, 12)
    throttle.client_key("203.0.113.1", "write")
    first = throttle.derived_salt("visits:2099-01-01")
    day_salt = _rows(throttle_store, "SELECT salt FROM salt_days WHERE kind = 'day'")[0][0]
    TestAcrossMidnight._at(monkeypatch, 2, 0, 0, 30)
    monkeypatch.setattr(throttle, "_last_forget", -1e9)
    throttle.forget_stale_salt()
    assert _rows(throttle_store, "SELECT COUNT(*) FROM salt_days WHERE kind = 'day'") == [(0,)]
    assert _rows(throttle_store, "SELECT date FROM salt_days WHERE kind = 'key'") == [("2099-01-01",)]  # still needed today
    assert throttle.derived_salt("visits:2099-01-01") != first
    import os

    for suffix in ("", "-wal"):
        if os.path.exists(throttle_store + suffix):
            with open(throttle_store + suffix, "rb") as fh:
                assert day_salt not in fh.read()


def test_a_worker_behind_midnight_never_brings_back_a_deleted_day_salt(throttle_store, monkeypatch):
    # The visit fallback's salt is deleted when its day ends; a worker that
    # read the clock just before midnight must not make that day's again.
    TestAcrossMidnight._at(monkeypatch, 1, 23)
    throttle.derived_salt("visits:2099-01-01")
    TestAcrossMidnight._at(monkeypatch, 2, 0)
    throttle.derived_salt("visits:2099-01-02")  # drops the 1st's
    throttle.use_path(throttle_store)  # the lagging worker: nothing cached
    TestAcrossMidnight._at(monkeypatch, 1, 23, 59, 59)
    assert throttle.derived_salt("visits:2099-01-01") is None
    assert _rows(throttle_store, "SELECT date FROM salt_days WHERE kind = 'day'") == [("2099-01-02",)]


async def test_maintenance_drops_stale_salts_without_any_traffic(throttle_store, monkeypatch):
    import asyncio

    TestAcrossMidnight._at(monkeypatch, 1, 12)
    throttle.derived_salt("visits:2099-01-01")
    TestAcrossMidnight._at(monkeypatch, 2, 0, 0, 30)
    monkeypatch.setattr(throttle, "_last_forget", -1e9)
    task = asyncio.create_task(throttle.run_maintenance())
    for _ in range(100):
        await asyncio.sleep(0.01)
        if _rows(throttle_store, "SELECT COUNT(*) FROM salt_days WHERE kind = 'day'") == [(0,)]:
            break
    task.cancel()
    assert _rows(throttle_store, "SELECT COUNT(*) FROM salt_days WHERE kind = 'day'") == [(0,)]


async def test_store_calls_do_not_queue_behind_the_default_executor():
    """Every limited request asks the store; on the default executor it
    would wait behind searches and PDF parses holding every thread."""
    import asyncio
    import threading
    import time

    release = threading.Event()
    loop = asyncio.get_running_loop()
    busy = [loop.run_in_executor(None, release.wait) for _ in range(64)]  # fill the default pool
    start = time.monotonic()
    name = await throttle.run(lambda: threading.current_thread().name)
    assert name.startswith("throttle") and time.monotonic() - start < 1
    release.set()
    await asyncio.gather(*busy)


def test_use_path_never_closes_a_connection_mid_use(throttle_store):
    """Closing a check_same_thread=False connection under another thread's
    query can crash the interpreter: use_path waits for it instead."""
    import threading
    import time

    started, finish = threading.Event(), threading.Event()
    outcome = {}

    def slow_user():
        with throttle._Txn() as conn:
            conn.execute("SELECT 1").fetchone()
            started.set()
            finish.wait(1)
            outcome["still_open"] = conn.execute("SELECT 1").fetchone() == (1,)

    worker = threading.Thread(target=slow_user)
    worker.start()
    started.wait()
    closer = threading.Thread(target=throttle.use_path, args=(throttle_store,))
    closer.start()
    time.sleep(0.1)
    assert closer.is_alive()  # waiting on the in-use connection
    finish.set()
    worker.join()
    closer.join()
    assert outcome["still_open"]
