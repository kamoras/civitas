"""api/throttle.py: the limits every API worker process shares."""

import multiprocessing
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
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 30})
    throttle._session_factory = sessionmaker(bind=engine)
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
        monkeypatch.setattr(throttle, "_PURGE_EVERY", 1)
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

    def test_expired_claims_are_purged(self, throttle_store, monkeypatch):
        at = [0.0]
        monkeypatch.setattr(throttle.time, "time", lambda: at[0])
        monkeypatch.setattr(throttle, "_PURGE_EVERY", 1)
        throttle.claim("b", "old", period=30)
        at[0] = 100.0
        throttle.claim("b", "new", period=30)
        with throttle_store.connect() as conn:
            keys = [r[0] for r in conn.execute(text("SELECT key FROM throttle_claims"))]
        assert keys == ["new"]


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
    engine = _file_store(str(tmp_path / "visits.db"))
    monkeypatch.setattr(throttle, "_session_factory", sessionmaker(bind=engine))
    monkeypatch.setattr(throttle, "_BUSY_TIMEOUT_MS", 200)
    blocker = engine.raw_connection()
    try:
        blocker.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        assert throttle.hit("b", "k", limit=3, period=60).allowed  # failed open
        assert time.monotonic() - started < 5
    finally:
        blocker.rollback()
        blocker.close()
        engine.dispose()
