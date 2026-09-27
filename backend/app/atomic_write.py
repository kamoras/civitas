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
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from typing import Any

logger = logging.getLogger(__name__)

# How long update_json_file waits for another writer's lock. A holder keeps
# it for one read and one write of a small file — milliseconds — and the
# callers are often on an event loop, which a longer wait would stall.
LOCK_WAIT_S = 5.0
# After a lock wait times out, how long later attempts on that file try the
# lock once instead of waiting: a loop of saves (a state each) mustn't stall
# its event loop LOCK_WAIT_S apiece behind one stuck writer.
CONTENDED_COOLOFF_S = 60.0
_contended_until: dict[str, float] = {}


class LockTimeout(Exception):
    """Another writer held a file's lock past LOCK_WAIT_S. Not an OSError:
    callers take OSError to mean "this path isn't writable, try the next",
    and a change written to a fallback path because of a lock race would be
    lost the next time the primary is read. The update didn't happen; the
    caller decides what that costs (the data files' writers keep the change
    in memory and let the next run write it)."""


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
    **dump_kwargs: Any,
) -> dict[str, Any]:
    """Read `path`'s JSON object, `update` it, and write it back whole —
    under an exclusive lock (`path`.lock) held across all three, so no
    concurrent writer's change is lost. `missing()` stands in for a file
    that doesn't exist or doesn't hold a JSON object. `written(data)` runs
    before the lock is released — for a module cache, so writers publish
    their copies in the order they wrote them. `end` follows the JSON (a
    trailing newline). Returns what was written.
    Raises OSError when the file can't be written, LockTimeout when another
    writer holds the lock past LOCK_WAIT_S."""
    path = os.fspath(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(f"{path}.lock", "a") as lock:
        _lock(lock.fileno(), path)  # released when the file closes
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


def _lock(fd: int, path: str) -> None:
    now = time.monotonic()
    wait = 0.0 if _contended_until.get(path, 0.0) > now else LOCK_WAIT_S
    give_up = now + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _contended_until.pop(path, None)
            return
        except BlockingIOError:
            if time.monotonic() >= give_up:
                _contended_until[path] = time.monotonic() + CONTENDED_COOLOFF_S
                raise LockTimeout(f"{path} stayed locked by another writer") from None
            time.sleep(0.02)


class SharedJsonFile:
    """A JSON object file several writers update (update_json_file), with a
    module cache of it. A change that loses a lock race (LockTimeout) isn't
    written to a fallback path, where the next read wouldn't look, nor
    dropped: it waits in memory, applied to the cache now and carried by
    the next write that gets the lock. `paths()` gives the paths in order
    of preference (the first writable one is used); `load()` is the cached
    read; `publish(d)` replaces the cache."""

    def __init__(
        self, name: str, paths: Callable[[], Iterable[str]], load: Callable[[], dict[str, Any]],
        publish: Callable[[dict[str, Any]], None], **dump_kwargs: Any,
    ) -> None:
        self.name, self.paths = name, paths
        self.load, self.publish, self.dump_kwargs = load, publish, dump_kwargs
        self._pending: list[Callable[[dict[str, Any]], dict[str, Any]]] = []
        self._lock = threading.Lock()

    def update(self, change: Callable[[dict[str, Any]], dict[str, Any]], what: str) -> None:
        """Apply `change` to the file (and the cache), with any change still
        waiting from a lost lock race. Never raises for a lock race or an
        unwritable path: logs, and keeps the change in memory."""
        with self._lock:
            waiting = list(self._pending)

        def merge(known: dict[str, Any]) -> dict[str, Any]:
            for pending in (*waiting, change):
                known = pending(known)
            return known

        def written(data: dict[str, Any]) -> None:
            with self._lock:
                del self._pending[:len(waiting)]  # appended since stay waiting
                still = list(self._pending)
            for pending in still:
                data = pending(data)
            self.publish(data)

        for path in self.paths():
            try:
                update_json_file(path, merge, missing=lambda: dict(self.load()), written=written, **self.dump_kwargs)
                return
            except LockTimeout:
                with self._lock:
                    self._pending.append(change)
                logger.warning(
                    "%s for %s waits in memory — %s stayed locked; the next write carries it",
                    self.name, what, path,
                )
                break
            except OSError:
                continue
        else:
            logger.warning("Nowhere writable to record %s for %s", self.name, what)
        self.publish(change(dict(self.load())))
