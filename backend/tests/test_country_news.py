"""/api/action/country-news fetches RSS live: a feed outage must not be
kept as "no countries" for a success's ten minutes."""

from unittest.mock import patch

from fastapi import Response

from app.api.action import get_country_news


async def test_an_outage_is_cached_only_briefly():
    with patch("app.pipeline.fetch.news_feeds.fetch_news_articles", return_value=[]):
        resp = await get_country_news(Response())
    assert resp.headers["Cache-Control"] == "public, max-age=30"


async def test_news_is_cached_for_ten_minutes():
    response = Response()
    from types import SimpleNamespace

    article = SimpleNamespace(
        title="Talks with Canada resume", summary="", url="https://example.org/a",
        source_name="Example", published=None,
    )
    with patch("app.pipeline.fetch.news_feeds.fetch_news_articles", return_value=[article]):
        body = await get_country_news(response)
    assert response.headers["Cache-Control"] == "public, max-age=600"
    assert [c["country"] for c in body["countries"]] == ["Canada"]
