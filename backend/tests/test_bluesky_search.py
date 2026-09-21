"""Tests for bluesky_search.search_posts — keyword search against
Bluesky's AUTHENTICATED AppView (api.bsky.app).

Bluesky withdrew searchPosts from the public, unauthenticated AppView in
2026-09: public.api.bsky.app now answers 403 for that one endpoint even
when a valid Bearer token is sent, while every other endpoint on the same
host still answers 200. So the read path has to log in — and the
rate-limit hazard that shaped the original design (2026-07 review B1) is
what these tests exist to pin down: one session per PROCESS, reused
across a whole pass of ~50 searches, never one login per search.

Uses httpx.MockTransport so the real request/parse code runs end-to-end
against a canned payload, with no network.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.pipeline.fetch import bluesky_search


@pytest.fixture(autouse=True)
def _reset_session(monkeypatch):
    """The cached session is module state; leaking it across tests would
    let one test's failed login silently disable every later one."""
    monkeypatch.setattr(bluesky_search, "_session", {}, raising=False)
    monkeypatch.setattr(bluesky_search, "_auth_unavailable", None, raising=False)
    monkeypatch.setattr(bluesky_search.settings, "BSKY_HANDLE", "civitas.test")
    monkeypatch.setattr(bluesky_search.settings, "BSKY_APP_PASSWORD", "app-pw")
    yield
    bluesky_search._session.clear()
    bluesky_search._auth_unavailable = None


def _payload_post(text, handle, uri, indexed_at=None):
    post = {
        "record": {"text": text},
        "author": {"handle": handle},
        "uri": uri,
    }
    if indexed_at is not None:
        post["indexedAt"] = indexed_at
    return post


def _session_body(n=1):
    return {"accessJwt": f"access-{n}", "refreshJwt": f"refresh-{n}"}


def _routing_client(search_response, counters=None):
    """Answers createSession/refreshSession properly and hands every
    searchPosts call to `search_response(request)`."""
    counters = counters if counters is not None else {}

    def handler(request):
        url = str(request.url)
        if "createSession" in url:
            counters["login"] = counters.get("login", 0) + 1
            return httpx.Response(200, json=_session_body(counters["login"]))
        if "refreshSession" in url:
            counters["refresh"] = counters.get("refresh", 0) + 1
            return httpx.Response(200, json=_session_body(100 + counters["refresh"]))
        counters["search"] = counters.get("search", 0) + 1
        return search_response(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), counters


def _client_returning(posts):
    client, _ = _routing_client(lambda r: httpx.Response(200, json={"posts": posts}))
    return client


def _iso_z(dt):
    return dt.isoformat().replace("+00:00", "Z")


