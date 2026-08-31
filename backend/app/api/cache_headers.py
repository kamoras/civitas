"""Run-ID-keyed HTTP caching for the read-only API.

Civitas serves read-mostly data that changes once a day, when the nightly
pipeline finishes. That is the most cacheable shape a web application can
have, and until now every request went all the way to SQLite on the Pi
regardless — so traffic growth translated directly into load on the one
box that also has to run the pipeline.

The version token is the identity of the data itself: the newest
completed pipeline run across every pipeline. It changes exactly when the
data changes, which means:

  * a CDN or browser can hold a response until the next run lands, with
    no guessing at a TTL and no serving of stale scorecards afterwards;
  * a conditional request that still matches gets a 304 with no body,
    which costs a version lookup instead of a query and a serialization.

Deliberately *not* time-based. A fixed max-age either expires early
(pointless revalidation all day) or expires late (yesterday's scores
served after tonight's run). Keying on the run identity has neither
failure mode.

Scope is restricted to GET requests on public read endpoints. Admin,
health, pipeline control, feedback, and visit tracking are excluded — see
CACHEABLE_PREFIXES for why each.
"""

import logging
import re
import threading
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)

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
)

# Excluded and why:
#   /api/admin      — authenticated, per-token, and mutates
#   /api/health     — liveness must never be answered from a cache
#   /api/pipeline   — run status changes continuously during a run
#   /api/feedback   — POST only
#   /api/visits     — per-visitor by definition
#   /api/qa         — question-specific; would need the query in the key

# How long a client may reuse a response without revalidating. Well under
# the ~24h gap between runs, so a client that fetched just before a run
# lands is stale for minutes rather than most of a day. The ETag is what
# provides correctness; this only controls how often revalidation happens.
MAX_AGE_S = 300

# A CDN may keep serving the old body this much longer while it fetches a
# fresh one in the background. This is where the real origin-offload comes
# from: a run landing does not produce a thundering herd against the Pi.
STALE_WHILE_REVALIDATE_S = 3600

# The version lookup is a DB query, so it is memoised. Short enough that a
# finishing run becomes visible promptly, long enough that a burst of
# traffic does not turn into a burst of queries.
_VERSION_TTL_S = 30.0

_lock = threading.Lock()
_cached_version: tuple[str, float] | None = None


def _query_data_version() -> str:
    """Identity of the newest completed run across every pipeline.

    All five are considered because they write different parts of the
    site: a Stock Trades run changes trade tables while leaving senator
    scores alone, and a reader holding a cached trades page needs that to
    invalidate too.
    """
    from app.database import SessionLocal
    from app.models import (
        ElectionPipelineRun,
        HousePipelineRun,
        PipelineRun,
        StockTradesPipelineRun,
        SupplementaryPipelineRun,
    )

    models = (
        PipelineRun, HousePipelineRun, SupplementaryPipelineRun,
        StockTradesPipelineRun, ElectionPipelineRun,
    )
    db = SessionLocal()
    try:
        stamps = []
        for model in models:
            row = (
                db.query(model)
                .filter(model.completed_at.isnot(None))
                .order_by(model.completed_at.desc())
                .first()
            )
            if row is not None:
                stamps.append(f"{model.__tablename__}:{row.id}:{row.completed_at.isoformat()}")
        if not stamps:
            # Nothing has ever completed. Return a constant rather than a
            # timestamp: a value that changed every second would make every
            # response uncacheable *and* defeat conditional requests.
            return "no-run"
        return "|".join(sorted(stamps))
    finally:
        db.close()


def data_version() -> str | None:
    """Memoised data version, or None if it cannot be determined.

    None means no cache headers are emitted at all — correct behaviour
    when we cannot tell whether the data changed. Caching is an
    optimisation and must never be the reason a reader sees stale data.
    """
    global _cached_version
    now = time.monotonic()
    with _lock:
        if _cached_version is not None and _cached_version[1] > now:
            return _cached_version[0]

    try:
        version = _query_data_version()
    except Exception:
        logger.warning("Could not determine data version — skipping cache headers", exc_info=True)
        return None

    with _lock:
        _cached_version = (version, now + _VERSION_TTL_S)
    return version


def reset_version_cache() -> None:
    """Drop the memoised version. Tests only."""
    global _cached_version
    with _lock:
        _cached_version = None


