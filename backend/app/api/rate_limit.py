"""Shared per-IP rate limiting for mutation endpoints (POST/DELETE), and
for the public routes that spend the shared api.data.gov key.

Separate from public.py's read-only limiter so write endpoints can use a
tighter limit without coupling to the read-path code. Every limit here is
counted in the throttle store every API worker process shares
(api/throttle.py), so it holds per client — not per client per worker —
and the hourly upstream budget holds for the whole backend.
"""

import asyncio
import ipaddress
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


# Bits of the HMAC a throttle key keeps. The day's salt sits in the same
# database as the throttle rows, and the IPv4 space is small enough to
# enumerate, so a full-length key would let anyone holding that file during
# the day recover which address voted on which issue. 24 bits leaves each
# key matching ~256 IPv4 addresses (2^32 / 2^24), so no row names one. The
# price is shared buckets: two real visitors land on the same key with
# probability ~n^2 / 2^25 per issue a day (0.3% of one vote at n = 300).
_KEY_BITS = 24


async def client_key(request: Request, purpose: str, scope: str = "") -> str:
    """The key a per-client limit counts under: a truncated HMAC of
    client_ip under the day's visit salt (api/visits.py), tagged with
    `purpose` and `scope`. Never the IP itself, never the visitor hash
    SiteVisit stores — so a throttle row can't be joined to a visit
    (AGENTS.md §8) — and too short to single out an address (_KEY_BITS).
    Rows for different purposes or scopes (one visitor's votes on two
    issues) can't be joined to each other."""
    import hashlib
    import hmac

    from app.api.visits import _daily_salt
    from app.time_utils import utcnow

    salt = await _daily_salt(utcnow().date().isoformat())
    message = f"{purpose}\x00{client_ip(request)}\x00{scope}".encode()
    return hmac.new(salt, message, hashlib.sha256).hexdigest()[: _KEY_BITS // 4]


class _PerClientLimit:
    """At most `limit` requests per `period` seconds per client."""

    def __init__(self, bucket: str, limit: int, period: float, what: str):
        self.bucket, self.limit, self.period, self.what = bucket, limit, period, what

    async def check(self, request: Request) -> None:
        key = await client_key(request, self.bucket)
        decision = await asyncio.to_thread(
            throttle.hit, self.bucket, key, limit=self.limit, period=self.period,
        )
        if not decision.allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded — {self.limit} {self.what} per minute per IP.",
                headers={"Retry-After": str(int(self.period))},
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


def spend_upstream(calls: int) -> None:
    """Charge `calls` upstream requests to this hour's public budget, or
    raise 503 when they don't fit. Call only for cache misses.

    One budget for the whole backend, in the shared throttle store: kept per
    process, each API worker would spend its own full hour of the key."""
    if calls <= 0:
        return
    decision = throttle.hit(
        _UPSTREAM_BUCKET, "all", limit=_UPSTREAM_CALLS_PER_HOUR, period=3600.0, cost=calls,
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=503,
            detail="Live lookups are paused for a few minutes; try again shortly.",
            headers={"Retry-After": "600"},
        )


def reset_upstream_budget() -> None:
    """For tests: clear the budget and the lookups limit."""
    throttle.clear(_UPSTREAM_BUCKET, _upstream_route_limiter.bucket)
