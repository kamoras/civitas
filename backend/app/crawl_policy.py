"""How Civitas's crawler behaves toward each host it reads: one request at
a time, at the host's pace (its robots.txt's Crawl-delay included), and
not at all while the host is refusing it. /about/crawler describes this.

Every request through the shared async client (http_client.
make_async_client) goes through here, and so do the sync paths that read
sources (`sync_gate` / `sync_observe`).

- **Pace** (`interval`): at most one request in flight per host, and the
  starts at least `interval(host)` apart: the host's Crawl-delay if its
  robots.txt names one, else the fastest of the readers' own limiters for
  that host (crawler.readers(), read from the code), and never faster than
  one a second unless a reader's limiter says so (Congress.gov's 1.2/s, the
  SEC's 5/s, both published limits). A wait longer than MAX_WAIT_S is not
  waited out: the request fails, saying why.
- **robots.txt** (`ensure_robots`): read per host (fetch/robots.py, under
  the token "Civitas"), kept ROBOTS_TTL_S, for one thing: its Crawl-delay,
  which sets the pace. It does not block reads (owner decision,
  2026-10-25): Civitas reads public records at addresses its code names,
  and a Disallow is not applied. No file, or one that can't be read, means
  no Crawl-delay (the last one read stands in while it can't be read, for
  ROBOTS_RETRY_S before it is asked again).
- **Refusals** (`observe`): a bot wall's challenge (Cloudflare, Akamai,
  Imperva) or a 429 starts a backoff for the whole host: no request at all
  for REFUSAL_BASE_S, doubling with each refusal in a row up to
  REFUSAL_MAX_S; when it ends, the next request is the one probe, and a
  real answer clears the record. A Retry-After is honoured: up to
  MAX_WAIT_S it paces the next request, longer it is a backoff of its own.
  The backoff is kept on the data volume (host_refusals.json), so it holds
  across jobs, processes and restarts. A request refused here raises
  HostRefusing, an httpx.TransportError, naming the host, the refusal and
  when it ends: callers read it as the failed fetch it is, never as empty.
  A plain 403 is not a host's refusal (Akamai NetStorage answers 403 for a
  file that isn't there: www.senate.gov's floor logs for days without a
  session, measured 2026-10-25), and is not retried.
"""

import asyncio
import ipaddress
import json
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from app import crawler
from app.atomic_write import runtime_data_path, write_text_atomic
from app.pipeline.fetch import robots
from app.site_url import SITE_DOMAIN

logger = logging.getLogger(__name__)

AGENT = "Civitas"
DEFAULT_INTERVAL_S = 1.0
MAX_WAIT_S = 120.0
REFUSAL_BASE_S = 1800.0
REFUSAL_MAX_S = 7 * 86400.0
ROBOTS_TTL_S = 86400.0
ROBOTS_RETRY_S = 3600.0
STATE_FILE = "host_refusals.json"


class HostRefusing(httpx.TransportError):
    """No request was sent: the host is refusing Civitas, or its pace would
    make the request wait too long."""

    def __init__(self, host: str, reason: str, until: float | None = None):
        self.host, self.reason, self.until = host, reason, until
        when = f" until {datetime.fromtimestamp(until, timezone.utc):%Y-%m-%d %H:%M} UTC" if until else ""
        super().__init__(f"{host}: {reason}{when}; not requested")


class _Host:
    __slots__ = ("busy", "next_start")

    def __init__(self) -> None:
        self.busy = False
        self.next_start = 0.0  # time.monotonic()


_lock = threading.Lock()
_hosts: dict[str, _Host] = {}
_robots: dict[str, tuple[float | None, float, str]] = {}  # host -> (Crawl-delay, expires, how read)
_state: dict[str, dict] = {}
_state_stamp: tuple[float, int] | None = None


# Off only in tests (conftest's _crawl_policy_off): a test's mocked hosts
# have no robots.txt to read and no pace to keep. test_crawl_policy turns
# it on.
ENABLED = True


def governed(url: httpx.URL) -> bool:
    """A request to a source: https or http, a public host name. Never this
    site, the stack's own services (no dot), or an address."""
    if not ENABLED:
        return False
    host = (url.host or "").lower()
    if url.scheme not in ("http", "https") or "." not in host or host == "localhost":
        return False
    if host == SITE_DOMAIN or host.endswith("." + SITE_DOMAIN):
        return False
    try:
        ipaddress.ip_address(host.strip("[]"))
        return False
    except ValueError:
        return True


