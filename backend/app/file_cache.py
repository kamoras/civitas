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

import json
import logging
import os
import threading
from collections.abc import Iterable

logger = logging.getLogger(__name__)

Stamp = tuple | None


def files_stamp(paths: Iterable[str | os.PathLike]) -> Stamp:
    """What identifies the current version of `paths` — each one's inode,
    size and modification time in nanoseconds — or None when none exists
    (so a cache loaded from a bundled fallback, or set directly, stays valid
    until a runtime copy appears). The inode matters: every rewrite here is
    atomic (a new file renamed into place), and two within one tick of a
    coarse filesystem clock would otherwise carry the same mtime."""
    stamps = []
    for path in paths:
        try:
            st = os.stat(path)
        except OSError:
            stamps.append(None)
        else:
            stamps.append((st.st_ino, st.st_size, st.st_mtime_ns))
    return None if all(stamp is None for stamp in stamps) else tuple(stamps)


# A stamp no file ever has: a value stored under it is reloaded on next use.
_RELOAD = ("reload",)

def new_reload_lock() -> threading.RLock:
    """A lock for one reload_if_moved cache, held around the call and its
    assignment: a value and its stamp are two stores, and two threads
    reloading at once could otherwise leave the older value under the newer
    stamp — served until the file next changed. One per cache, so a slow
    reload of one file doesn't hold up readers of another. Re-entrant, so a
    loader may read another cache."""
    return threading.RLock()


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


def read_json(path: str | os.PathLike):
    """A file's JSON, or None when it is absent or not JSON (logged). Raises
    OSError when it exists but can't be read right now — unreadable is not
    absent, and callers of reload_if_moved must not keep it as such."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except ValueError:
        logger.exception("%s is not valid JSON", path)
        return None


def read_json_preferring(*paths: str | os.PathLike, default, accept=None):
    """The first of `paths` that holds JSON (and passes `accept`, when given)
    — a runtime copy ahead of its bundled fallback — else `default`. When
    one ahead of it exists but couldn't be read, the result is raised as
    Uncached: it stands in for a file that will be readable again, so a
    cache retries rather than keeping it."""
    unreadable = False
    for path in paths:
        try:
            data = read_json(path)
        except OSError:
            logger.warning("Couldn't read %s — retrying on next use", path, exc_info=True)
            unreadable = True
            continue
        if data is not None and (accept is None or accept(data)):
            if unreadable:
                raise Uncached(data)
            return data
    if unreadable:
        raise Uncached(default)
    return default


def load_json_once(cached, *paths: str | os.PathLike):
    """For a module cache loaded once from a runtime copy else its bundled
    fallback (files nothing rewrites while the process runs): returns
    (the dict to use now, the dict to keep — None when it stood in for an
    unreadable file, so the next call reads again)."""
    if cached is not None:
        return cached, cached
    try:
        data = read_json_preferring(*paths, default={}, accept=lambda d: isinstance(d, dict))
    except Uncached as unreadable:
        return unreadable.value, None
    return data, data
