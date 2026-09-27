"""Work that writes the database outside a request's own turn on the loop.

Every background writer thread the app starts goes through start_writer, and
work an endpoint hands to a thread while it awaits goes through writing() —
which is how anything that must not overlap them (the admin data reset) can
see them all. A hand-kept list of thread names or per-job flags goes stale
the first time someone adds a job and forgets it.
"""

import itertools
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

_lock = threading.Lock()
_running: dict[int, str] = {}
_ids = itertools.count()


@contextmanager
def writing(name: str) -> Iterator[None]:
    """Registers the enclosed work as a database writer while it runs."""
    token = next(_ids)
    with _lock:
        _running[token] = name
    try:
        yield
    finally:
        with _lock:
            del _running[token]


def start_writer(target: Callable[..., object], *, name: str, args: tuple = ()) -> threading.Thread:
    """Start a daemon thread running `target(*args)`, registered as a
    database writer until it returns. Registered before the thread starts,
    so it is visible from the moment this returns."""
    registration = writing(name)
    registration.__enter__()

    def _run() -> None:
        try:
            target(*args)
        finally:
            registration.__exit__(None, None, None)

    thread = threading.Thread(target=_run, daemon=True, name=name)
    thread.start()
    return thread


def running_writers() -> list[str]:
    """What is writing now."""
    with _lock:
        return sorted(_running.values())
