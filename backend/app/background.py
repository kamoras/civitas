"""Work that writes the database outside a request's own turn on the loop.

Every background writer thread the app starts goes through start_writer, and
work an endpoint hands to a thread while it awaits goes through writing() —
which is how anything that must not overlap them (the admin data reset) can
see them all, and hold them off: exclusive() is granted only while nothing
is registered, and while it is held nothing new registers. Both happen under
one lock, so there is no gap between the check and the hold. A hand-kept
list of thread names or per-job flags goes stale the first time someone
adds a job and forgets it.
"""

import itertools
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

_lock = threading.Lock()
_running: dict[int, str] = {}
_ids = itertools.count()
_exclusive = False


class WritersBusy(RuntimeError):
    """exclusive() refused: these were writing (or another holder has it)."""

    def __init__(self, names: list[str]) -> None:
        super().__init__(", ".join(names))
        self.names = names


class WritesHeld(RuntimeError):
    """A writer was refused: exclusive() is held."""


def _register(name: str) -> int:
    with _lock:
        if _exclusive:
            raise WritesHeld(f"{name} not started: the database is held for a data reset")
        token = next(_ids)
        _running[token] = name
        return token


def _unregister(token: int) -> None:
    with _lock:
        _running.pop(token, None)


@contextmanager
def writing(name: str) -> Iterator[None]:
    """Registers the enclosed work as a database writer while it runs.
    Raises WritesHeld, before the work starts, while exclusive() is held."""
    token = _register(name)
    try:
        yield
    finally:
        _unregister(token)


def start_writer(target: Callable[..., object], *, name: str, args: tuple = ()) -> threading.Thread:
    """Start a daemon thread running `target(*args)`, registered as a
    database writer from before it starts until it returns. Raises
    WritesHeld, starting nothing, while exclusive() is held — which an
    endpoint answers with a 409 (main's handler) and the scheduler logs,
    or alerts on (scheduler._start_job)."""
    token = _register(name)

    def _run() -> None:
        try:
            target(*args)
        finally:
            _unregister(token)

    thread = threading.Thread(target=_run, daemon=True, name=name)
    try:
        thread.start()
    except BaseException:
        _unregister(token)
        raise
    return thread


@contextmanager
def exclusive(holder: str) -> Iterator[None]:
    """Hold every writer in this process off for the enclosed work. Raises
    WritersBusy, naming them, if any is writing — or if another holder
    already has it."""
    global _exclusive
    with _lock:
        if _exclusive:
            raise WritersBusy([holder])
        if _running:
            raise WritersBusy(sorted(_running.values()))
        _exclusive = True
    try:
        yield
    finally:
        with _lock:
            _exclusive = False


def running_writers() -> list[str]:
    """What is writing now."""
    with _lock:
        return sorted(_running.values())
