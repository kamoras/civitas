"""Tests for run-ID-keyed HTTP caching.

The version token is the identity of the data: the newest completed run
across every pipeline. It must change exactly when the data changes —
earlier means pointless revalidation, later means yesterday's scores
served after tonight's run.
"""

from datetime import timedelta

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from app.api import cache_headers as ch
from app.models import HousePipelineRun, PipelineRun, StockTradesPipelineRun
from app.time_utils import utcnow


@pytest.fixture(autouse=True)
def clear_version_cache():
    ch.reset_version_cache()
    yield
    ch.reset_version_cache()


@pytest.fixture()
def client(monkeypatch):
    """A minimal app carrying only the middleware under test."""
    app = FastAPI()
    app.add_middleware(ch.DataVersionCacheMiddleware)

    @app.get("/api/senators")
    def senators():
        return {"ok": True}

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
        # deliberately short Cache-Control (e.g. because a longer one
        # caused a real staleness incident) rather than taking the
        # middleware's default.
        response.headers["Cache-Control"] = "public, max-age=30"
        return {"ok": True}

    @app.get("/api/action/timeline")
    def timeline(response: Response):
        # A real path from _ROUTE_CACHE_CONTROL_OVERRIDES — registered
        # here (not under /api/senators) specifically so the 304
        # short-circuit's override lookup, which matches on path, has
        # something real to match against.
        response.headers["Cache-Control"] = "public, max-age=300"
        return {"ok": True}

    monkeypatch.setattr(ch, "data_version", lambda: "run-v1")
    return TestClient(app)


# --- Version identity --------------------------------------------------

def test_version_reflects_the_newest_completed_run(db_session, monkeypatch):
    monkeypatch.setattr("app.database.SessionLocal", lambda: db_session)
    now = utcnow()

    db_session.add(PipelineRun(status="completed", completed_at=now))
    db_session.commit()
    first = ch._query_data_version()

    ch.reset_version_cache()
    db_session.add(PipelineRun(status="completed", completed_at=now + timedelta(hours=1)))
    db_session.commit()

    assert ch._query_data_version() != first


def test_version_covers_every_pipeline_not_just_senate(db_session, monkeypatch):
    """A Stock Trades run changes trade tables while leaving senator scores
    alone. A reader holding a cached trades page needs that to invalidate."""
    monkeypatch.setattr("app.database.SessionLocal", lambda: db_session)
    now = utcnow()

    db_session.add(PipelineRun(status="completed", completed_at=now))
    db_session.commit()
    before = ch._query_data_version()

    db_session.add(StockTradesPipelineRun(status="completed", completed_at=now))
    db_session.commit()
    assert ch._query_data_version() != before

    db_session.add(HousePipelineRun(status="completed", completed_at=now))
    db_session.commit()
    assert ch._query_data_version() != before


def test_incomplete_runs_do_not_move_the_version(db_session, monkeypatch):
    """A run in flight has not changed the served data yet. Letting it bump
    the version would invalidate every cache for the hours it runs."""
    monkeypatch.setattr("app.database.SessionLocal", lambda: db_session)

    db_session.add(PipelineRun(status="completed", completed_at=utcnow()))
    db_session.commit()
    before = ch._query_data_version()

    db_session.add(PipelineRun(status="running", completed_at=None))
    db_session.commit()
    assert ch._query_data_version() == before


def test_version_is_stable_before_any_run_completes(db_session, monkeypatch):
    """A timestamp here would change every second, making every response
    uncacheable AND defeating conditional requests."""
    monkeypatch.setattr("app.database.SessionLocal", lambda: db_session)
    assert ch._query_data_version() == "no-run"
    assert ch._query_data_version() == "no-run"


def test_version_is_memoised(monkeypatch):
    calls = []

    def _counted():
        calls.append(1)
        return "v1"

    monkeypatch.setattr(ch, "_query_data_version", _counted)
    assert ch.data_version() == "v1"
    assert ch.data_version() == "v1"
    assert len(calls) == 1


def test_version_failure_disables_caching_rather_than_erroring(monkeypatch):
    """Caching is an optimisation and must never be the reason a reader
    sees stale data — or an error."""
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(ch, "_query_data_version", _boom)
    assert ch.data_version() is None


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
    # 2026-08 audit (independent review, follow-up): the fix above only
    # covered the 200 path. The 304 short-circuit runs BEFORE the route,
    # so it can't read what the route would have set — it always emitted
    # this middleware's own (longer) default instead, reopening the exact
    # staleness bug via revalidation. /api/action/timeline's real literal
    # ("public, max-age=300", no stale-while-revalidate) is deliberately
    # distinct in string form from the middleware default despite sharing
    # the number 300, so this can't pass by coincidence.
    etag = client.get("/api/action/timeline").headers["ETag"]
    resp = client.get("/api/action/timeline", headers={"If-None-Match": etag})
    assert resp.status_code == 304
    assert resp.headers["Cache-Control"] == "public, max-age=300"
    assert resp.headers["Cache-Control"] != ch._cache_control()


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
    """A 404 cached under the data version would survive until the next
    run — long past whatever made it a 404."""
    resp = client.get("/api/senators/missing")
    assert resp.status_code == 404
    assert "ETag" not in resp.headers


