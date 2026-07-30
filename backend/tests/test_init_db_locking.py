"""Tests for the cross-process lock that serialises init_db.

The backend runs `--workers 2` and each worker process runs its own
FastAPI lifespan, so two of them call `init_db` at the same moment. Every
step inside is check-then-act — `create_all` inspects `sqlite_master`
before CREATE TABLE, `_migrate_columns` inspects columns before ALTER,
`_ensure_indexes` inspects indexes before CREATE INDEX — so both can
observe "absent" and both issue the DDL. Measured before this lock
existed: four processes against a fresh database, and the losers died
with "table senators already exists", aborting partway through and
leaving migrations and the keyword index unapplied in that worker while
the other reported a clean start.
"""

import os
import subprocess
import sys
import threading
import time

import pytest

from app.database import _init_lock, _init_lock_path

_REPO_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestLockPath:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            # Four slashes is an absolute path — the production form.
            ("sqlite:////data/civitas.db", "/data/civitas.db.init.lock"),
            # Three is relative, the local-development form.
            ("sqlite:///data/civitas.db", "data/civitas.db.init.lock"),
            # Nothing to serialise: an in-memory database is private to
            # the process that opened it.
            ("sqlite:///:memory:", None),
            # Other backends handle concurrent DDL themselves.
            ("postgresql://user@host/db", None),
        ],
    )
    def test_derives_from_the_database_path(self, url, expected, monkeypatch):
        from app import database

        monkeypatch.setattr(database.settings, "DATABASE_URL", url)
        assert _init_lock_path() == expected

    def test_two_databases_do_not_share_a_lock(self, monkeypatch):
        # Otherwise two stacks on one host block each other's startup for
        # no reason.
        from app import database

        monkeypatch.setattr(database.settings, "DATABASE_URL", "sqlite:////a/one.db")
        first = _init_lock_path()
        monkeypatch.setattr(database.settings, "DATABASE_URL", "sqlite:////a/two.db")
        assert _init_lock_path() != first


class TestLockBehaviour:
    def test_in_memory_url_is_a_passthrough(self, monkeypatch):
        from app import database

        monkeypatch.setattr(database.settings, "DATABASE_URL", "sqlite:///:memory:")
        entered = False
        with _init_lock():
            entered = True
        assert entered

    def test_concurrent_entrants_are_serialised(self, tmp_path, monkeypatch):
        from app import database

        monkeypatch.setattr(
            database.settings, "DATABASE_URL", f"sqlite:///{tmp_path}/c.db"
        )
        overlapping = []
        inside = []

        def _worker():
            with _init_lock():
                inside.append(1)
                overlapping.append(len(inside))
                time.sleep(0.05)
                inside.pop()

        threads = [threading.Thread(target=_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(overlapping) == 4
        # Never two holders at once — which is the entire point.
        assert max(overlapping) == 1

    def test_an_unusable_lock_file_does_not_fail_startup(self, tmp_path, monkeypatch):
        # A read-only mount or a permissions problem must degrade to the
        # previous behaviour (run unlocked), never take the app down: the
        # lock is a defence against duplicate log noise and a half-applied
        # migration, not a correctness precondition for booting.
        from app import database

        unwritable = tmp_path / "nope"
        unwritable.mkdir()
        unwritable.chmod(0o500)
        monkeypatch.setattr(
            database.settings, "DATABASE_URL", f"sqlite:///{unwritable}/c.db"
        )
        try:
            entered = False
            with _init_lock():
                entered = True
            assert entered
        finally:
            unwritable.chmod(0o700)


class TestConcurrentInitDb:
    """The real thing: separate OS processes, as uvicorn --workers runs them.

    Deliberately NOT marked slow, even though it spawns interpreters: CI's
    fast job runs `-m "not slow"`, and a regression test for the failure
    this lock exists to prevent is worth little if the job that gates every
    PR skips it. The subprocesses import `app.database` and its migration
    helpers, not the sentence-transformer stack, so the cost is a few
    seconds rather than the model load `slow` is reserved for.

    Threads are not a faithful stand-in here — `flock` is advisory and
    per-open-file-description, and the failure being prevented is between
    OS processes.
    """

    WORKER = (
        "import os, sys\n"
        "sys.path.insert(0, {backend!r})\n"
        "os.environ['DATABASE_URL'] = 'sqlite:///' + sys.argv[1] + '/c.db'\n"
        "from app.database import init_db\n"
        "init_db()\n"
        "print('OK')\n"
    )

    def _run(self, tmp_path, n):
        script = self.WORKER.format(backend=_REPO_BACKEND)
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", script, str(tmp_path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for _ in range(n)
        ]
        return [(p.wait(timeout=180), *p.communicate()) for p in procs]

    def test_a_fresh_database_survives_four_simultaneous_workers(self, tmp_path):
        for code, out, err in self._run(tmp_path, 4):
            assert code == 0, err
            assert "OK" in out

    def test_a_redeploy_against_an_existing_database_also_survives(self, tmp_path):
        self._run(tmp_path, 1)
        for code, out, err in self._run(tmp_path, 4):
            assert code == 0, err
            assert "OK" in out