def _etag_for(version: str) -> str:
    import hashlib

    digest = hashlib.sha256(version.encode("utf-8")).hexdigest()[:32]
    # Weak validator: GZipMiddleware may or may not have compressed the
    # body, and a strong ETag would be claiming byte equality across both.
    return f'W/"{digest}"'


def _is_cacheable_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in CACHEABLE_PREFIXES)


# Routes whose Cache-Control differs from this middleware's own default
# (see action.py's per-route "public, max-age=N" lines). Only needed for
# DataVersionCacheMiddleware's 304 short-circuit, which runs before the
# route handler and so has no response to read a value off of — the 200
# path just reads whatever the route already set. Values here MUST match
# the corresponding route's own header exactly; test_cache_headers.py's
# TestRouteOverrideRegistryMatchesLiveRoutes enforces that automatically
# by hitting each pattern's real endpoint and diffing its 200 response
# against this table, so a value edited in one place and not the other
# fails CI instead of silently drifting the way this whole bug started.
_ROUTE_CACHE_CONTROL_OVERRIDES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^/api/action/issues(/.*)?$"), "public, max-age=30"),
    (re.compile(r"^/api/action/recent/[^/]+$"), "public, max-age=120"),
    (re.compile(r"^/api/action/country-news$"), "public, max-age=600"),
    (re.compile(r"^/api/action/my-reps$"), "public, max-age=300"),
    (re.compile(r"^/api/action/open-comments$"), "public, max-age=3600"),
    (re.compile(r"^/api/action/elections$"), "public, max-age=3600"),
    (re.compile(r"^/api/action/monitors(/.*)?$"), "public, max-age=300"),
    (re.compile(r"^/api/action/timeline$"), "public, max-age=300"),
]


def _route_cache_control_override(path: str) -> str | None:
    for pattern, value in _ROUTE_CACHE_CONTROL_OVERRIDES:
        if pattern.match(path):
            return value
    return None


class DataVersionCacheMiddleware(BaseHTTPMiddleware):
    """Attach ETag/Cache-Control keyed to the pipeline's data version, and
    answer matching conditional requests with 304.

    Must never overwrite Cache-Control unconditionally — that would
    silently discard any value a route handler set on purpose. E.g.
    action.py's recent-issues endpoint sets a deliberately short 30s
    max-age, because a longer one lets a browser serve a stale cached
    response after a deploy changes the response shape (see
    TestCacheHeaderMatchesNginx). A route-level unit test that only calls
    the handler function directly can't catch this middleware overriding
    the header one layer up — verify via TestClient against the full app
    instead. ETag is still attached unconditionally (revalidation is a
    pure win regardless of a route's own freshness policy); Cache-Control
    here is only a default for routes that haven't chosen their own.
    """

    async def dispatch(self, request, call_next):
        if request.method != "GET" or not _is_cacheable_path(request.url.path):
            return await call_next(request)

        version = data_version()
        if version is None:
            return await call_next(request)

        etag = _etag_for(version)

        # Short-circuit before doing any query work. This is the whole
        # point: a revalidation costs a memoised string comparison.
        #
        # The 200 path below can just read a route's own Cache-Control off
        # the real response, because the route actually ran. This path
        # can't — running the route to find out would defeat the entire
        # optimization. So a route with its own shorter TTL (see
        # _ROUTE_CACHE_CONTROL_OVERRIDES) needs a second lookup here, or
        # 304s silently hand it back this middleware's longer default,
        # re-extending a client's cache lifetime on every revalidation —
        # the same staleness bug the 200 path guards against, reopened
        # through this response path instead. test_cache_headers.py
        # verifies every override's 304 value against its real route.
        if _if_none_match_matches(request.headers.get("if-none-match"), etag):
            override = _route_cache_control_override(request.url.path)
            return Response(
                status_code=304,
                headers={
                    "ETag": etag,
                    "Cache-Control": override if override is not None else _cache_control(),
                    "Vary": "Accept-Encoding",
                },
            )

        response = await call_next(request)
        # Only successful, complete responses. A 404 or a 500 must not be
        # cached under the data version — the next run would not clear it.
        if response.status_code == 200:
            response.headers["ETag"] = etag
            if "Cache-Control" not in response.headers:
                response.headers["Cache-Control"] = _cache_control()
            existing_vary = response.headers.get("Vary")
            if existing_vary:
                if "accept-encoding" not in existing_vary.lower():
                    response.headers["Vary"] = f"{existing_vary}, Accept-Encoding"
            else:
                response.headers["Vary"] = "Accept-Encoding"
        return response


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
