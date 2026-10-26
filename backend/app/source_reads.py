"""Whether each source answered: every request crawl_policy governs ends
here as an answer (a real one, or a refusal: a bot wall, a 429, a server
error) or as no answer at all (a timeout, a failed connection).

Two records, both counts and times, never a request's address or body:

- **Per process, cumulative** (`counts`): answered and failed requests per
  host, which ProgressTracker diffs over each step's window to say which
  run and step read which host (SourceReadStat), as it does for the rate
  limiters.
- **Per host, on the data volume** (`source_reads.json`, `reads`): when the
  host was last asked, last answered, last failed and how, and since when
  it has failed without an answer in between. Written at most every
  FLUSH_S per process, and at the end of every pipeline step; merged with
  what another process wrote, newest time winning, so the API process's
  source map (app/source_health.py) reads every process's reads.

A plain 403 or 404 is an answer: the host is up and said something about
one address (crawl_policy's docstring). What fails a host is a bot wall's
challenge (walled), a 429 (refused), a 5xx or no answer (unavailable).
"""

import atexit
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import httpx

from app.atomic_write import runtime_data_path, write_text_atomic

logger = logging.getLogger(__name__)

READS_FILE = "source_reads.json"
# Longest a process keeps reads it hasn't written: a minute of a source's
# record lost in a crash, against a file write per request.
FLUSH_S = 60.0

_lock = threading.Lock()
_counts: dict[str, list[int]] = {}  # host -> [answered, failed], since the process started
_pending: dict[str, dict] = {}  # host -> its record since the last flush
_last_flush = 0.0


def _now() -> str:
    # Microseconds: an answer and a failure in the same second stay ordered.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def failure_of(status: int, challenge: bool) -> tuple[str, str] | None:
    """(kind, plain reason) when an answer fails the host, else None."""
    if challenge:
        return "walled", f"a bot wall's challenge (HTTP {status})"
    if status == 429:
        return "refused", "asked Civitas to slow down (HTTP 429)"
    if status >= 500:
        return "unavailable", f"a server error (HTTP {status})"
    return None


def unanswered_reason(exc: BaseException) -> str:
    """No answer, in plain words."""
    if isinstance(exc, httpx.TimeoutException):
        return "no answer in time"
    if isinstance(exc, httpx.ConnectError):
        return "the connection failed"
    return "the request failed"


def answered(host: str, status: int, challenge: bool) -> None:
    failure = failure_of(status, challenge)
    _record(host, failure is None, *(failure or (None, None)))


def unanswered(host: str, exc: BaseException) -> None:
    _record(host, False, "unavailable", unanswered_reason(exc))


def held_off(host: str, reason: str) -> None:
    """A reader asked for the host while its refusal backoff holds: no
    request went out, but the step that asked still needed the host and
    didn't get it, so it counts against the host (and its step) like a
    refusal."""
    _record(host, False, "walled" if "bot wall" in reason else "refused", reason)


def _record(host: str, ok: bool, kind: str | None, reason: str | None) -> None:
    host, now = host.lower(), _now()
    with _lock:
        _counts.setdefault(host, [0, 0])[0 if ok else 1] += 1
        rec = _pending.setdefault(host, {})
        rec["lastAttempt"] = now
        if ok:
            rec["lastOk"] = now
            rec.pop("failingSince", None)
        else:
            rec["lastFailure"], rec["kind"], rec["reason"] = now, kind, reason
            rec.setdefault("failingSince", now)
        due = time.monotonic() - _last_flush >= FLUSH_S
    if due:
        flush()


def counts() -> dict[str, tuple[int, int]]:
    """(answered, failed) per host since the process started."""
    with _lock:
        return {h: (c[0], c[1]) for h, c in _counts.items()}


def diff(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int]]:
    """Per host (answered, failed) between two `counts`; hosts with none left out."""
    out = {}
    for host, (ok, failed) in after.items():
        ok0, failed0 = before.get(host, (0, 0))
        if ok - ok0 or failed - failed0:
            out[host] = (ok - ok0, failed - failed0)
    return out


def merge(old: dict, new: dict) -> dict:
    """One host's record from two: each time the newer, the failure's kind
    and reason with the newer failure, and failing since the earliest
    failure after the last answer (none once it answered last)."""
    out = {k: max(filter(None, (old.get(k), new.get(k))), default=None)
           for k in ("lastAttempt", "lastOk", "lastFailure")}
    out = {k: v for k, v in out.items() if v}
    if out.get("lastFailure"):
        newer = new if new.get("lastFailure") == out["lastFailure"] else old
        out["kind"], out["reason"] = newer.get("kind"), newer.get("reason")
    if out.get("lastFailure") and out["lastFailure"] > out.get("lastOk", ""):
        since = [s for s in (old.get("failingSince"), new.get("failingSince"))
                 if s and s > out.get("lastOk", "")]
        out["failingSince"] = min(since) if since else out["lastFailure"]
    return out


def _read_file() -> dict:
    try:
        with open(runtime_data_path(READS_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def flush() -> None:
    """Write this process's reads since the last flush, merged into the file."""
    global _last_flush
    with _lock:
        pending = dict(_pending)
        _pending.clear()
        _last_flush = time.monotonic()
    if not pending:
        return
    try:
        data = _read_file()
        for host, rec in pending.items():
            data[host] = merge(data.get(host, {}), rec)
        path = runtime_data_path(READS_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_text_atomic(path, json.dumps(data, indent=1, sort_keys=True), durable=False)
    except OSError:
        logger.warning("source reads not saved", exc_info=True)


def reads() -> dict[str, dict]:
    """Every host's record: the file's, with this process's unwritten reads."""
    data = _read_file()
    with _lock:
        pending = {h: dict(r) for h, r in _pending.items()}
    for host, rec in pending.items():
        data[host] = merge(data.get(host, {}), rec)
    return data


# A short-lived process (a data refresh's script) writes what it read
# before it exits.
atexit.register(flush)


def reset_for_tests() -> None:
    global _last_flush
    with _lock:
        _counts.clear()
        _pending.clear()
        _last_flush = 0.0
