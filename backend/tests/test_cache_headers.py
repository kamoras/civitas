"""Tests for content-keyed HTTP caching.

The ETag is derived from the response body, so it changes exactly when
what a reader would see changes — whatever wrote the data. It used to be
the newest nightly run's identity, and every hourly or on-demand write
between runs was then answered 304 with the old body.
"""

import gzip
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from app.api import cache_headers as ch

_STATE = {"body": {"ok": True}}


@pytest.fixture()
def client():
    """A minimal app carrying only the middleware under test."""
    _STATE["body"] = {"ok": True}
    app = FastAPI()
    app.add_middleware(ch.ETagCacheMiddleware)

    @app.get("/api/senators")
    def senators():
        return _STATE["body"]

    @app.get("/api/admin/dashboard")
    def admin():
        return {"ok": True}

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/senators/missing")
    def missing():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="nope")

    @app.post("/api/senators")
    def create():
        return {"ok": True}

    @app.get("/api/senators/short-cache")
    def short_cache(response: Response):
        # Mirrors action.py's pattern: a route that sets its own
        # deliberately short Cache-Control rather than taking the
        # middleware's default.
        response.headers["Cache-Control"] = "public, max-age=30"
        return {"ok": True}

    return TestClient(app)


# --- Content identity --------------------------------------------------

def test_a_changed_body_is_never_answered_304(client):
    """The bug this design fixes: an hourly Action Center write (or a bill
    refresh, a ballot sync, a startup rescore) changed the body under an
    unchanged run-keyed ETag, so a revalidating browser kept the old body
    until the next nightly run finished."""
    etag = client.get("/api/senators").headers["ETag"]
    _STATE["body"] = {"ok": False, "story": "new"}
    resp = client.get("/api/senators", headers={"If-None-Match": etag})
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "story": "new"}
    assert resp.headers["ETag"] != etag


def test_an_unchanged_body_keeps_its_etag(client):
    assert client.get("/api/senators").headers["ETag"] == client.get("/api/senators").headers["ETag"]


# --- Header behaviour --------------------------------------------------

def test_cacheable_endpoint_gets_etag_and_cache_control(client):
    resp = client.get("/api/senators")
    assert resp.status_code == 200
    assert resp.headers["ETag"].startswith('W/"')
    assert "max-age=300" in resp.headers["Cache-Control"]
    assert "stale-while-revalidate=3600" in resp.headers["Cache-Control"]
    assert "Accept-Encoding" in resp.headers["Vary"]


def test_middleware_does_not_override_a_route_own_cache_control(client):
    # 2026-08 audit: this used to overwrite Cache-Control unconditionally,
    # silently discarding a route's own deliberately short value (real
    # example: action.py's recent-issues endpoint, whose short TTL exists
    # specifically because a longer one caused a real staleness incident).
    # Confirmed live via TestClient against the full app before this fix —
    # the route's 30s value never reached the actual HTTP response.
    resp = client.get("/api/senators/short-cache")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "public, max-age=30"
    # The ETag/revalidation benefit still applies regardless — a route's
    # own freshness policy and the shared conditional-GET machinery are
    # independent concerns.
    assert resp.headers["ETag"].startswith('W/"')
    assert "Accept-Encoding" in resp.headers["Vary"]


def test_matching_conditional_request_also_respects_route_own_cache_control(client):
    # A 304 used to be answered before the route ran, and so carried the
    # middleware's longer default instead of the route's own value unless
    # a hand-kept registry mirrored it. It is built after the route now.
    etag = client.get("/api/senators/short-cache").headers["ETag"]
    resp = client.get("/api/senators/short-cache", headers={"If-None-Match": etag})
    assert resp.status_code == 304
    assert resp.headers["Cache-Control"] == "public, max-age=30"


def test_matching_conditional_request_gets_304_with_no_body(client):
    etag = client.get("/api/senators").headers["ETag"]
    resp = client.get("/api/senators", headers={"If-None-Match": etag})

    assert resp.status_code == 304
    assert resp.content == b""
    assert resp.headers["ETag"] == etag


def test_stale_conditional_request_gets_a_fresh_body(client):
    resp = client.get("/api/senators", headers={"If-None-Match": 'W/"stale"'})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_wildcard_if_none_match_matches(client):
    resp = client.get("/api/senators", headers={"If-None-Match": "*"})
    assert resp.status_code == 304


def test_weak_and_strong_forms_both_match(client):
    etag = client.get("/api/senators").headers["ETag"]
    bare = etag[2:]  # drop the W/ prefix
    assert client.get("/api/senators", headers={"If-None-Match": bare}).status_code == 304


def test_multiple_validators_are_all_considered(client):
    """RFC 9110 allows a client to send a list."""
    etag = client.get("/api/senators").headers["ETag"]
    resp = client.get("/api/senators", headers={"If-None-Match": f'W/"other", {etag}'})
    assert resp.status_code == 304


# --- Scope -------------------------------------------------------------

def test_admin_endpoints_are_never_cached(client):
    """Authenticated and per-token — a shared cache entry would be a
    cross-tenant leak, not just a staleness bug."""
    assert "ETag" not in client.get("/api/admin/dashboard").headers


def test_health_is_never_cached(client):
    """Liveness answered from a cache is not liveness."""
    assert "ETag" not in client.get("/api/health").headers


def test_non_get_requests_are_untouched(client):
    assert "ETag" not in client.post("/api/senators").headers


def test_error_responses_are_not_cached(client):
    """A 404 must never be revalidated into a 304."""
    resp = client.get("/api/senators/missing")
    assert resp.status_code == 404
    assert "ETag" not in resp.headers


