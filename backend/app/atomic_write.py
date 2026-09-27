"""Replace a file whole, never in place.

A file rewritten in place (`open(path, "w")`, `Path.write_text`) is empty
from the moment it is truncated until the write finishes. A reader in that
window — another process, or this one's with a cold cache — gets an empty
or partial file: the discovered ballot sources or election dates read as
none, and a JSON parse error caches that as {} for the life of the
process; an empty embedding-model version file reads as a model change and
wipes the vector store. Written beside the target and renamed over it, the
file is always the old version or the new one.
"""

import os
import tempfile


def write_text_atomic(path: str | os.PathLike, text: str) -> None:
    """Write `text` to `path` (UTF-8), replacing it in one step. Raises
    OSError as a plain write would; nothing is left behind on failure."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{os.path.basename(path)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
