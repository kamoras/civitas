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
