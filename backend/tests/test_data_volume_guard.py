"""The test run never writes the data volume (conftest._data_volume_untouched):
a write that reaches /data is refused, as a read-only volume would refuse it,
and fails the test that made it."""

import sqlite3

import pytest

from tests import conftest


@pytest.mark.parametrize("write", [
    lambda: open("/data/civitas-guard-probe.json", "w"),
    lambda: sqlite3.connect("/data/civitas-guard-probe.db"),
])
def test_a_write_to_the_data_volume_is_refused_and_recorded(write):
    before = len(conftest._data_writes)
    with pytest.raises(PermissionError):
        write()
    assert conftest._data_writes[before:]
    del conftest._data_writes[before:]  # this test's own, on purpose


def test_reads_and_other_directories_are_left_alone(tmp_path):
    before = len(conftest._data_writes)
    (tmp_path / "x.json").write_text("{}")
    sqlite3.connect(str(tmp_path / "x.db")).close()
    assert conftest._data_writes[before:] == []


def test_runtime_paths_point_into_the_test_s_tmp_path(tmp_path):
    from app.atomic_write import runtime_data_path
    from app.pipeline.analyze.signal_overlap import SIGNAL_OVERLAP
    from app.pipeline.fetch import senate_classes
    from app.shared_state import record_path

    for path in (runtime_data_path("x.json"), record_path("scheduler_heartbeat.json"),
                 str(SIGNAL_OVERLAP.live_path), senate_classes._PERSISTENT_PATH):
        assert path.startswith(str(tmp_path)), path