def test_existing_vary_header_is_preserved(monkeypatch):
    from starlette.responses import JSONResponse

    app = FastAPI()
    app.add_middleware(ch.ETagCacheMiddleware)

    @app.get("/api/senators")
    def senators():
        return JSONResponse({"ok": True}, headers={"Vary": "Origin"})

    vary = TestClient(app).get("/api/senators").headers["Vary"]
    assert "Origin" in vary
    assert "Accept-Encoding" in vary


def test_a_changed_body_produces_a_different_etag():
    assert ch._etag_for(b"v1") != ch._etag_for(b"v2")


# --- Against the real app ----------------------------------------------

def test_middleware_is_mounted_on_the_real_app():
    """The tests above build a minimal app, so none of them would notice
    the middleware never being added in main.py."""
    from app.main import app

    assert any(
        m.cls is ch.ETagCacheMiddleware
        for m in app.user_middleware
    ), "ETagCacheMiddleware is not mounted"


@pytest.fixture(scope="module")
def real_client():
    """The real app's lifespan for real: init_db, the scheduler, the
    embedding-model preload thread, the visit consumer, the explore-index
    bootstrap — all of it.

    Module-scoped and shared by every "real app" test below rather than
    each test opening its own `with TestClient(app)`. That lifespan is
    heavyweight enough (spawns its own background threads, some touching
    native extensions — torch, scipy, sqlite-vec) that cycling it more
    than once per process is not just slow, it segfaulted here: two
    separate start/stop cycles in the same pytest session reliably
    crashed the interpreter, most likely a native-thread-teardown race
    in the embedding-model preload rather than anything in the cache
    middleware itself. One real lifespan per session, entered once,
    reused by every test that needs it, sidesteps the whole class of
    problem — the same reason nothing else in this suite does this
    per-test.
    """
    from app.main import app

    with TestClient(app) as client:
        yield client


def test_real_app_emits_headers_through_the_gzip_stack(monkeypatch, real_client):
    """Ordering check against the *real* middleware stack.

    A probe route rather than a live endpoint: the app's own engine has no
    schema in this environment, and a 500 from a missing table would tell
    us nothing about middleware ordering, which is the thing under test.
    The body is padded past GZipMiddleware's 500-byte floor so compression
    genuinely engages — the cache middleware must sit inside it, hashing
    the uncompressed body, because gzip output carries a timestamp and
    changes every second.
    """
    app = real_client.app

    @app.get("/api/explore/__cache_probe")
    def _probe():
        return {"padding": "x" * 2000}

    # `app` is a module-level object shared by every test in the session,
    # so the probe is removed in a finally — a failed assertion must not
    # leave a stray route mounted for whatever runs next.
    original_routes = list(app.router.routes)
    try:
        # Move the probe to the front: the explore router registered a
        # path-param route first, which would otherwise match
        # "__cache_probe" and 422 on it.
        app.router.routes.insert(0, app.router.routes.pop())

        resp = real_client.get(
            "/api/explore/__cache_probe", headers={"Accept-Encoding": "gzip"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("Content-Encoding") == "gzip"
        etag = resp.headers.get("ETag")
        assert etag and etag.startswith('W/"')

        # gzip writes the current time into its header. A second later the
        # compressed bytes differ, and an ETag hashed from them would too:
        # move gzip's clock forward so the revalidation always crosses one.
        later = time.time() + 60
        monkeypatch.setattr(gzip, "time", SimpleNamespace(time=lambda: later))
        conditional = real_client.get(
            "/api/explore/__cache_probe",
            headers={"If-None-Match": etag, "Accept-Encoding": "gzip"},
        )
        assert conditional.status_code == 304
        assert conditional.content == b""
        # A 304 must not claim a compressed body it does not have.
        assert "Content-Encoding" not in conditional.headers
    finally:
        app.router.routes[:] = original_routes


def test_real_app_leaves_health_uncached(real_client):
    assert "ETag" not in real_client.get("/api/health").headers


def test_real_app_responses_vary_on_origin(real_client):
    """CORS here is an explicit origin list, so Access-Control-Allow-Origin
    echoes the caller and the response genuinely depends on Origin. nginx's
    proxy_cache honours Vary, so every response must say so — including one
    to a request with no Origin, which is what gets cached for same-origin
    and SSR traffic. Before starlette 1.7 that response carried no
    `Vary: Origin`, and nginx could hand it (with no ACAO) to a
    cross-origin caller, whose browser then blocked it."""
    from app.main import _cors_origins

    plain = real_client.get("/api/health")
    assert "Origin" in plain.headers["Vary"]
    assert "access-control-allow-origin" not in plain.headers

    allowed = _cors_origins[0]
    cross = real_client.get("/api/health", headers={"Origin": allowed})
    assert "Origin" in cross.headers["Vary"]
    assert cross.headers["access-control-allow-origin"] == allowed


def test_real_app_action_issues_revalidate_after_an_hourly_write(db_session, monkeypatch):
    """End to end on the real app: the Action Center's hourly refresh
    replaces the day's story without any nightly run completing. A browser
    revalidating must get the new story, not a 304."""
    from app.database import get_db
    from app.main import app
    from app.models import ActionIssue

    def issue(title):
        return ActionIssue(date="2026-09-27", rank=1, title=title, summary="s", facts="[]", actions="[]",
                           source_urls="[]", source_names="[]", policy_areas="[]", is_current=True)

    db_session.add(issue("Old story"))
    db_session.commit()
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        etag = client.get("/api/action/issues").headers["ETag"]
        for row in db_session.query(ActionIssue):
            row.is_current = False
        db_session.add(issue("New story"))
        db_session.commit()
        resp = client.get("/api/action/issues", headers={"If-None-Match": etag})
        assert resp.status_code == 200
        assert "New story" in resp.text
    finally:
        app.dependency_overrides.clear()
