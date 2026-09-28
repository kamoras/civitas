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