# ---- refusals, kept on the data volume -------------------------------------

def _path() -> str:
    return runtime_data_path(STATE_FILE)


def _load() -> None:
    """Re-read the shared record when another process has written it."""
    global _state, _state_stamp
    try:
        st = os.stat(_path())
    except OSError:
        return
    stamp = (st.st_mtime, st.st_size)
    if stamp == _state_stamp:
        return
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    _state, _state_stamp = (data if isinstance(data, dict) else {}), stamp


def _save() -> None:
    global _state_stamp
    try:
        write_text_atomic(_path(), json.dumps(_state, indent=1, sort_keys=True), durable=False)
        st = os.stat(_path())
        _state_stamp = (st.st_mtime, st.st_size)
    except OSError:
        logger.warning("host refusals not saved to %s", _path(), exc_info=True)


def refusing(host: str, now: float | None = None) -> dict | None:
    """The host's refusal record while its backoff holds: {"until", "count",
    "reason", "since"}; None when the host may be asked."""
    if not ENABLED:
        return None
    now = time.time() if now is None else now
    with _lock:
        _load()
        entry = _state.get(host.lower())
    return entry if entry and entry.get("until", 0) > now else None


def record_refusal(host: str, reason: str, retry_after: float | None = None) -> float:
    """A refusal: back off the host. Returns when the backoff ends."""
    host, now = host.lower(), time.time()
    if not ENABLED:
        return now
    with _lock:
        _load()
        prev = _state.get(host) or {}
        count = int(prev.get("count", 0)) + 1
        wait = min(REFUSAL_BASE_S * 2 ** (count - 1), REFUSAL_MAX_S)
        if retry_after is not None:
            wait = max(wait, retry_after)
        until = now + wait
        _state[host] = {
            "count": count, "until": until, "reason": reason,
            "since": prev.get("since") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _save()
    logger.warning("%s refused Civitas (%s, %d in a row): no requests to it for %.0f minutes",
                   host, reason, count, wait / 60)
    return until


def _clear(host: str) -> None:
    with _lock:
        _load()
        if _state.pop(host, None) is not None:
            _save()
            logger.info("%s answered again: its refusal record is cleared", host)


def refusals() -> dict[str, dict]:
    """Every host whose backoff holds now (for the crawler page)."""
    now = time.time()
    with _lock:
        _load()
        return {h: dict(e) for h, e in sorted(_state.items()) if e.get("until", 0) > now}


def retry_after_s(response: httpx.Response, now: float | None = None) -> float | None:
    """Retry-After in seconds (delta-seconds or an HTTP date), or None."""
    value = (response.headers.get("retry-after") or "").strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, when.timestamp() - (time.time() if now is None else now))


def observe(url: httpx.URL, response: httpx.Response, is_challenge: bool) -> None:
    """What the host's answer means for the next request to it."""
    host = url.host.lower()
    status = response.status_code
    retry_after = retry_after_s(response) if status in (429, 503) else None
    if is_challenge:
        record_refusal(host, f"a bot wall's challenge (HTTP {status})", retry_after)
    elif status == 429:
        if retry_after is not None and retry_after <= MAX_WAIT_S:
            _pace_after(host, retry_after)
        else:
            record_refusal(host, "too many requests (HTTP 429)", retry_after)
    elif status == 503 and retry_after is not None:
        if retry_after <= MAX_WAIT_S:
            _pace_after(host, retry_after)
        else:
            record_refusal(host, "unavailable, with a Retry-After (HTTP 503)", retry_after)
    elif status < 500 and status != 403:
        _clear(host)


def _pace_after(host: str, seconds: float) -> None:
    with _lock:
        h = _hosts.setdefault(host, _Host())
        h.next_start = max(h.next_start, time.monotonic() + seconds)


# ---- pace --------------------------------------------------------------------

def _limiter_intervals() -> dict[str, float]:
    """host -> the shortest gap any reader's own limiter keeps for it."""
    out: dict[str, float] = {}
    for reader in crawler.readers():
        if reader["rps"]:
            for host in reader["hosts"]:
                out[host] = min(out.get(host, DEFAULT_INTERVAL_S), 1 / reader["rps"])
    return out


_intervals: dict[str, float] | None = None


