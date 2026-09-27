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
    """Not an OSError, which a caller could take as "try the next path"."""
    import fcntl

    from app import atomic_write

    monkeypatch.setattr(atomic_write, "LOCK_WAIT_S", 0.1)
    target = workdir / "dates.json"
    with open(f"{target}.lock", "a") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        with pytest.raises(atomic_write.LockTimeout, match="stayed locked"):
            update_json_file(target, lambda known: known)
    assert not issubclass(atomic_write.LockTimeout, OSError)


def test_a_killed_writers_temp_file_is_swept_up(workdir):
    """Left behind by a process killed mid-write; not one still in flight."""
    import time

    target = workdir / "dates.json"
    old, fresh = workdir / ".dates.json.dead.tmp", workdir / ".dates.json.live.tmp"
    old.write_text("{")
    fresh.write_text("{")
    an_hour_ago = time.time() - 2 * 3600
    os.utime(old, (an_hour_ago, an_hour_ago))
    write_text_atomic(target, "{}")
    assert not old.exists() and fresh.exists()
