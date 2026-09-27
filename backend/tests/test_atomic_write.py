"""app.atomic_write: a file is replaced whole, never seen partial."""

import os

import pytest

from app.atomic_write import write_text_atomic


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
