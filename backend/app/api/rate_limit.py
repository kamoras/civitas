"""Shared per-IP rate limiting for mutation endpoints (POST/DELETE), and
for the public routes that spend the shared api.data.gov key.

Separate from public.py's read-only limiter so write endpoints can use a
tighter limit without coupling to the read-path code. Every limit here is
counted in the throttle store every API worker process shares
(api/throttle.py), so it holds per client — not per client per worker —
and the hourly upstream budget holds for the whole backend.
"""

import ipaddress
import math
import time
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.api import throttle

_WRITE_LIMIT = 20        # requests
_WRITE_PERIOD = 60.0     # per 60 seconds


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

    nginx sets that last hop from its $remote_addr, which its realip
    configuration (nginx/civitas.conf: set_real_ip_from the host-level
    nginx, real_ip_header X-Real-IP) resolves to the visitor; the internal
    cache-miss hop passes the header on unchanged rather than adding itself.
    """
    peer = request.client.host if request.client else None
    if _is_trusted_proxy_peer(peer):
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return peer or "unknown"


def retry_after(reset_at: int) -> str:
    """Retry-After, in whole seconds, for a refusal that lifts at `reset_at`."""
    return str(max(1, math.ceil(reset_at - time.time())))


def limit_client(ip: str, bucket: str, *, limit: int, period: float) -> throttle.Decision:
    """Key `ip` for `bucket` and count it, in one call — so a limited
    request costs one thread hop, not one for the key and one for the count."""
    return throttle.hit(bucket, throttle.client_key(ip, bucket), limit=limit, period=period)


class _PerClientLimit:
    """At most `limit` requests per `period` seconds per client."""

    def __init__(self, bucket: str, limit: int, period: float, what: str):
        self.bucket, self.limit, self.period, self.what = bucket, limit, period, what

    async def check(self, request: Request) -> None:
        decision = await throttle.run(
            limit_client, client_ip(request), self.bucket, limit=self.limit, period=self.period,
        )
        if not decision.allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: {self.limit} {self.what} per minute per IP.",
                headers={"Retry-After": retry_after(decision.reset_at)},
            )


_write_limiter = _PerClientLimit("write", _WRITE_LIMIT, _WRITE_PERIOD, "requests")


async def write_rate_limit(request: Request) -> None:
    """FastAPI dependency: 20 mutation requests/minute per IP."""
    await _write_limiter.check(request)


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
_UPSTREAM_BUCKET = "upstream-budget"
_upstream_route_limiter = _PerClientLimit("upstream-lookups", _UPSTREAM_ROUTE_LIMIT, 60.0, "lookups")


async def upstream_route_limit(request: Request) -> None:
    """FastAPI dependency for routes that may fetch upstream."""
    await _upstream_route_limiter.check(request)


UpstreamRouteLimit = Annotated[None, Depends(upstream_route_limit)]


async def spend_upstream(calls: int) -> None:
    """Charge `calls` upstream requests to this hour's public budget, or
    raise 503 when they don't fit. Call only for cache misses.

    One budget for the whole backend, in the shared throttle store: kept per
    process, each API worker would spend its own full hour of the key. Async,
    with the store's own threads inside (throttle.run): a charge is a write
    to the store, and a caller handed a plain function could block the event
    loop on it."""
    if calls <= 0:
        return
    decision = await throttle.run(
        throttle.hit, _UPSTREAM_BUCKET, "all", limit=_UPSTREAM_CALLS_PER_HOUR, period=3600.0, cost=calls,
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=503,
            detail="Live lookups are paused for a few minutes; try again shortly.",
            headers={"Retry-After": retry_after(decision.reset_at)},
        )


def reset_upstream_budget() -> None:
    """For tests: clear the budget and the lookups limit."""
    throttle.clear(_UPSTREAM_BUCKET, _upstream_route_limiter.bucket)


# ── Public reads: the developer API and the site's own search ────
#
# One bucket for both: GET /api/public/v1/* and GET /api/explore, whose
# search runs the same hybrid engine as the public one. Shared so that
# the site's search can't be used to get past the public API's limit.
# The count is left on request.state for the public API's
# X-RateLimit-* response headers (api/public.py).
PUBLIC_READ_LIMIT = 60        # requests per minute per IP
_PUBLIC_READ_PERIOD = 60.0


async def public_read_limit(request: Request) -> None:
    """FastAPI dependency: PUBLIC_READ_LIMIT requests/minute per IP."""
    decision = await throttle.run(
        limit_client, client_ip(request), "public-api", limit=PUBLIC_READ_LIMIT, period=_PUBLIC_READ_PERIOD,
    )
    request.state.rl_remaining = decision.remaining
    request.state.rl_reset = decision.reset_at
    request.state.rl_counted = decision.counted
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {PUBLIC_READ_LIMIT} requests per minute per IP.",
            headers={
                "X-RateLimit-Limit": str(PUBLIC_READ_LIMIT),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(decision.reset_at),
                "Retry-After": retry_after(decision.reset_at),
                "Access-Control-Allow-Origin": "*",
            },
        )


PublicReadLimit = Annotated[None, Depends(public_read_limit)]
