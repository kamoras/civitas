"""The test run never touches the data volume (conftest._data_volume_untouched):
a write that reaches /data is refused, as a read-only volume would refuse it,
a read is refused as a host without the volume would refuse it, and either
fails the test that made it — and the environment the run inherits never
points the app at the site's database."""

import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys

import pytest

from tests import conftest

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def _refused(action, error, log):
    before = len(log)
    with pytest.raises(error):
        action()
    hits = log[before:]
    del log[before:]  # this test's own, on purpose
    return hits


@pytest.mark.parametrize("write", [
    lambda: open("/data/civitas-guard-probe.json", "w"),
    lambda: sqlite3.connect("/data/civitas-guard-probe.db"),
    lambda: open("//data/civitas-guard-probe.json", "w"),
    lambda: open("/tmp/../data/civitas-guard-probe.json", "w"),
    lambda: os.makedirs("/data/civitas-guard-probe", exist_ok=False),
    lambda: os.chown("/data/civitas-guard-probe.json", -1, -1),
    lambda: os.mkfifo("/data/civitas-guard-probe.fifo"),
    lambda: os.mknod("/data/civitas-guard-probe.node"),
])
def test_a_write_to_the_data_volume_is_refused_and_recorded(write):
    assert _refused(write, PermissionError, conftest._data_writes)


def test_making_a_data_directory_counts_whether_or_not_it_exists():
    """makedirs(exist_ok=True) calls mkdir even for a directory already
    there: recorded the same on a host with the volume and one without."""
    before = len(conftest._data_writes)
    try:
        os.makedirs("/data", exist_ok=True)
    except PermissionError:
        pass  # no /data on this host: makedirs re-raises the refusal
    assert conftest._data_writes[before:]
    del conftest._data_writes[before:]


def test_a_symlink_into_the_data_volume_is_followed(tmp_path):
    link = tmp_path / "volume"
    link.symlink_to("/data")
    assert _refused(lambda: open(link / "civitas-guard-probe.json", "w"), PermissionError, conftest._data_writes)


@pytest.mark.parametrize("read", [
    lambda: open("/data/senate_classes.json"),
    lambda: sqlite3.connect("file:/data/vectors.db?mode=ro", uri=True),
    lambda: os.listdir("/data"),
    lambda: shutil.copyfile("/data/senate_classes.json", "/tmp/civitas-guard-probe-copy.json"),
])
def test_a_read_of_the_data_volume_is_refused_as_absent_and_recorded(read):
    """Refused as FileNotFoundError whatever the host holds there, so a test
    sees what it would see on a host with no volume — and fails for it."""
    hits = _refused(read, FileNotFoundError, conftest._data_reads)
    assert hits and not any(h.startswith("sqlite3.connect") for h in conftest._data_writes)


def test_reads_and_other_directories_are_left_alone(tmp_path):
    writes, reads = len(conftest._data_writes), len(conftest._data_reads)
    (tmp_path / "x.json").write_text("{}")
    (tmp_path / "x.json").read_text()
    sqlite3.connect(str(tmp_path / "x.db")).close()
    sqlite3.connect(":memory:").close()
    os.listdir(tmp_path)
    (tmp_path / "datafile").mkdir()  # a name that starts with "data", not /data
    assert conftest._data_writes[writes:] == [] and conftest._data_reads[reads:] == []


def test_runtime_paths_point_into_the_test_s_tmp_path(tmp_path):
    from app.atomic_write import runtime_data_path
    from app.pipeline.analyze.signal_overlap import SIGNAL_OVERLAP
    from app.shared_state import record_path

    for path in (runtime_data_path("x.json"), record_path("scheduler_heartbeat.json"),
                 str(SIGNAL_OVERLAP.live_path)):
        assert path.startswith(str(tmp_path)), path


