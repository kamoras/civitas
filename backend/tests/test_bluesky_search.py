"""Tests for bluesky_search.search_posts — the new keyword/candidate-name
search capability (the platform previously only read fixed author feeds
and platform-wide trending topics, never searched by query)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.pipeline.fetch import bluesky_search


def _post(text, handle, uri, indexed_at=None):
    return SimpleNamespace(
        record=SimpleNamespace(text=text),
        author=SimpleNamespace(handle=handle),
        uri=uri,
        indexed_at=indexed_at,
    )


class TestSearchPosts:
    def test_no_credentials_returns_empty(self, monkeypatch):
        monkeypatch.setattr(bluesky_search.settings, "BSKY_HANDLE", "", raising=False)
        monkeypatch.setattr(bluesky_search.settings, "BSKY_APP_PASSWORD", "", raising=False)
        assert bluesky_search.search_posts("Ossoff") == []

    def test_returns_parsed_posts(self, monkeypatch):
        monkeypatch.setattr(bluesky_search.settings, "BSKY_HANDLE", "test.handle", raising=False)
        monkeypatch.setattr(bluesky_search.settings, "BSKY_APP_PASSWORD", "pw", raising=False)

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        fake_client = MagicMock()
        fake_client.app.bsky.feed.search_posts.return_value = SimpleNamespace(
            posts=[_post("Ossoff holds a narrow lead in early polling.", "apnews.com", "at://did:plc:abc/app.bsky.feed.post/xyz123", now)],
        )
        with patch("atproto.Client", return_value=fake_client):
            results = bluesky_search.search_posts("Ossoff")

        assert len(results) == 1
        assert results[0].text == "Ossoff holds a narrow lead in early polling."
        assert results[0].author_handle == "apnews.com"
        assert results[0].url == "https://bsky.app/profile/apnews.com/post/xyz123"

    def test_stale_posts_filtered_out(self, monkeypatch):
        monkeypatch.setattr(bluesky_search.settings, "BSKY_HANDLE", "test.handle", raising=False)
        monkeypatch.setattr(bluesky_search.settings, "BSKY_APP_PASSWORD", "pw", raising=False)

        stale = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat().replace("+00:00", "Z")
        fake_client = MagicMock()
        fake_client.app.bsky.feed.search_posts.return_value = SimpleNamespace(
            posts=[_post("Old post about the race.", "someone.bsky.social", "at://did:plc:abc/app.bsky.feed.post/old1", stale)],
        )
        with patch("atproto.Client", return_value=fake_client):
            results = bluesky_search.search_posts("some query")
        assert results == []

    def test_empty_text_skipped(self, monkeypatch):
        monkeypatch.setattr(bluesky_search.settings, "BSKY_HANDLE", "test.handle", raising=False)
        monkeypatch.setattr(bluesky_search.settings, "BSKY_APP_PASSWORD", "pw", raising=False)

        fake_client = MagicMock()
        fake_client.app.bsky.feed.search_posts.return_value = SimpleNamespace(
            posts=[_post("   ", "someone.bsky.social", "at://did:plc:abc/app.bsky.feed.post/empty")],
        )
        with patch("atproto.Client", return_value=fake_client):
            results = bluesky_search.search_posts("some query")
        assert results == []

    def test_login_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(bluesky_search.settings, "BSKY_HANDLE", "test.handle", raising=False)
        monkeypatch.setattr(bluesky_search.settings, "BSKY_APP_PASSWORD", "pw", raising=False)

        fake_client = MagicMock()
        fake_client.login.side_effect = Exception("bad creds")
        with patch("atproto.Client", return_value=fake_client):
            assert bluesky_search.search_posts("Ossoff") == []

    def test_search_call_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(bluesky_search.settings, "BSKY_HANDLE", "test.handle", raising=False)
        monkeypatch.setattr(bluesky_search.settings, "BSKY_APP_PASSWORD", "pw", raising=False)

        fake_client = MagicMock()
        fake_client.app.bsky.feed.search_posts.side_effect = Exception("rate limited")
        with patch("atproto.Client", return_value=fake_client):
            assert bluesky_search.search_posts("Ossoff") == []
