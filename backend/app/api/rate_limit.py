"""Shared per-IP rate limiting for mutation endpoints (POST/DELETE).

Separate from public.py's read-only limiter so write endpoints can use a
tighter limit without coupling to the read-path code. Counted in the shared
throttle store (api/throttle.py), so the limit is per client, not per client
per API worker process.
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


async def client_key(request: Request) -> str:
    """The key per-client limits count under: the day's salted visitor hash
    of client_ip (api/visits.py), so no limiter stores an IP address."""
    from app.api.visits import _daily_salt, _visitor_hash
    from app.time_utils import utcnow

    salt = await _daily_salt(utcnow().date().isoformat())
    return _visitor_hash(client_ip(request), salt)


async def write_rate_limit(request: Request) -> None:
    """FastAPI dependency: 20 mutation requests/minute per IP."""
    key = await client_key(request)
    decision = await asyncio.to_thread(
        throttle.hit, "write", key, limit=_WRITE_LIMIT, period=_WRITE_PERIOD,
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded — {_WRITE_LIMIT} requests per minute per IP.",
            headers={"Retry-After": "60"},
        )


WriteRateLimit = Annotated[None, Depends(write_rate_limit)]
