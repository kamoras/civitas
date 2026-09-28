"""Shared per-IP rate limiting for mutation endpoints (POST/DELETE).

Separate from public.py's read-only limiter so write endpoints can use a
tighter limit without coupling to the read-path code.
"""

import ipaddress
import threading
from collections import deque
from time import time
from typing import Annotated

from fastapi import Depends, HTTPException, Request

_WRITE_LIMIT = 20        # requests
_WRITE_PERIOD = 60.0     # per 60 seconds

_EVICT_EVERY = 2000


def _is_trusted_proxy_peer(peer: str | None) -> bool:
    """True when the direct peer is a private/loopback address — i.e. our
    own reverse proxy on the Docker network, never a public client.

    Loopback-only (the pre-2026-07 rule) was wrong for the production
    Swarm topology: nginx runs in its own container and reaches the
    backend over the overlay network, so the peer is nginx's overlay IP
    (e.g. 10.0.x.x), never 127.0.0.1 — the header was therefore NEVER
    trusted and both rate limiters keyed on nginx's single IP, collapsing
    per-IP limiting into one global bucket. The backend publishes no host
    port under Swarm (nginx is the only path to it), so any private-range
    peer IS the reverse proxy; trusting it is safe. This is not a spoofing
    hole: client_ip takes the LAST X-Forwarded-For hop, which nginx sets
    from its own $remote_addr ($proxy_add_x_forwarded_for) and a remote
    HTTP client cannot control.
    """
    if not peer:
        return False
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


def client_ip(request: Request) -> str:
    """Best-effort real client IP, trusting X-Forwarded-For only when the
    direct peer is our own reverse proxy (a private/loopback address).
    Any limiter that trusts this header unconditionally can be bypassed by
    sending a different fake value per request; taking the LAST hop (set
    by nginx from its own view of the peer) is the unspoofable choice.

    Caveat: nginx has no `real_ip` module, so its $remote_addr — hence the
    last XFF hop — is whatever connects to nginx. With an IP-preserving
    external port-forward (DNAT) that is the true client; behind a
    userspace/NAT forwarder it is that forwarder's address. Either way this
    is strictly better than bucketing every request under nginx's overlay
    IP, and never worse from a spoofing standpoint.
    """
    peer = request.client.host if request.client else None
    if _is_trusted_proxy_peer(peer):
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return peer or "unknown"


class _PerIpWindow:
    """A sliding window of request times per client IP."""

    def __init__(self, limit: int, period: float, what: str):
        self.limit, self.period, self.what = limit, period, what
        self.lock = threading.Lock()
        self.window: dict[str, deque] = {}
        self._count = 0

    def check(self, request: Request) -> None:
        ip = client_ip(request)
        now = time()
        cutoff = now - self.period
        with self.lock:
            dq = self.window.setdefault(ip, deque())
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self.limit:
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded — {self.limit} {self.what} per minute per IP.",
                    headers={"Retry-After": str(int(self.period))},
                )
            dq.append(now)
            self._count += 1
            if self._count >= _EVICT_EVERY:
                self._count = 0
                for k in [k for k, v in self.window.items() if not v or v[-1] < cutoff]:
                    del self.window[k]


_write_limiter = _PerIpWindow(_WRITE_LIMIT, _WRITE_PERIOD, "requests")
_window = _write_limiter.window  # the write limiter's state (tests clear it)


def write_rate_limit(request: Request) -> None:
    """FastAPI dependency: 20 mutation requests/minute per IP."""
    _write_limiter.check(request)


WriteRateLimit = Annotated[None, Depends(write_rate_limit)]


# ── Public routes that fetch from the shared api.data.gov key ─────
#
# A few read routes answer from an upstream API on a cache miss: a bill's
# record (Congress.gov, up to five calls) and a rulemaking's public comments
# (Regulations.gov). Both spend the api.data.gov key the nightly pipeline
# runs on — the Congress.gov fetch at 1.2 requests/s is already most of that
# API's hourly allowance — through the same process-wide rate limiter. Left
# open, one client walking bill numbers could spend the key's hour and stall
# the pipeline behind it. So these routes are held to a per-IP rate, and all
# of their upstream calls together to a fixed share of each hour; past it
# they answer 503 with Retry-After while cached answers keep serving.
_UPSTREAM_ROUTE_LIMIT = 10          # requests per minute per IP
_UPSTREAM_CALLS_PER_HOUR = 200      # upstream calls, all public routes together
_upstream_route_limiter = _PerIpWindow(_UPSTREAM_ROUTE_LIMIT, 60.0, "lookups")
_upstream_lock = threading.Lock()
_upstream_calls: deque = deque()


def upstream_route_limit(request: Request) -> None:
    """FastAPI dependency for routes that may fetch upstream."""
    _upstream_route_limiter.check(request)


UpstreamRouteLimit = Annotated[None, Depends(upstream_route_limit)]


def spend_upstream(calls: int) -> None:
    """Charge `calls` upstream requests to this hour's public budget, or
    raise 503 when they don't fit. Call only for cache misses."""
    if calls <= 0:
        return
    now = time()
    with _upstream_lock:
        while _upstream_calls and _upstream_calls[0] < now - 3600:
            _upstream_calls.popleft()
        if len(_upstream_calls) + calls > _UPSTREAM_CALLS_PER_HOUR:
            raise HTTPException(
                status_code=503,
                detail="Live lookups are paused for a few minutes; try again shortly.",
                headers={"Retry-After": "600"},
            )
        _upstream_calls.extend([now] * calls)


def reset_upstream_budget() -> None:
    """For tests."""
    with _upstream_lock:
        _upstream_calls.clear()
    with _upstream_route_limiter.lock:
        _upstream_route_limiter.window.clear()
