"""Staleness checks for module-level caches of files another process writes.

The pipeline runs in its own process (settings.PROCESS_ROLE), and several
files it rewrites at runtime are cached in memory by the API processes that
read them. A writer clearing its module's cache only clears its own
process's copy; the readers have to notice the new file themselves. Each
cache keeps the stamp its copy was loaded under and reloads when
files_stamp() says otherwise — one stat per path per read.
"""

import os
from collections.abc import Iterable

Stamp = tuple[float | None, ...] | None


def files_stamp(paths: Iterable[str | os.PathLike]) -> Stamp:
    """The modification times of `paths`, or None when none exists (so a
    cache loaded from a bundled fallback, or set directly, stays valid
    until a runtime copy appears)."""
    mtimes = []
    for path in paths:
        try:
            mtimes.append(os.stat(path).st_mtime)
        except OSError:
            mtimes.append(None)
    return None if all(m is None for m in mtimes) else tuple(mtimes)