def interval(host: str) -> float:
    """Seconds between request starts for `host` (the module docstring)."""
    global _intervals
    if _intervals is None:
        _intervals = _limiter_intervals()
    base = _intervals.get(host, DEFAULT_INTERVAL_S)
    cached = _robots.get(host)
    delay = cached[0] if cached else None
    return max(base, delay or 0.0)


def _claim(host: str) -> float:
    """Take the host's slot if it is free and due (0.0), else how long to wait."""
    gap = interval(host)
    now = time.monotonic()
    with _lock:
        h = _hosts.setdefault(host, _Host())
        if not h.busy and now >= h.next_start:
            h.busy = True
            h.next_start = now + gap
            return 0.0
        return max(h.next_start - now, 0.05)


def _release(host: str) -> None:
    with _lock:
        _hosts.setdefault(host, _Host()).busy = False


def _check(host: str) -> None:
    entry = refusing(host)
    if entry:
        raise HostRefusing(host, f"refusing Civitas ({entry.get('reason')})", entry.get("until"))


@asynccontextmanager
async def slot(url: httpx.URL):
    """The host's turn: its refusal checked, one request at a time, at its pace."""
    host = url.host.lower()
    _check(host)
    waited = 0.0
    while (wait := _claim(host)) > 0:
        if waited + wait > MAX_WAIT_S and wait > 1:
            raise HostRefusing(host, f"its pace allows the next request in {wait:.0f} s")
        step = min(wait, 0.25)
        await asyncio.sleep(step)
        waited += step
    try:
        yield
    finally:
        _release(host)


@contextmanager
def sync_slot(url: str | httpx.URL):
    """The same turn for a blocking request (news feeds, the requests and
    urllib readers). A no-op for a request that isn't to a source."""
    url = httpx.URL(str(url))
    if not governed(url):
        yield
        return
    host = url.host.lower()
    _check(host)
    waited = 0.0
    while (wait := _claim(host)) > 0:
        if waited + wait > MAX_WAIT_S and wait > 1:
            raise HostRefusing(host, f"its pace allows the next request in {wait:.0f} s")
        step = min(wait, 0.25)
        time.sleep(step)
        waited += step
    try:
        yield
    finally:
        _release(host)


# ---- robots.txt: the pace it asks for -------------------------------------

def needs_robots(url: httpx.URL) -> bool:
    cached = _robots.get(url.host.lower())
    return url.path != "/robots.txt" and (cached is None or cached[1] <= time.monotonic())


def classify_robots(host: str, response: httpx.Response | None, is_challenge: bool) -> str:
    """Keep the Crawl-delay a read of `host`'s robots.txt gives. Returns how
    it read. Nothing here blocks a read."""
    now_m = time.monotonic()
    if response is not None and not is_challenge and 200 <= response.status_code < 300:
        delay = robots.parse_bytes(response.content).crawl_delay(AGENT)
        _robots[host] = (delay, now_m + ROBOTS_TTL_S, "read")
        return "read" + (f", Crawl-delay {delay:g} s" if delay else "")
    if response is not None and not is_challenge and 400 <= response.status_code < 500:
        _robots[host] = (None, now_m + ROBOTS_TTL_S, f"HTTP {response.status_code}")
        return f"HTTP {response.status_code} (no Crawl-delay)"
    last = _robots.get(host)
    _robots[host] = (last[0] if last else None, now_m + ROBOTS_RETRY_S, "unreadable")
    return "unreadable (the last Crawl-delay read stands)"


async def ensure_robots(url: httpx.URL, fetch: Callable[[str], Awaitable[httpx.Response]],
                        is_challenge: Callable[[httpx.Response], bool]) -> None:
    """Read the host's robots.txt for its Crawl-delay, once per ROBOTS_TTL_S."""
    if not needs_robots(url):
        return
    host = url.host.lower()
    try:
        response = await fetch(f"{url.scheme}://{url.netloc.decode()}/robots.txt")
    except HostRefusing:
        raise
    except httpx.HTTPError:
        response = None
    how = classify_robots(host, response, response is not None and is_challenge(response))
    logger.info("%s robots.txt: %s", host, how)


def reset_for_tests() -> None:
    """Forget every in-memory record (pace, robots, the shared file's copy)."""
    global _state, _state_stamp, _intervals
    with _lock:
        _hosts.clear()
        _robots.clear()
        _state, _state_stamp, _intervals = {}, None, None
