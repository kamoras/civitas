"""app.atomic_write: a file is replaced whole, never seen partial."""

import json
import os

import pytest

from app.atomic_write import update_json_file, write_text_atomic


@pytest.fixture()
def workdir(tmp_path):
    """A directory of its own (conftest seeds tmp_path with bundled files)."""
    own = tmp_path / "atomic"
    own.mkdir()
    return own


def test_replaces_the_file_whole(workdir):
    target = workdir / "dates.json"
    target.write_text('{"old": true}')
    write_text_atomic(target, '{"new": true}')
    assert target.read_text() == '{"new": true}'
    assert os.listdir(workdir) == ["dates.json"]  # no temp file left beside it


def test_a_failed_write_leaves_the_old_file_and_no_temp(workdir, monkeypatch):
    target = workdir / "dates.json"
    target.write_text('{"old": true}')

    def fail(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        write_text_atomic(target, '{"new": true}')
    assert target.read_text() == '{"old": true}'
    assert os.listdir(workdir) == ["dates.json"]


def test_the_target_is_never_truncated_in_place(workdir, monkeypatch):
    """What a concurrent reader sees mid-write: the old file, whole."""
    target = workdir / "dates.json"
    target.write_text('{"old": true}')
    seen = []
    real_fsync = os.fsync

    def fsync(fd):
        seen.append(target.read_text())  # the new text is written, not yet renamed in
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    write_text_atomic(target, '{"new": true}')
    assert seen == ['{"old": true}']


def test_keeps_the_files_mode(workdir):
    """mkstemp's 0600 must not become the data file's mode."""
    target = workdir / "dates.json"
    target.write_text("{}")
    target.chmod(0o644)
    write_text_atomic(target, '{"new": true}')
    assert oct(target.stat().st_mode & 0o777) == "0o644"
    old_umask = os.umask(0o077)  # a new file gets the umask's mode, as open() would give it
    try:
        fresh = workdir / "fresh.json"
        write_text_atomic(fresh, "{}")
    finally:
        os.umask(old_umask)
    assert oct(fresh.stat().st_mode & 0o777) == "0o600"


def test_concurrent_updates_keep_every_change(workdir):
    """Two writers' read-modify-writes (the crawl and a sync, in threads or
    processes) interleave without losing either's key."""
    import threading
    import time

    target = workdir / "dates.json"
    target.write_text("{}")
    in_first = threading.Event()

    def slow_add(known):
        known["first"] = 1
        in_first.set()
        time.sleep(0.2)  # the second writer arrives mid-update
        return known

    first = threading.Thread(target=update_json_file, args=(target, slow_add))
    first.start()
    in_first.wait()
    update_json_file(target, lambda known: {**known, "second": 2})
    first.join()
    assert json.loads(target.read_text()) == {"first": 1, "second": 2}


def test_a_missing_file_starts_from_what_the_caller_knows(workdir):
    target = workdir / "sub" / "dates.json"
    written = update_json_file(target, lambda known: {**known, "new": 1}, missing=lambda: {"bundled": 1})
    assert written == {"bundled": 1, "new": 1} == json.loads(target.read_text())


@pytest.mark.parametrize("content", ["[]", '"text"', "not json"])
def test_a_file_not_holding_an_object_starts_from_what_the_caller_knows(workdir, content):
    target = workdir / "dates.json"
    target.write_text(content)
    assert update_json_file(target, lambda known: {**known, "new": 1}, missing=lambda: {"seed": 1}) == {
        "seed": 1, "new": 1,
    }


def test_a_writer_holding_the_lock_too_long_is_a_lock_timeout(workdir, monkeypatch):
    """Not an OSError, which callers take as "try the next path" — that
    would write the change where the next read won't look. An event loop
    isn't stalled for long either."""
    import fcntl

    from app import atomic_write

    monkeypatch.setattr(atomic_write, "LOCK_WAIT_S", 0.1)
    target = workdir / "dates.json"
    with open(f"{target}.lock", "a") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        with pytest.raises(atomic_write.LockTimeout, match="stayed locked"):
            update_json_file(target, lambda known: known)
    assert not issubclass(atomic_write.LockTimeout, OSError)


def test_the_written_copy_is_published_before_the_lock_is_let_go(workdir):
    """So two writers in one process publish their copies (a module cache)
    in the order they wrote them."""
    import fcntl

    target = workdir / "dates.json"
    locked_while_published = []

    def publish(_data):
        with open(f"{target}.lock", "a") as probe:
            try:
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked_while_published.append(False)
            except BlockingIOError:
                locked_while_published.append(True)

    update_json_file(target, lambda known: {**known, "a": 1}, written=publish)
    assert locked_while_published == [True]


def test_a_change_that_loses_a_lock_race_is_carried_by_the_next_write(workdir, monkeypatch):
    """Never written to the fallback path, never an error for the sync or
    the crawl, and not dropped when the next save publishes the file."""
    import fcntl

    from app import atomic_write
    from app.pipeline.fetch import state_election_dates as dates

    primary, fallback = workdir / "dates.json", workdir / "fallback" / "dates.json"
    monkeypatch.setattr(dates, "_PATHS", (str(primary), str(fallback)))
    monkeypatch.setattr(dates, "_cache", None)
    monkeypatch.setattr(dates._FILE, "_pending", [])
    monkeypatch.setattr(atomic_write, "LOCK_WAIT_S", 0.05)
    monkeypatch.setattr(atomic_write, "_contended_until", {})
    with open(f"{primary}.lock", "a") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        dates.save("MN", 2026, {"primary": "2026-08-11"})
    assert dates.primary_date("MN", 2026) == "2026-08-11"  # kept in memory
    assert not primary.exists() and not fallback.exists()

    dates.save("WI", 2026, {"primary": "2026-08-11"})  # the lock is free again
    written = json.loads(primary.read_text())
    assert set(written) == {"2026-MN", "2026-WI"}  # carried, not dropped
    assert dates.primary_date("MN", 2026) == "2026-08-11"


def test_a_contended_file_is_not_waited_on_again_for_a_while(workdir, monkeypatch):
    """A loop of saves behind one stuck writer mustn't wait LOCK_WAIT_S each."""
    import fcntl
    import time

    from app import atomic_write

    monkeypatch.setattr(atomic_write, "LOCK_WAIT_S", 0.3)
    monkeypatch.setattr(atomic_write, "_contended_until", {})
    target = workdir / "dates.json"
    with open(f"{target}.lock", "a") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        with pytest.raises(atomic_write.LockTimeout):
            update_json_file(target, lambda known: known)
        started = time.monotonic()
        with pytest.raises(atomic_write.LockTimeout):
            update_json_file(target, lambda known: known)
        assert time.monotonic() - started < 0.1
    update_json_file(target, lambda known: {**known, "free": 1})  # one try succeeds once it's free
    assert json.loads(target.read_text()) == {"free": 1}
