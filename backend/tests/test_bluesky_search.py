"""Tests for bluesky_search.search_posts — keyword search against
Bluesky's PUBLIC AppView endpoint (no login, no atproto client: the read
path deliberately shares nothing with the posting account, so search
volume can never rate-limit the posting credentials — 2026-07 review B1).

Uses httpx.MockTransport so the real request/parse code runs end-to-end
against a canned payload, with no network.
"""

from datetime import datetime, timedelta, timezone

import httpx

from app.pipeline.fetch import bluesky_search


def _payload_post(text, handle, uri, indexed_at=None):
    post = {
        "record": {"text": text},
        "author": {"handle": handle},
        "uri": uri,
    }
    if indexed_at is not None:
        post["indexedAt"] = indexed_at
    return post


def _client_returning(posts):
    def handler(request):
        return httpx.Response(200, json={"posts": posts})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


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

    async def test_query_hits_public_endpoint_unauthenticated(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"posts": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await bluesky_search.search_posts(client, "Jon Ossoff")

        assert seen["url"].startswith(bluesky_search.PUBLIC_SEARCH_URL)
        assert "q=Jon+Ossoff" in seen["url"]
        assert seen["auth"] is None  # public AppView — never a session token

    async def test_request_failure_returns_empty(self):
        def handler(request):
            raise httpx.ConnectError("network down")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await bluesky_search.search_posts(client, "Ossoff") == []

    async def test_http_error_status_returns_empty(self):
        def handler(request):
            return httpx.Response(429, json={"error": "RateLimitExceeded"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await bluesky_search.search_posts(client, "Ossoff") == []

    async def test_malformed_json_returns_empty(self):
        def handler(request):
            return httpx.Response(200, content=b"not json")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
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
