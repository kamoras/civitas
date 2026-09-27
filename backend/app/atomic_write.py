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
exclusive lock across the read and the write, so two writers — the nightly
source crawl and a ballot sync, in one process or two — can't each write
back a copy missing the other's change.
"""

import fcntl
import json
import os
import stat
import tempfile
from collections.abc import Callable
from typing import Any


def write_text_atomic(path: str | os.PathLike, text: str) -> None:
    """Write `text` to `path` (UTF-8), replacing it in one step, with the
    mode it had (0644 for a new file). Raises OSError as a plain write
    would; nothing is left behind on failure."""
    directory = os.path.dirname(os.path.abspath(path))
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{os.path.basename(path)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            # mkstemp's 0600 would otherwise become the file's mode.
            os.fchmod(fh.fileno(), mode)
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
    **dump_kwargs: Any,
) -> dict[str, Any]:
    """Read `path`'s JSON object, `update` it, and write it back whole —
    under an exclusive lock (`path`.lock) held across all three, so no
    concurrent writer's change is lost. `missing()` stands in for a file
    that doesn't exist or can't be parsed. Returns what was written."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(f"{path}.lock", "a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)  # released when the file closes
        try:
            with open(path, encoding="utf-8") as fh:
                current = json.load(fh) or {}
        except (FileNotFoundError, ValueError):
            current = missing()
        updated = update(dict(current))
        write_text_atomic(path, json.dumps(updated, **dump_kwargs))
        return updated