def test_every_module_constant_naming_a_data_file_points_into_tmp_path(tmp_path):
    """The sweep in redirect_data_volume: no loaded app module is left
    holding a path on /data — so a test reads the bundled fallback, not
    whatever the host's volume holds."""
    left = []
    for name, module in list(sys.modules.items()):
        if module is None or not (name == "app" or name.startswith("app.")):
            continue
        for attr, value in vars(module).items():
            values = value if isinstance(value, tuple) else (value,)
            for v in values:
                if isinstance(v, (str, pathlib.PurePath)) and conftest._data_literal(v):
                    left.append(f"{name}.{attr} = {v}")
    assert left == []
    from app.pipeline.fetch import district_pvi, senate_classes, state_candidate_sources

    for path in (district_pvi._PVI_PATH, senate_classes._PERSISTENT_PATH, state_candidate_sources._VOLUME_PATH):
        assert str(path).startswith(str(tmp_path)), path


def _first_import(monkeypatch, name):
    """Import `name` afresh, as a test that is the first to import it
    would; the module already loaded is put back at teardown."""
    import importlib

    parent, _, child = name.rpartition(".")
    monkeypatch.setattr(sys.modules[parent], child, getattr(sys.modules[parent], child))
    monkeypatch.delitem(sys.modules, name)
    return importlib.import_module(name)


def test_a_module_first_imported_inside_a_test_is_redirected(tmp_path, monkeypatch):
    """conftest._RedirectOnImport: a module the run has not loaded yet
    (app.pipeline.vector_store, imported when a pipeline run starts) gets
    the test's redirect as it loads, not the /data path it names."""
    module = _first_import(monkeypatch, "app.pipeline.fetch.senate_classes")
    assert module._PERSISTENT_PATH == str(tmp_path / "data-volume" / "senate_classes.json")


def test_a_redirect_made_on_import_is_undone_with_its_monkeypatch(tmp_path, monkeypatch):
    """...and taken off with the monkeypatch that made it, as is a clock
    the module bound by name while a test had replaced it — not left
    frozen for every later test."""
    from app import time_utils

    real = time_utils.utcnow
    inner = pytest.MonkeyPatch()
    conftest.redirect_data_volume(inner, tmp_path / "inner")

    def frozen():
        return None

    inner.setattr(time_utils, "utcnow", frozen)
    module = _first_import(monkeypatch, "app.pipeline.fetch.senate_classes")
    assert module.utcnow is frozen
    assert module._PERSISTENT_PATH == str(tmp_path / "inner" / "senate_classes.json")
    inner.undo()
    assert module.utcnow is real
    assert module._PERSISTENT_PATH == str(tmp_path / "data-volume" / "senate_classes.json")


_SITE_ENV = {
    "DATABASE_URL": "sqlite:////data/civitas.db", "VECTOR_DB_PATH": "/data/vectors.db",
    "CIVITAS_RAM_DIR": "/dev/shm", "THROTTLE_DB_PATH": "/dev/shm/civitas_throttle.db",
}


def test_a_run_started_with_the_site_s_environment_uses_its_own():
    """The documented container run (docker compose run … pytest) inherits
    docker-compose.yml's DATABASE_URL=sqlite:////data/civitas.db with the
    live volume mounted: the run still uses its own database, vector store
    and RAM directory."""
    check = (
        "import os, tests.conftest\n"
        "from app.api import throttle\n"
        "from app.config import settings\n"
        "from app.database import VISITS_DATABASE_URL, engine\n"
        "values = (settings.DATABASE_URL, str(engine.url), VISITS_DATABASE_URL, os.environ['VECTOR_DB_PATH'],\n"
        "          throttle.RAM_DIR, throttle._path)\n"
        "bad = [v for v in values if '/data' in v or '/dev/shm' in v]\n"
        "assert not bad, bad\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", check], cwd=BACKEND, env=dict(os.environ, **_SITE_ENV),
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]


def test_the_guard_s_own_tests_pass_under_the_site_s_environment():
    """...and a pytest run started that way passes without touching /data
    (this module, less this test and the one above)."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", __file__,
         "-k", "not site_s_environment"],
        cwd=BACKEND, env=dict(os.environ, **_SITE_ENV), capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
