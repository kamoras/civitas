"""conftest's teardown joins the background threads a test started running
app code (_join_app_threads_started_since) — a threading.Timer's included,
whose app code is its .function, not a _target — and fails a test that
leaves a Timer still waiting to fire into the next test."""

import os
import pathlib
import subprocess
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]

_CASES = '''
import threading

from app.pipeline.fetch import district_pvi


def test_leaves_a_timer_pending(db_session):
    timer = threading.Timer(1.0, district_pvi._reset_caches)
    timer.daemon = True
    timer.start()


def test_joins_its_timer(db_session):
    timer = threading.Timer(0.01, district_pvi._reset_caches)
    timer.daemon = True
    timer.start()
    timer.join(5)


def test_a_library_timer_is_not_the_app_s(db_session):
    timer = threading.Timer(1.0, print)
    timer.daemon = True
    timer.start()
    timer.cancel()
'''


def test_a_timer_left_waiting_to_run_app_code_fails_the_test(tmp_path):
    cases = tmp_path / "test_timer_cases.py"
    cases.write_text(_CASES)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "tests.conftest",
         "-c", str(BACKEND / "pytest.ini"), "--rootdir", str(tmp_path), "-q", "-rE", str(cases)],
        cwd=BACKEND, env=dict(os.environ), capture_output=True, text=True, timeout=300,
    )
    out = result.stdout + result.stderr
    assert result.returncode != 0, out[-3000:]
    assert "timers still waiting to run app code at teardown" in out, out[-3000:]
    assert "ERROR at teardown of test_leaves_a_timer_pending" in out, out[-3000:]
    assert "3 passed, 1 error" in out, out[-3000:]
