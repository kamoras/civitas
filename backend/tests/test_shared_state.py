"""app/shared_state.py: api_cache rows one process writes and another reads."""

from datetime import datetime, timedelta

from app.shared_state import UNREADABLE, read_row, write_row


def test_a_written_row_reads_back_whatever_its_age(db_session):
    long_ago = datetime(2020, 1, 1)
    write_row(db_session, "t", "k", {"a": 1}, at=long_ago)
    db_session.commit()
    assert read_row("t", "k", db_session) == (long_ago, {"a": 1})


def test_a_rewrite_moves_the_time_and_the_value(db_session):
    write_row(db_session, "t", "k", {"a": 1}, at=datetime(2026, 1, 1))
    write_row(db_session, "t", "k", {"a": 2}, at=datetime(2026, 1, 1) + timedelta(hours=1))
    db_session.commit()
    assert read_row("t", "k", db_session) == (datetime(2026, 1, 1, 1), {"a": 2})


def test_an_empty_value_is_written_as_given(db_session):
    # Unlike api_cache_set, which keeps an older non-empty value over an
    # empty one: a marker's value is often empty.
    write_row(db_session, "t", "k", {"a": 1}, at=datetime(2026, 1, 1))
    write_row(db_session, "t", "k", {}, at=datetime(2026, 1, 2))
    db_session.commit()
    assert read_row("t", "k", db_session) == (datetime(2026, 1, 2), {})


def test_a_missing_row_is_none(db_session):
    assert read_row("t", "nope", db_session) is None


def test_an_unreadable_database_is_unreadable_not_missing(monkeypatch):
    def broken():
        raise RuntimeError("database is locked")

    monkeypatch.setattr("app.database.SessionLocal", broken)
    assert read_row("t", "k") is UNREADABLE


class TestPolledRow:
    """The one rule for a value another process writes: every consumer
    (explore_ranking, race_relevance) gets it from here."""

    @staticmethod
    def _polled(rows):
        from app.shared_state import PolledRow, decode_json_dict

        reads = []

        def reader(_db):
            reads.append(1)
            return rows[0]

        return PolledRow("t", "k", every_s=30, decode=decode_json_dict, reader=reader), reads

    def test_reads_once_per_interval(self):
        polled, reads = self._polled([(datetime(2026, 1, 1), {"a": 1})])
        for _ in range(5):
            assert polled.get() == {"a": 1}
        assert reads == [1]

    def test_a_replaced_row_is_picked_up_at_the_next_check(self):
        rows = [(datetime(2026, 1, 1), {"a": 1})]
        polled, _ = self._polled(rows)
        polled.get()
        rows[0] = (datetime(2026, 1, 2), '{"a": 2}')  # encoded twice, as api_cache_set writes
        assert polled.get() == {"a": 1}
        polled.expire()
        assert polled.get() == {"a": 2}

    def test_unreadable_keeps_the_value(self):
        rows = [(datetime(2026, 1, 1), {"a": 1})]
        polled, _ = self._polled(rows)
        polled.get()
        rows[0] = UNREADABLE
        polled.expire()
        assert polled.get() == {"a": 1}

    def test_an_undecodable_replacement_keeps_the_value_and_is_retried(self):
        rows = [(datetime(2026, 1, 1), {"a": 1})]
        polled, _ = self._polled(rows)
        polled.get()
        rows[0] = (datetime(2026, 1, 2), "not json")
        polled.expire()
        assert polled.get() == {"a": 1}
        rows[0] = (datetime(2026, 1, 2), {"a": 3})  # same stamp, readable now
        polled.expire()
        assert polled.get() == {"a": 3}

    def test_a_removed_row_is_none(self):
        rows = [(datetime(2026, 1, 1), {"a": 1})]
        polled, _ = self._polled(rows)
        polled.get()
        rows[0] = None
        polled.expire()
        assert polled.get() is None

    def test_a_first_read_that_meets_a_lock_is_retried_soon(self, monkeypatch):
        import time as time_module

        rows = [UNREADABLE]
        polled, reads = self._polled(rows)
        clock = [1000.0]
        monkeypatch.setattr(time_module, "monotonic", lambda: clock[0])
        assert polled.get() is None
        rows[0] = (datetime(2026, 1, 1), {"a": 1})
        clock[0] += 1.5  # well inside the 30s interval
        assert polled.get() == {"a": 1}
        assert len(reads) == 2

    def test_a_reset_during_a_read_is_not_undone(self):
        from app.shared_state import PolledRow, decode_json_dict

        polled = None

        def reader(_db):
            polled.reset()  # the writer's reset_cache(), mid-read
            return (datetime(2026, 1, 1), {"old": True})

        polled = PolledRow("t", "k", every_s=30, decode=decode_json_dict, reader=reader)
        assert polled.get() == {"old": True}  # this caller still gets what it read
        assert polled.current() is None  # but it isn't kept


def test_api_cache_set_survives_a_concurrent_first_write(db_session, monkeypatch):
    """Two API workers can both miss a key and both write it: the second
    must update, not fail on the primary key."""
    from app.models import ApiCache
    from app.pipeline import cache

    real_query = db_session.query

    class _Nothing:
        def filter(self, *a, **k):
            return self

        def first(self):
            return None

    # Both writers read "no row" before either writes.
    monkeypatch.setattr(db_session, "query", lambda *a, **k: _Nothing())
    cache.api_cache_set(db_session, "t", "k", {"a": 1})
    cache.api_cache_set(db_session, "t", "k", {"a": 2})
    cache.api_cache_set(db_session, "t", "k", {})  # empty never replaces non-empty
    monkeypatch.setattr(db_session, "query", real_query)
    row = db_session.get(ApiCache, ("t", "k"))
    assert row.data_json == '{"a": 2}'


async def test_a_request_path_cache_write_that_fails_is_only_logged(db_session, monkeypatch):
    """A cache write only saves later work: a failing one (the pipeline
    holding the write lock past the busy timeout) must not fail the request
    that already has its answer."""
    from app.pipeline import cache

    def locked(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(cache, "api_cache_set", locked)
    await cache.api_cache_set_async(db_session, "t", "k", {"a": 1})
    await cache.api_cache_set_many_async(db_session, "t", {"k": {"a": 1}})


def test_callers_during_the_first_read_wait_for_it(monkeypatch):
    """Before the first read answers there is no value to serve: concurrent
    callers wait for it instead of each falling back to the bundled value."""
    import threading
    import time

    from app.shared_state import PolledRow, decode_json_dict

    started = threading.Event()

    def slow_reader(_db):
        started.set()
        time.sleep(0.2)
        return (datetime(2026, 1, 1), {"a": 1})

    polled = PolledRow("t", "k", every_s=30, decode=decode_json_dict, reader=slow_reader)
    results = []
    first = threading.Thread(target=lambda: results.append(polled.get()))
    first.start()
    started.wait()
    results.append(polled.get())  # arrives mid-read
    first.join()
    assert results == [{"a": 1}, {"a": 1}]