def test_no_headers_when_the_version_is_unavailable(monkeypatch):
    app = FastAPI()
    app.add_middleware(ch.DataVersionCacheMiddleware)

    @app.get("/api/senators")
    def senators():
        return {"ok": True}

    monkeypatch.setattr(ch, "data_version", lambda: None)
    resp = TestClient(app).get("/api/senators")
    assert resp.status_code == 200
    assert "ETag" not in resp.headers


def test_existing_vary_header_is_preserved(monkeypatch):
    from starlette.responses import JSONResponse

    app = FastAPI()
    app.add_middleware(ch.DataVersionCacheMiddleware)

    @app.get("/api/senators")
    def senators():
        return JSONResponse({"ok": True}, headers={"Vary": "Origin"})

    monkeypatch.setattr(ch, "data_version", lambda: "run-v1")
    vary = TestClient(app).get("/api/senators").headers["Vary"]
    assert "Origin" in vary
    assert "Accept-Encoding" in vary


def test_a_changed_version_produces_a_different_etag():
    assert ch._etag_for("run-v1") != ch._etag_for("run-v2")


# --- Against the real app ----------------------------------------------

def test_middleware_is_mounted_on_the_real_app():
    """The tests above build a minimal app, so none of them would notice
    the middleware never being added in main.py."""
    from app.main import app

    assert any(
        m.cls is ch.DataVersionCacheMiddleware
        for m in app.user_middleware
    ), "DataVersionCacheMiddleware is not mounted"


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
    genuinely engages — the cache middleware must sit outside it, so a 304
    short-circuits before the compressor and the weak ETag stays valid
    whether or not the body below was compressed.
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
        monkeypatch.setattr(ch, "data_version", lambda: "run-v1")

        resp = real_client.get(
            "/api/explore/__cache_probe", headers={"Accept-Encoding": "gzip"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("Content-Encoding") == "gzip"
        etag = resp.headers.get("ETag")
        assert etag and etag.startswith('W/"')

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


def test_real_app_leaves_health_uncached(monkeypatch, real_client):
    monkeypatch.setattr(ch, "data_version", lambda: "run-v1")
    assert "ETag" not in real_client.get("/api/health").headers


class TestRouteOverrideRegistryMatchesLiveRoutes:
    """cache_headers._ROUTE_CACHE_CONTROL_OVERRIDES exists only so the 304
    short-circuit (which never runs the route, so can't read its response)
    can still answer with the *same* Cache-Control the route would have
    set on a 200. If a route's own literal ever changes without this table
    changing too, 304s silently go back to serving the middleware's longer
    default — reopening the exact staleness bug this table exists to
    close, undetected, because nothing else exercises this path. Calling
    each route's handler function directly (not through the full app,
    which has no DB schema in this environment) and reading the header it
    actually sets is the automated guardrail: no one has to remember to
    keep two places in sync, CI fails the moment they disagree.
    """

    @pytest.mark.asyncio
    async def test_every_override_matches_its_real_route(self, db_session):
        from app.api import action as action_api

        cases = [
            ("/api/action/issues", action_api.get_action_issues,
             dict(response=Response(), date=None, db=db_session, db_visits=db_session)),
            ("/api/action/issues/recent", action_api.get_recent_action_issues,
             dict(response=Response(), db=db_session)),
            ("/api/action/issues/some-id", action_api.get_action_issue,
             dict(issue_id="some-id", response=Response(), db=db_session)),
            ("/api/action/recent/senate", action_api.get_recent_by_branch,
             dict(response=Response(), branch="senate", db=db_session)),
            ("/api/action/country-news", action_api.get_country_news,
             dict(response=Response())),
            ("/api/action/my-reps", action_api.get_my_reps,
             dict(response=Response(), state="CA", db=db_session)),
            ("/api/action/open-comments", action_api.get_open_comments,
             dict(response=Response(), db=db_session)),
            ("/api/action/elections", action_api.get_election_info,
             dict(response=Response(), db=db_session)),
            ("/api/action/monitors", action_api.list_monitors,
             dict(response=Response(), db=db_session)),
            ("/api/action/monitors/some-slug", action_api.get_monitor,
             dict(response=Response(), slug="some-slug", db=db_session)),
            ("/api/action/timeline", action_api.get_timeline,
             dict(response=Response(), db=db_session)),
        ]

        for path, handler, kwargs in cases:
            response = kwargs["response"]
            try:
                result = handler(**kwargs)
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                # The route sets its Cache-Control header as its very
                # first statement, before touching the DB — anything
                # downstream failing on an empty test DB doesn't matter,
                # the header is already on `response` by then.
                pass

            live_value = response.headers.get("Cache-Control")
            table_value = ch._route_cache_control_override(path)
            assert live_value is not None, f"{path} never set its own Cache-Control"
            assert table_value == live_value, (
                f"{path}: override table says {table_value!r}, "
                f"route actually sets {live_value!r} — update "
                f"_ROUTE_CACHE_CONTROL_OVERRIDES in cache_headers.py"
            )
