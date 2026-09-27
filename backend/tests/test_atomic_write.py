"""app.atomic_write: a file is replaced whole, never seen partial."""

import json
import os

import pytest

from app.atomic_write import update_json_file, update_shared_file, write_text_atomic


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


def test_a_date_that_loses_a_lock_race_is_dropped_not_misfiled(workdir, monkeypatch):
    """Never written to the fallback path (where the next read wouldn't
    look), never an error for the sync or the crawl; the file and the cache
    agree, and the next save is unaffected."""
    import fcntl

    from app import atomic_write
    from app.pipeline.fetch import state_election_dates as dates

    primary, fallback = workdir / "dates.json", workdir / "fallback" / "dates.json"
    monkeypatch.setattr(dates, "_PATHS", (str(primary), str(fallback)))
    monkeypatch.setattr(dates, "_cache", None)
    monkeypatch.setattr(atomic_write, "DATA_FILE_WAIT_S", 0.05)
    with open(f"{primary}.lock", "a") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        dates.save("MN", 2026, {"primary": "2026-08-11"})
        assert dates.save("MN", 2026, {"primary": "2026-08-12"}) is False  # the caller hears it
    assert dates.primary_date("MN", 2026) is None
    assert not primary.exists() and not fallback.exists()

    dates.save("WI", 2026, {"primary": "2026-08-11"})  # the lock is free again
    assert set(json.loads(primary.read_text())) == {"2026-WI"}
    assert dates.primary_date("WI", 2026) == "2026-08-11"


def test_the_calendar_and_its_read_marker_land_together(workdir, monkeypatch):
    """One update: the marker never vouches for a state whose dates weren't
    recorded, and a lost lock race records neither."""
    import fcntl

    from app import atomic_write
    from app.pipeline.fetch import state_election_dates as dates

    primary = workdir / "dates.json"
    monkeypatch.setattr(dates, "_PATHS", (str(primary),))
    monkeypatch.setattr(dates, "_cache", None)
    monkeypatch.setattr(atomic_write, "DATA_FILE_WAIT_S", 0.05)
    calendar = {"OH": {"primary": "2028-03-14", "senate": True}, "MN": {"primary": "2028-08-08"}}
    with open(f"{primary}.lock", "a") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        assert dates.save_calendar(2028, calendar, "2027-12-01") is False
    assert dates.senate_election_known("OH", 2028) is None  # not read, rather than "no race"
    assert dates.save_calendar(2028, calendar, "2027-12-01") is True
    assert dates.senate_election_known("OH", 2028) is True
    assert dates.senate_election_known("MN", 2028) is False
    assert dates.primary_date("MN", 2028) == "2028-08-08"



def test_a_shared_file_is_written_where_reads_find_it(workdir, monkeypatch):
    """The first path that exists — never a later one on a write failure,
    and never the cache alone when nothing can be written."""
    primary, fallback = workdir / "p" / "dates.json", workdir / "f" / "dates.json"
    fallback.parent.mkdir()
    fallback.write_text('{"old": 1}')  # only the fallback exists: reads find it first
    published = []
    assert update_shared_file(
        [str(primary), str(fallback)], lambda known: {**known, "new": 1},
        missing=dict, publish=published.append, what="test",
    )
    assert json.loads(fallback.read_text()) == {"old": 1, "new": 1} and not primary.exists()

    import errno

    from app import atomic_write

    def disk_full(*_args):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(atomic_write, "write_text_atomic", disk_full)  # the file reads find can't be written
    published.clear()
    assert not update_shared_file(
        [str(primary), str(fallback)], lambda known: {**known, "newer": 1},
        missing=dict, publish=published.append, what="test",
    )
    assert not primary.exists() and published == []  # not misfiled, not cache-only


def test_a_senate_race_the_calendar_stops_listing_is_kept(workdir, monkeypatch):
    """Merged, deliberately: a calendar can stop listing a real race (a
    relabelled election-day special), and retracting it deletes the race."""
    from app.pipeline.fetch import state_election_dates as dates

    monkeypatch.setattr(dates, "_PATHS", (str(workdir / "dates.json"),))
    monkeypatch.setattr(dates, "_cache", None)
    dates.save_calendar(2028, {"OH": {"primary": "2028-03-14", "senate": "2028-11-07"}}, "2027-12-01")
    dates.save_calendar(2028, {"OH": {"primary": "2028-03-14"}}, "2027-12-08")
    assert dates.senate_election_known("OH", 2028) is True


def test_a_data_file_goes_where_it_can_be_written(workdir, monkeypatch):
    """Before it exists anywhere: the first writable data directory, else
    the last path's, created — never a directory the process can't write."""
    from app.atomic_write import shared_file_path

    volume, checkout = workdir / "volume", workdir / "checkout" / "data"
    volume.mkdir()
    assert shared_file_path([str(volume / "x.json"), str(checkout / "x.json")]) == str(volume / "x.json")
    monkeypatch.setattr(os, "access", lambda path, mode: False)  # the volume isn't writable
    assert shared_file_path([str(volume / "x.json"), str(checkout / "x.json")]) == str(checkout / "x.json")
    assert checkout.is_dir()


def test_a_calendar_missing_a_page_is_not_a_calendar(monkeypatch):
    """Recorded as read, a partial calendar would vouch that the states on
    its missing pages hold no Senate race."""
    import asyncio

    from app.pipeline.fetch import fec
    from app.pipeline.fetch import state_election_dates as dates

    pages = iter([
        {"results": [{"election_state": "OH", "election_date": "2028-03-14", "office_sought": "S",
                      "election_type_full": "Primary Election"}], "pagination": {"pages": 2}},
        None,  # page 2 failed after its retries
    ])

    async def fetch(_client, _url):
        return next(pages)

    monkeypatch.setattr(fec, "_fetch_with_retry", fetch)
    assert asyncio.run(dates.fetch_fec_calendar(None, 2028)) == {}
