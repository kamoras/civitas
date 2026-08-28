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


class DataVersionCacheMiddleware(BaseHTTPMiddleware):
    """Attach ETag/Cache-Control keyed to the pipeline's data version, and
    answer matching conditional requests with 304.

    2026-08 audit: this used to overwrite Cache-Control unconditionally,
    silently discarding any value a route handler set on purpose — e.g.
    action.py's recent-issues endpoint sets a deliberately short 30s
    max-age specifically because a longer one caused a real incident (a
    browser's stale cached response after a deploy added a field crashed
    the whole Action Center; see TestCacheHeaderMatchesNginx). Confirmed
    live via TestClient against the full app: that endpoint's real HTTP
    response carried this middleware's 300s value, not its own 30s — the
    route-level unit test only calls the handler function directly, so
    it could never have caught the override happening one layer up. ETag
    is still attached unconditionally (revalidation is a pure win
    regardless of a route's own freshness policy); Cache-Control here is
    only a default for routes that haven't chosen their own.
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
        if _if_none_match_matches(request.headers.get("if-none-match"), etag):
            return Response(
                status_code=304,
                headers={
                    "ETag": etag,
                    "Cache-Control": _cache_control(),
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
