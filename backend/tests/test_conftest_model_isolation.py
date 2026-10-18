"""A model a slow test loaded never reaches a fast test that runs after it
in the same process (conftest's _slow_test_running). vector_store keeps the
loaded model in a module global, so before 2026-10-17 a fast test run
after a slow one used the real model: the Action Center candidate-pool
tests failed after test_broadcast_kicker's real-model test and passed
alone. Run in a subprocess, without xdist, so the order is fixed."""

import os
import pathlib
import subprocess
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]

_CASES = '''
import pytest

from app.pipeline import vector_store


@pytest.mark.slow
def test_a_slow_test_leaves_a_loaded_model_behind():
    # What get_similarity_model() leaves in the module after a real load.
    vector_store._model = vector_store._similarity_model = object()


def test_a_later_fast_test_sees_no_model():
    with pytest.raises(OSError, match="fast tests load no embedding model"):
        vector_store.get_similarity_model()
    with pytest.raises(OSError, match="fast tests load no embedding model"):
        vector_store.get_embedding_model()


@pytest.mark.slow
def test_a_later_slow_test_keeps_reusing_it():
    assert vector_store._similarity_model is not None
'''


def test_a_fast_test_never_sees_a_model_a_slow_test_loaded(tmp_path):
    cases = tmp_path / "test_model_cases.py"
    cases.write_text(_CASES)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:xdist", "-p", "tests.conftest",
         "-c", str(BACKEND / "pytest.ini"), "--rootdir", str(tmp_path), "-q", str(cases),
         "--basetemp", str(tmp_path / "basetemp")],
        cwd=BACKEND, env=dict(os.environ), capture_output=True, text=True, timeout=300,
    )
    out = result.stdout + result.stderr
    assert result.returncode == 0, out[-3000:]
    assert "3 passed" in out, out[-3000:]
