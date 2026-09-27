"""app.background: the registry the admin data reset reads, and the gate it
holds, to keep every database writer out of its wipe."""

import threading

import pytest

from app.background import WritersBusy, WritesHeld, exclusive, running_writers, start_writer, writing


def test_a_writer_thread_is_registered_from_start_until_it_returns():
    release = threading.Event()
    thread = start_writer(release.wait, name="test-nightly")
    assert "test-nightly" in running_writers()
    release.set()
    thread.join(timeout=5)
    assert "test-nightly" not in running_writers()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_writer_that_raises_is_still_unregistered():
    def boom():
        raise RuntimeError("failed run")

    start_writer(boom, name="test-boom").join(timeout=5)
    assert "test-boom" not in running_writers()


def test_writing_registers_the_enclosed_work():
    with writing("test-reembed"), writing("test-reembed"):
        assert running_writers().count("test-reembed") == 2
    assert "test-reembed" not in running_writers()


def test_exclusive_is_refused_while_anything_writes():
    with writing("test-refresh"):
        with pytest.raises(WritersBusy) as busy:
            with exclusive("test-reset"):
                pass
    assert "test-refresh" in busy.value.names


def test_nothing_starts_while_exclusive_is_held():
    ran = []
    with exclusive("test-reset"):
        with pytest.raises(WritesHeld):
            start_writer(lambda: ran.append(1), name="test-late")
        with pytest.raises(WritesHeld):
            with writing("test-late"):
                pass
        with pytest.raises(WritersBusy):  # a second reset
            with exclusive("test-reset-2"):
                pass
    assert ran == [] and "test-late" not in running_writers()
    with writing("test-after"):  # released
        pass