class TestSearchPosts:
    async def test_returns_parsed_posts(self):
        now = _iso_z(datetime.now(timezone.utc))
        client = _client_returning([_payload_post(
            "Ossoff holds a narrow lead in early polling.",
            "apnews.com", "at://did:plc:abc/app.bsky.feed.post/xyz123", now,
        )])
        async with client:
            results = await bluesky_search.search_posts(client, "Jon Ossoff")

        assert len(results) == 1
        assert results[0].text == "Ossoff holds a narrow lead in early polling."
        assert results[0].author_handle == "apnews.com"
        assert results[0].url == "https://bsky.app/profile/apnews.com/post/xyz123"
        assert results[0].published is not None

    async def test_query_hits_authenticated_appview(self):
        seen = {}

        def search(request):
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"posts": []})

        client, _ = _routing_client(search)
        async with client:
            await bluesky_search.search_posts(client, "Jon Ossoff")

        assert seen["url"].startswith(bluesky_search.SEARCH_URL)
        assert "api.bsky.app" in seen["url"]
        assert "q=Jon+Ossoff" in seen["url"]
        # public.api.bsky.app 403s this endpoint even WITH a token, so
        # sending one there would be a silent total outage, not a fallback.
        assert "public.api.bsky.app" not in seen["url"]
        assert seen["auth"] == "Bearer access-1"

    async def test_one_login_serves_a_whole_pass(self):
        """The 2026-07 review B1 guarantee, now actually pinned.

        A pass searches BLUESKY_SEARCH_BATCH (50) candidates. Logging in
        per search would be ~50 createSession calls against a
        ~30-per-5-min limit and would lock out the account the platform
        POSTS from — the reason search was unauthenticated originally.
        """
        client, counters = _routing_client(
            lambda r: httpx.Response(200, json={"posts": []}))
        async with client:
            for i in range(50):
                await bluesky_search.search_posts(client, f"Candidate {i}")

        assert counters["search"] == 50
        assert counters["login"] == 1

    async def test_concurrent_first_calls_do_not_stampede_logins(self):
        """Same hazard by a different route: N coroutines all finding an
        empty session cache and each starting its own login."""
        import asyncio

        client, counters = _routing_client(
            lambda r: httpx.Response(200, json={"posts": []}))
        async with client:
            await asyncio.gather(*[
                bluesky_search.search_posts(client, f"Candidate {i}")
                for i in range(12)
            ])

        assert counters["login"] == 1

    async def test_expired_token_refreshes_instead_of_logging_in_again(self):
        """An access token lasts ~2h and the process lives for days, so
        expiry mid-run is routine. refreshSession is not bound by the
        session-CREATE rate limit; a second login would be."""
        state = {"calls": 0}

        def search(request):
            state["calls"] += 1
            if state["calls"] == 1:
                return httpx.Response(401, json={"error": "ExpiredToken"})
            return httpx.Response(200, json={"posts": [_payload_post(
                "A fresh post.", "someone.bsky.social",
                "at://did:plc:abc/app.bsky.feed.post/ok",
            )]})

        client, counters = _routing_client(search)
        async with client:
            results = await bluesky_search.search_posts(client, "Ossoff")

        assert len(results) == 1, "should retry after refreshing, not give up"
        assert counters["refresh"] == 1
        assert counters["login"] == 1  # the initial one only

    async def test_missing_credentials_reports_once_and_stays_unavailable(self):
        """Without credentials the source is UNAVAILABLE, which is not the
        same as a candidate having no coverage — and it must not turn into
        50 doomed requests per run."""
        bluesky_search.settings.BSKY_HANDLE = ""
        client, counters = _routing_client(
            lambda r: httpx.Response(200, json={"posts": []}))
        async with client:
            for i in range(5):
                assert await bluesky_search.search_posts(client, f"C{i}") == []

        assert counters.get("login", 0) == 0
        assert counters.get("search", 0) == 0
        assert bluesky_search.search_is_available() is False

    async def test_rejected_login_stops_further_attempts(self):
        def handler(request):
            if "createSession" in str(request.url):
                return httpx.Response(401, json={"error": "AuthFactorTokenRequired"})
            raise AssertionError("must not search without a session")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            for i in range(5):
                assert await bluesky_search.search_posts(client, f"C{i}") == []

        assert bluesky_search.search_is_available() is False

    async def test_available_while_healthy(self):
        client = _client_returning([])
        async with client:
            await bluesky_search.search_posts(client, "Ossoff")
        assert bluesky_search.search_is_available() is True

    async def test_request_failure_returns_empty(self):
        def search(request):
            raise httpx.ConnectError("network down")

        client, _ = _routing_client(search)
        async with client:
            assert await bluesky_search.search_posts(client, "Ossoff") == []

    async def test_http_error_status_returns_empty(self):
        client, _ = _routing_client(
            lambda r: httpx.Response(429, json={"error": "RateLimitExceeded"}))
        async with client:
            assert await bluesky_search.search_posts(client, "Ossoff") == []

    async def test_malformed_json_returns_empty(self):
        client, _ = _routing_client(lambda r: httpx.Response(200, content=b"not json"))
        async with client:
            assert await bluesky_search.search_posts(client, "Ossoff") == []

    async def test_stale_posts_filtered_out(self):
        stale = _iso_z(datetime.now(timezone.utc) - timedelta(hours=200))
        client = _client_returning([_payload_post(
            "Old post about the race.", "someone.bsky.social",
            "at://did:plc:abc/app.bsky.feed.post/old1", stale,
        )])
        async with client:
            assert await bluesky_search.search_posts(client, "some query") == []

    async def test_missing_indexed_at_is_kept_with_null_published(self):
        """"Timestamp unknown" must not be treated as "stale": the AppView
        isn't contractually required to populate indexedAt, and dropping on
        it would silently discard valid results. published stays None so
        downstream stores NULL, never a guessed time."""
        client = _client_returning([_payload_post(
            "Fresh post, no timestamp field.", "someone.bsky.social",
            "at://did:plc:abc/app.bsky.feed.post/nots",
        )])
        async with client:
            results = await bluesky_search.search_posts(client, "some query")

        assert len(results) == 1
        assert results[0].published is None

    async def test_unparseable_indexed_at_is_kept(self):
        client = _client_returning([_payload_post(
            "Fresh post, garbage timestamp.", "someone.bsky.social",
            "at://did:plc:abc/app.bsky.feed.post/badts", "not-a-date",
        )])
        async with client:
            results = await bluesky_search.search_posts(client, "some query")

        assert len(results) == 1
        assert results[0].published is None

    async def test_empty_text_skipped(self):
        client = _client_returning([_payload_post(
            "   ", "someone.bsky.social", "at://did:plc:abc/app.bsky.feed.post/empty",
        )])
        async with client:
            assert await bluesky_search.search_posts(client, "some query") == []
