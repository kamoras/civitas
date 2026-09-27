"""api/throttle.py: the limits every API worker process shares."""

import multiprocessing
import sqlite3
import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.api import throttle
from app.database import VisitsBase


def _file_store(path: str):
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 30})
    VisitsBase.metadata.create_all(bind=engine)
    return engine


def _worker(path: str, calls: int, start, results) -> None:
    """One API worker process: its own interpreter and module state, the
    same database file."""
    throttle._session_factory = throttle.make_session_factory(f"sqlite:///{path}", busy_timeout_s=30)
    start.wait()
    allowed = sum(throttle.hit("write", "k", limit=20, period=3600).allowed for _ in range(calls))
    claimed = sum(throttle.claim("pulse", "k:1", period=3600) for _ in range(calls))
    results.put((allowed, claimed))


def test_a_limit_holds_across_processes(tmp_path):
    # The reason this module exists: with per-process state, two workers
    # would each allow 20 (40 total) and each grant the claim once.
    path = str(tmp_path / "visits.db")
    _file_store(path).dispose()
    ctx = multiprocessing.get_context("spawn")
    start, results = ctx.Event(), ctx.Queue()
    procs = [ctx.Process(target=_worker, args=(path, 15, start, results)) for _ in range(2)]
    for p in procs:
        p.start()
    start.set()
    totals = [results.get(timeout=60) for _ in procs]
    for p in procs:
        p.join(timeout=60)

    assert sum(allowed for allowed, _ in totals) == 20
    assert sum(claimed for _, claimed in totals) == 1


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

    def test_the_previous_window_still_counts_across_a_boundary(self, throttle_store, monkeypatch):
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

    def test_reset_at_is_the_end_of_the_window(self, monkeypatch):
        monkeypatch.setattr(throttle.time, "time", lambda: 125.0)
        assert throttle.hit("b", "k", limit=3, period=60).reset_at == 180

    def test_expired_windows_are_purged(self, throttle_store, monkeypatch):
        at = [0.0]
        monkeypatch.setattr(throttle.time, "time", lambda: at[0])
        monkeypatch.setattr(throttle, "_last_purge", -1e9)
        throttle.hit("b", "old", limit=3, period=60)
        at[0] = 600.0
        throttle.hit("b", "new", limit=3, period=60)
        with throttle_store.connect() as conn:
            keys = [r[0] for r in conn.execute(text("SELECT key FROM throttle_windows"))]
        assert keys == ["new"]


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
        with throttle_store.connect() as conn:
            keys = [r[0] for r in conn.execute(text("SELECT key FROM throttle_claims"))]
        assert keys == ["new"]

    def test_the_purge_runs_at_most_once_a_minute(self, throttle_store, monkeypatch):
        at = [1000.0]
        monkeypatch.setattr(throttle.time, "time", lambda: at[0])
        monkeypatch.setattr(throttle, "_last_purge", 1000.0)
        throttle.claim("b", "old", period=1)
        at[0] += 30
        throttle.claim("b", "new", period=1)
        with throttle_store.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM throttle_claims")).scalar() == 2


class TestFailsOpen:
    """A locked or missing store lets requests through rather than turning
    every limited endpoint into an outage."""

    @pytest.fixture(autouse=True)
    def _missing_tables(self, monkeypatch):
        engine = create_engine("sqlite:///:memory:")
        monkeypatch.setattr(throttle, "_session_factory", sessionmaker(bind=engine))
        yield
        engine.dispose()

    def test_hit(self):
        decision = throttle.hit("b", "k", limit=3, period=60)
        assert decision.allowed and decision.remaining == 3

    def test_claim(self):
        assert throttle.claim("b", "k", period=30)

    def test_release(self):
        throttle.release("b", "k")  # logs, doesn't raise


def test_a_held_write_lock_is_waited_on_briefly_not_for_the_full_timeout(tmp_path, monkeypatch):
    path = str(tmp_path / "visits.db")
    _file_store(path).dispose()
    monkeypatch.setattr(throttle, "_session_factory", throttle.make_session_factory(f"sqlite:///{path}", 0.2))
    blocker = sqlite3.connect(path)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        assert throttle.hit("b", "k", limit=3, period=60).allowed  # failed open
        assert time.monotonic() - started < 5
    finally:
        blocker.rollback()
        blocker.close()
        throttle._session_factory.kw["bind"].dispose()


def test_its_short_timeout_never_reaches_the_visits_engine(tmp_path, monkeypatch):
    # An earlier version set the short timeout per connection and
    # "restored" it after commit — by then on a different pooled
    # connection, leaving 2 s on the one the visit consumer next used.
    from app.database import _sqlite_connect_args_for

    path = str(tmp_path / "visits.db")
    _file_store(path).dispose()
    factory = throttle.make_session_factory(f"sqlite:///{path}")
    monkeypatch.setattr(throttle, "_session_factory", factory)
    for _ in range(5):
        throttle.hit("b", "k", limit=100, period=60)
    with factory.kw["bind"].connect() as conn:
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == int(throttle._BUSY_TIMEOUT_S * 1000)
    visits_engine = create_engine(f"sqlite:///{path}", connect_args=_sqlite_connect_args_for(f"sqlite:///{path}"))
    with visits_engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30_000
    visits_engine.dispose()
    factory.kw["bind"].dispose()


def test_a_store_that_cannot_be_opened_fails_open(monkeypatch):
    def broken():
        raise throttle.SQLAlchemyError("unable to open database file")

    monkeypatch.setattr(throttle, "_session_factory", broken)
    assert throttle.hit("b", "k", limit=3, period=60).allowed
    assert throttle.claim("b", "k", period=30)
    throttle.release("b", "k")
