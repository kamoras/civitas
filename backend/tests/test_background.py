"""app.background: the registry the admin data reset reads to see every
database writer."""

import threading

import pytest

from app.background import running_writers, start_writer, writing


def test_a_writer_thread_is_registered_from_start_until_it_returns():
    release = threading.Event()
    thread = start_writer(release.wait, name="nightly-pipeline")
    assert running_writers() == ["nightly-pipeline"]
    release.set()
    thread.join(timeout=5)
    assert running_writers() == []


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_writer_that_raises_is_still_unregistered():
    def boom():
        raise RuntimeError("failed run")

    thread = start_writer(boom, name="action-refresh")
    thread.join(timeout=5)
    assert running_writers() == []


def test_writing_registers_the_enclosed_work():
    with writing("Explore re-embed"), writing("Explore re-embed"):
        assert running_writers() == ["Explore re-embed", "Explore re-embed"]
    assert running_writers() == []
