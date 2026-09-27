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

A read-modify-write of a file two writers share (update_json_file — the
Senate and House pipelines each own a key of the population references and
the member ideal points) also holds an exclusive lock across the read and
the write, so neither can write back a copy missing the other's key.
"""

import fcntl
import json
import os
import stat
import time
import uuid
from collections.abc import Callable
from typing import Any

# How long update_json_file waits for another writer's lock. A holder keeps
# it for one read and one write — milliseconds, though an fsync on the Pi's
# SD card under a pipeline's I/O can take seconds — so a wait this long
# means a stuck writer, not a busy one. The writers are pipeline steps, each
# on its own thread and event loop (never the API's), so a wait delays only
# the pipeline that waits.
LOCK_WAIT_S = 10.0


class LockTimeout(Exception):
    """Another writer held a file's lock past LOCK_WAIT_S. Not an OSError: a
    caller that takes OSError to mean "this path isn't writable, try the
    next" would write the change where the next read won't look. The update
    didn't happen; the caller decides what that costs."""


def write_text_atomic(path: str | os.PathLike, text: str) -> None:
    """Write `text` to `path` (UTF-8), replacing it in one step, keeping
    the file's mode (a new one gets the umask's, as open() would give).
    Raises OSError as a plain write would; nothing is left behind on
    failure."""
    path = os.fspath(path)
    directory, name = os.path.dirname(os.path.abspath(path)), os.path.basename(path)
    _sweep_leftovers(directory, name)
    tmp = os.path.join(directory, f".{name}.{uuid.uuid4().hex}.tmp")
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


# A temp file this old beside its target is a write that never finished —
# its process was killed between creating it and the rename — not one in
# flight (a write takes milliseconds to seconds).
_LEFTOVER_AFTER_S = 3600


def _sweep_leftovers(directory: str, name: str) -> None:
    """Remove a killed writer's temp files for `name`, so they don't pile up
    on the data volume across deploys."""
    prefix, cutoff = f".{name}.", time.time() - _LEFTOVER_AFTER_S
    try:
        entries = os.listdir(directory)
    except OSError:
        return
    for entry in entries:
        if entry.startswith(prefix) and entry.endswith(".tmp"):
            leftover = os.path.join(directory, entry)
            try:
                if os.stat(leftover).st_mtime < cutoff:
                    os.unlink(leftover)
            except OSError:
                pass


def update_json_file(
    path: str | os.PathLike,
    update: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    missing: Callable[[], dict[str, Any]] = dict,
    end: str = "",
    **dump_kwargs: Any,
) -> dict[str, Any]:
    """Read `path`'s JSON object, `update` it, and write it back whole —
    under an exclusive lock (`path`.lock) held across all three, so no
    concurrent writer's change is lost. `missing()` stands in for a file
    that doesn't exist or doesn't hold a JSON object; `end` follows the JSON
    (a trailing newline). Returns what was written. Raises OSError when the
    file can't be written, LockTimeout when another writer holds the lock
    past LOCK_WAIT_S."""
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
        return updated


def _lock(fd: int, path: str) -> None:
    give_up = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= give_up:
                raise LockTimeout(f"{path} stayed locked by another writer for {LOCK_WAIT_S}s") from None
            time.sleep(0.02)
