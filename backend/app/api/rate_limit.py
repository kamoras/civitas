"""Shared per-IP rate limiting for mutation endpoints (POST/DELETE).

Separate from public.py's read-only limiter so write endpoints can use a
tighter limit without coupling to the read-path code.
"""

import threading
from collections import deque
from time import time
from typing import Annotated

from fastapi import Depends, HTTPException, Request

_WRITE_LIMIT = 20        # requests
_WRITE_PERIOD = 60.0     # per 60 seconds

_lock = threading.Lock()
_window: dict[str, deque] = {}
_req_count = 0
_EVICT_EVERY = 2000

_TRUSTED_PROXIES = frozenset({"127.0.0.1", "::1"})


def client_ip(request: Request) -> str:
    """Best-effort real client IP, trusting X-Forwarded-For only when the
    direct peer is our own reverse proxy. Any rate limiter that trusts
    this header unconditionally can be bypassed by simply sending a
    different fake value on every request (found in public.py's own
    limiter, 2026-07 audit — it had a second, unguarded copy of this
    logic that made its already-wired-in rate limit a no-op against a
    trivial spoofed header)."""
    peer = request.client.host if request.client else None
    if peer in _TRUSTED_PROXIES:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return peer or "unknown"


def write_rate_limit(request: Request) -> None:
    """FastAPI dependency: 20 mutation requests/minute per IP."""
    global _req_count
    ip = client_ip(request)
    now = time()
    cutoff = now - _WRITE_PERIOD
    with _lock:
        dq = _window.get(ip)
        if dq is None:
            dq = deque()
            _window[ip] = dq
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= _WRITE_LIMIT:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded — {_WRITE_LIMIT} requests per minute per IP.",
                headers={"Retry-After": "60"},
            )
        dq.append(now)
        _req_count += 1
        if _req_count >= _EVICT_EVERY:
            _req_count = 0
            stale = [k for k, v in _window.items() if not v or v[-1] < cutoff]
            for k in stale:
                del _window[k]


WriteRateLimit = Annotated[None, Depends(write_rate_limit)]
