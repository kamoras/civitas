"""Run the test suite as if it were another date: a pytest plugin.

Tests must never depend on the real clock (AGENTS.md, Testing): a fixture
written for one election cycle or one Congress breaks the day the calendar
moves past it — election night, noon ET on January 3 of an odd year. This
plugin moves the whole process's clock to FREEZE_AT, so the fast suite can
be run on those dates before they arrive:

    cd backend
    FREEZE_AT=2027-01-03T17:01Z PYTHONPATH="$PWD" \\
        PYTEST_PLUGINS=scripts.frozen_clock_plugin \\
        .venv/bin/python -m pytest tests/ -m "not slow" -p no:cacheprovider

PYTEST_PLUGINS (rather than ``-p``) so the nested pytest runs some tests
start in a subprocess travel too; PYTHONPATH makes ``scripts`` importable
in those subprocesses, whatever their working directory. The plugin is
imported while pytest registers plugins, before conftest.py imports app
code, so values computed at import (settings.CURRENT_CONGRESS) see the
travelled clock as a process started on that date would.

Needs time-machine (scripts/requirements-research.txt):

    pip install -r requirements.txt -r scripts/requirements-research.txt

- FREEZE_AT: an ISO 8601 instant (required; a ``Z`` or offset, else UTC).
- FREEZE_TICK=0: stop the clock at FREEZE_AT instead of letting it run on
  from there (the default — a suite with timeouts needs a moving clock).

The kernel stamps files with the real time, so code that compares a file's
age with the clock (cache freshness, heartbeats) would see every fresh file
as months or years off. os.stat, os.lstat and os.fstat therefore report
times shifted by the same constant as the clock, and os.utime takes
travelled times and stores real ones, so a utime then a stat round-trips as
it does on the real clock. Not shifted: os.DirEntry.stat (os.scandir) and
anything reading timestamps outside these functions (importlib reads
posix.stat directly, so its bytecode caches are unaffected).
"""

import math
import os
import time

import time_machine
from time_machine import escape_hatch

_NS = 10**9

# Set by _install(): one constant shift, taken once. A shift re-read on
# every call would move by the nanoseconds between two reads, and a utime
# then a stat would not round-trip.
_OFFSET_NS = 0

# os.stat_result's fields beyond its ten visible ones, by name (st_atime,
# st_atime_ns, st_blksize, ... as this platform has them).
_TIMES = ("atime", "mtime", "ctime")
_VISIBLE_INT_TIMES = {"atime": 7, "mtime": 8, "ctime": 9}


def _seconds(ns: int) -> float:
    # As CPython builds st_mtime from the kernel's (sec, nsec): sec + nsec * 1e-9.
    # Adding a float offset to the real st_mtime instead rounds differently,
    # and int(offset / 1e9) on the integer fields could be a second off.
    return ns // _NS + (ns % _NS) * 1e-9


def _shifted(result: os.stat_result) -> os.stat_result:
    visible = list(result)
    hidden = {
        name: getattr(result, name)
        for name in dir(result)
        if name.startswith("st_") and name not in os.stat_result.__match_args__
    }
    for which in _TIMES:
        ns = getattr(result, f"st_{which}_ns") + _OFFSET_NS
        visible[_VISIBLE_INT_TIMES[which]] = ns // _NS
        hidden[f"st_{which}"] = _seconds(ns)
        hidden[f"st_{which}_ns"] = ns
    return os.stat_result(visible, hidden)


def _wrap_stat(function):
    def stat(*args, **kwargs):
        return _shifted(function(*args, **kwargs))

    stat.__wrapped__ = function
    return stat


def _ns(seconds) -> int:
    """Seconds (int or float) as integer ns, rounded as os.utime rounds a
    float (floor of the fractional part). Never ``round(t * 1e9)``: a
    float holds about 16 digits and 2027 in ns has 19."""
    if isinstance(seconds, int):
        return seconds * _NS
    fraction, whole = math.modf(seconds)
    nsec = math.floor(fraction * 1e9)
    if nsec >= _NS:
        nsec, whole = nsec - _NS, whole + 1
    elif nsec < 0:
        nsec, whole = nsec + _NS, whole - 1
    return int(whole) * _NS + nsec


_real_utime = os.utime


def _utime(path, times=None, *, ns=None, **kwargs):
    if ns is None:
        if times is None:
            now = time.time_ns()
            ns = (now, now)
        else:
            ns = (_ns(times[0]), _ns(times[1]))
    return _real_utime(path, ns=(ns[0] - _OFFSET_NS, ns[1] - _OFFSET_NS), **kwargs)


def _install(at: str) -> None:
    global _OFFSET_NS
    # A FREEZE_AT with no Z or offset is UTC, as documented. time-machine's
    # default (MIXED) would read it in the process's local zone, hours off
    # the hand-over instant on a machine not set to UTC.
    time_machine.naive_mode = time_machine.NaiveMode.UTC
    time_machine.travel(at, tick=os.environ.get("FREEZE_TICK", "1") != "0").start()
    _OFFSET_NS = time.time_ns() - escape_hatch.time.time_ns()
    os.stat = _wrap_stat(os.stat)
    os.lstat = _wrap_stat(os.lstat)
    os.fstat = _wrap_stat(os.fstat)
    os.utime = _utime


def pytest_configure(config):
    """Loaded as a plugin without FREEZE_AT, the run would silently be on
    the real clock: stop it instead. Only here, not at import, so importing
    the module (CI's every-script import check) does nothing."""
    import pytest

    if not os.environ.get("FREEZE_AT"):
        raise pytest.UsageError("scripts/frozen_clock_plugin.py: set FREEZE_AT to the instant to run at")


# Travel at import, while pytest registers plugins and before conftest.py
# imports the app, so the process-start Congress is computed on the
# travelled clock.
if os.environ.get("FREEZE_AT"):
    _install(os.environ["FREEZE_AT"])
