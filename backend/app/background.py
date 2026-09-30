"""Work that writes the database outside a request's own turn on the loop.

Every background writer thread the app starts goes through start_writer, and
work an endpoint hands to a thread while it awaits goes through writing() —
which is how anything that must not overlap them (the admin data reset) can
see them all, and hold them off: exclusive() is granted only while nothing
is registered, and while it is held nothing new registers. Both happen under
one lock, so there is no gap between the check and the hold. A hand-kept
list of thread names or per-job flags goes stale the first time someone
adds a job and forgets it.

It is also where the read-only API process (settings.PROCESS_ROLE == "api")
refuses background work. Production runs the pipeline in its own process so
that a run can't hold the interpreter lock or the memory page requests need;
nginx sends every endpoint that starts one to that process. A trigger that
reaches the API process anyway (a route nginx wasn't told about) is refused
here, loudly, rather than quietly running a pipeline beside page requests —
and outside the pipeline process's registry, where the data reset would not
see it.
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


class WritesElsewhere(RuntimeError):
    """A writer was refused: this is the read-only API process, and
    background work runs in the pipeline process (settings.PROCESS_ROLE)."""


def writers_allowed() -> bool:
    """Whether this process may start background writers — every role but
    the read-only API."""
    from app.config import settings

    return settings.PROCESS_ROLE != "api"


def _register(name: str) -> int:
    if not writers_allowed():
        raise WritesElsewhere(f"{name} not started: this process serves reads only; it runs on the pipeline service")
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
