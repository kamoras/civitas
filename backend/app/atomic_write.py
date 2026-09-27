"""Replace a file whole, never in place; update one without losing a
concurrent writer's change.

A file rewritten in place (`open(path, "w")`, `Path.write_text`) is empty
from the moment it is truncated until the write finishes. A reader in that
window — another process, or this one's with a cold cache — gets an empty
or partial file: the discovered ballot sources or election dates read as
none, and a JSON parse error caches that as {} for the life of the
process; an empty embedding-model version file reads as a model change and
wipes the vector store. Written beside the target and renamed over it, the
file is always the old version or the new one.

A read-modify-write of a shared file (update_json_file) also holds an
exclusive lock across the read and the write, so two writers — the source
crawl and a ballot sync, the Senate and House pipelines, in one process or
two — can't each write back a copy missing the other's change.
"""

import fcntl
import json
import logging
import os
import stat
import time
import uuid
from collections.abc import Callable, Iterable
from typing import Any

logger = logging.getLogger(__name__)

# How long update_json_file waits for another writer's lock, by default. A
# holder keeps it for one read and one write — milliseconds, though an fsync
# on the Pi's SD card under a pipeline's I/O can take seconds — so a wait
# this long means a stuck writer, not a busy one. The writers are pipeline
# steps, each on its own thread and event loop (never the API's), so a wait
# delays only the pipeline that waits. A loop of saves (the crawl, a state
# each) passes a shorter one (DATA_FILE_WAIT_S): behind a stuck writer it
# pays that per save.
LOCK_WAIT_S = 10.0
DATA_FILE_WAIT_S = 2.0


class LockTimeout(Exception):
    """Another writer held a file's lock past the caller's wait
    (update_json_file's `wait`). Not an OSError:
    callers take OSError to mean "this path isn't writable, try the next",
    and a change written to a fallback path because of a lock race would be
    lost the next time the primary is read. The update didn't happen; the
    caller decides what that costs."""


def write_text_atomic(path: str | os.PathLike, text: str) -> None:
    """Write `text` to `path` (UTF-8), replacing it in one step, keeping
    the file's mode (a new one gets the umask's, as open() would give).
    Raises OSError as a plain write would; nothing is left behind on
    failure."""
    path = os.fspath(path)
    tmp = os.path.join(os.path.dirname(os.path.abspath(path)), f".{os.path.basename(path)}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)  # umask applies, as for open(path, "w")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            try:
                os.fchmod(fh.fileno(), stat.S_IMODE(os.stat(path).st_mode))
            except FileNotFoundError:
                pass
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def update_json_file(
    path: str | os.PathLike,
    update: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    missing: Callable[[], dict[str, Any]] = dict,
    written: Callable[[dict[str, Any]], None] | None = None,
    end: str = "",
    wait: float | None = None,
    **dump_kwargs: Any,
) -> dict[str, Any]:
    """Read `path`'s JSON object, `update` it, and write it back whole —
    under an exclusive lock (`path`.lock) held across all three, so no
    concurrent writer's change is lost. `missing()` stands in for a file
    that doesn't exist or doesn't hold a JSON object. `written(data)` runs
    before the lock is released — for a module cache, so writers publish
    their copies in the order they wrote them. `end` follows the JSON (a
    trailing newline). `wait` bounds the lock wait (LOCK_WAIT_S by default).
    Returns what was written.
    Raises OSError when the file can't be written, LockTimeout when another
    writer holds the lock past `wait`."""
    path = os.fspath(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(f"{path}.lock", "a") as lock:
        _lock(lock.fileno(), path, LOCK_WAIT_S if wait is None else wait)  # released when the file closes
        try:
            with open(path, encoding="utf-8") as fh:
                current = json.load(fh)
        except (FileNotFoundError, ValueError):
            current = None
        if not isinstance(current, dict):
            current = missing()
        updated = update(dict(current))
        write_text_atomic(path, json.dumps(updated, **dump_kwargs) + end)
        if written is not None:
            written(updated)
        return updated


def _lock(fd: int, path: str, wait: float) -> None:
    give_up = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= give_up:
                raise LockTimeout(f"{path} stayed locked by another writer for {wait}s") from None
            time.sleep(0.02)


def shared_file_path(paths: Iterable[str]) -> str | None:
    """The one file of a shared data file's candidate `paths` that both its
    loader and its writers use: the first that exists, or — before it
    exists anywhere — the first whose directory does (the data volume in
    production, the checkout's data/ in development). Chosen by existence
    alone, never by a read or write succeeding, so a file that can't be
    read or written is a failure on that file, not a quiet switch to
    another that the next process wouldn't look at."""
    paths = list(paths)
    for path in paths:
        if os.path.exists(path):
            return path
    for path in paths:
        if os.path.isdir(os.path.dirname(os.path.abspath(path))):
            return path
    return None


def update_shared_file(
    paths: Iterable[str],
    change: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    missing: Callable[[], dict[str, Any]],
    publish: Callable[[dict[str, Any]], None],
    what: str,
    wait: float | None = None,
    **dump_kwargs: Any,
) -> bool:
    """`change` a shared data file that has a module cache — the file
    shared_file_path picks, as its loader does — returning whether the
    change was recorded; `publish(data)` updates the cache under the file's
    lock. A failure (another writer holding it past `wait`,
    DATA_FILE_WAIT_S by default; a full disk) records nothing, anywhere:
    never another path, and never the cache alone, which the next process
    wouldn't have. The file and the cache agree; the caller says what the
    loss costs."""
    path = shared_file_path(paths)
    if path is None:
        logger.warning("%s not recorded — no data directory among %s", what, ", ".join(paths))
        return False
    try:
        update_json_file(
            path, change, missing=missing, written=publish,
            wait=DATA_FILE_WAIT_S if wait is None else wait, **dump_kwargs,
        )
        return True
    except LockTimeout:
        logger.warning("%s not recorded — %s stayed locked by another writer", what, path)
    except OSError:
        logger.warning("%s not recorded — %s couldn't be written", what, path, exc_info=True)
    return False
