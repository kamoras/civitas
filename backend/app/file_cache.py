"""Staleness checks for module-level caches of files another process writes.

The pipeline runs in its own process (settings.PROCESS_ROLE), and several
files it rewrites at runtime are cached in memory by the API processes that
read them. A writer clearing its module's cache only clears its own
process's copy; the readers have to notice the new file themselves. Each
cache keeps the stamp its copy was loaded under and reloads when
files_stamp() says otherwise — one stat per path per read. The one mechanism
for every such cache: district PVI and member ideal points
(score_calculator), the population references (population_reference),
ballot lookup links, discovered candidate sources and election dates
(fetch/) — each through reload_if_moved, so the order of stat and read, and
what a failed read leaves behind, are decided once.
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


# A stamp no file ever has: a value stored under it is reloaded on next use.
_RELOAD = ("reload",)


class Uncached(Exception):
    """Raised by a reload_if_moved loader for a result that must not be
    kept — an unreadable file is not an empty one — carrying what to return
    this time."""

    def __init__(self, value):
        super().__init__("not cached")
        self.value = value


def reload_if_moved(paths, cached, cached_stamp: Stamp, load):
    """The one rule for a module cache of files another process rewrites:
    returns (value, stamp) for the caller to keep.

    `cached` stands while `paths` still carry `cached_stamp`; otherwise
    `load()` reads them. The stamp is taken before the read, so a rewrite
    that lands during it moves the stamp again and the next call reloads —
    never the other way round, which would pin the older content. A loader
    that raises Uncached(value) has `value` returned and retried on the next
    call. A value set directly (tests, a bundled fallback) with stamp None
    stands until a file appears."""
    stamp = files_stamp(paths)
    if cached is not None and stamp == cached_stamp:
        return cached, cached_stamp
    try:
        return load(), stamp
    except Uncached as uncached:
        return uncached.value, _RELOAD
