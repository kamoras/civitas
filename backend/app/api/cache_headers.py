"""Content-keyed HTTP caching for the read-only API.

Every successful GET on a public read endpoint carries an ETag computed
from the response body, and a conditional request whose ETag still matches
gets a 304 with no body. A browser or CDN can then revalidate a response
for the cost of running the route, without re-sending it.

The ETag used to be the identity of the newest completed nightly pipeline
run. That was only right while all of the data changed with those runs, and
it no longer does: the Action Center refreshes hourly, bill statuses every
45 minutes, ballots and election coverage on their own schedules, holdings
and the congress record in their own jobs, and startup rescores and admin
edits between runs. Each of those changed the body under an unchanged ETag,
so a browser revalidating got a 304 and kept the old body until the next
nightly run finished — a returning reader saw the Action Center as it was
when they first opened it that day. Hashing the body can't go stale that
way, whatever writes the data next.

The 304 is built after the route has run, so it carries the route's own
Cache-Control; nothing has to mirror a route's freshness policy by hand.

Scope is restricted to GET requests on public read endpoints. Admin,
health, pipeline control, feedback, and visit tracking are excluded — see
CACHEABLE_PREFIXES for why each.
"""

import hashlib

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# Only these prefixes get cache headers. An allowlist, not a denylist:
# a new endpoint should have to opt in after someone has thought about
# whether its response is the same for every caller.
CACHEABLE_PREFIXES = (
    "/api/senators",
    "/api/representatives",
    "/api/presidents",
    "/api/justices",
    "/api/politicians",
    "/api/bills",
    "/api/elections",
    "/api/explore",
    "/api/action",
    "/api/highlights",
    "/api/public/",
    "/api/congress",
)

# Excluded and why:
#   /api/admin      — authenticated, per-token, and mutates
#   /api/health     — liveness must never be answered from a cache
#   /api/pipeline   — run status changes continuously during a run
#   /api/feedback   — POST only
#   /api/visits     — per-visitor by definition
#   /api/qa         — question-specific; would need the query in the key

# How long a client may reuse a response without revalidating, for a route
# that hasn't set its own. Revalidation is what keeps it correct; this only
# controls how often it happens.
MAX_AGE_S = 300

# A CDN may keep serving the old body this much longer while it fetches a
# fresh one in the background.
STALE_WHILE_REVALIDATE_S = 3600


def _etag_for(body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()[:32]
    # Weak validator: the body hashed is the uncompressed one (this runs
    # inside GZipMiddleware), so it claims equivalence of the
    # representation, not byte equality across encodings.
    return f'W/"{digest}"'


def _is_cacheable_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in CACHEABLE_PREFIXES)


class ETagCacheMiddleware(BaseHTTPMiddleware):
    """Attach a body-derived ETag (and a default Cache-Control) to
    successful public GETs, and answer a matching conditional request with
    304.

    Never overwrites a Cache-Control the route set on purpose — e.g.
    action.py's recent-issues endpoint keeps a short 30s max-age so a
    browser doesn't serve a stale shape after a deploy.
    """

    async def dispatch(self, request, call_next):
        if request.method != "GET" or not _is_cacheable_path(request.url.path):
            return await call_next(request)

        response = await call_next(request)
        # Only successful, complete responses. A 404 or a 500 must not be
        # revalidated into a 304. An event stream is never buffered.
        if response.status_code != 200 or "text/event-stream" in response.headers.get("content-type", ""):
            return response

        body = b"".join([chunk async for chunk in response.body_iterator])
        etag = _etag_for(body)
        cache_control = response.headers.get("Cache-Control") or _cache_control()
        vary = response.headers.get("Vary")
        if not vary:
            vary = "Accept-Encoding"
        elif "accept-encoding" not in vary.lower():
            vary = f"{vary}, Accept-Encoding"

        if _if_none_match_matches(request.headers.get("if-none-match"), etag):
            return Response(
                status_code=304,
                headers={"ETag": etag, "Cache-Control": cache_control, "Vary": vary},
            )

        fresh = Response(content=body, status_code=200)
        fresh.raw_headers = list(response.raw_headers)
        fresh.headers["ETag"] = etag
        fresh.headers["Cache-Control"] = cache_control
        fresh.headers["Vary"] = vary
        return fresh


def _cache_control() -> str:
    return (
        f"public, max-age={MAX_AGE_S}, "
        f"stale-while-revalidate={STALE_WHILE_REVALIDATE_S}"
    )


def _if_none_match_matches(header: str | None, etag: str) -> bool:
    """RFC 9110 If-None-Match comparison, weak semantics.

    A client may send several validators, and `*` matches anything it
    holds. Weak comparison ignores the W/ prefix, so a client echoing
    back either form matches.
    """
    if not header:
        return False
    candidates = [c.strip() for c in header.split(",") if c.strip()]
    if "*" in candidates:
        return True
    normalized = etag[2:] if etag.startswith("W/") else etag
    for candidate in candidates:
        bare = candidate[2:] if candidate.startswith("W/") else candidate
        if bare == normalized:
            return True
    return False
