"""GET /api/action/country-news fetches every RSS feed live on a miss.

nginx keys its cache on the path alone, and the answer is also held in
process: concurrent misses share one fetch. A feed outage (no articles at
all) is never kept as "no countries" for a success's ten minutes."""

import asyncio
from types import SimpleNamespace

from fastapi import Response

from app.api import action
from app.pipeline.fetch import news_feeds

_ARTICLE = SimpleNamespace(
    title="Talks with Canada resume", summary="", url="https://example.org/a",
    source_name="Example", published=None,
)


def _count_fetches(monkeypatch, articles=(_ARTICLE,)) -> list[int]:
    calls: list[int] = []

    def fake_fetch():
        calls.append(1)
        return list(articles)

    monkeypatch.setattr(news_feeds, "fetch_news_articles", fake_fetch)
    monkeypatch.setattr(action, "_country_news", None)
    return calls


async def test_news_is_cached_for_ten_minutes(monkeypatch):
    _count_fetches(monkeypatch)
    response = Response()
    body = await action.get_country_news(response)
    assert response.headers["Cache-Control"] == "public, max-age=600, stale-while-revalidate=600"
    assert [c["country"] for c in body["countries"]] == ["Canada"]


async def test_an_outage_is_cached_only_briefly_and_not_held(monkeypatch):
    calls = _count_fetches(monkeypatch, articles=())
    resp = await action.get_country_news(Response())
    assert resp.headers["Cache-Control"] == "public, max-age=30"
    await action.get_country_news(Response())
    assert len(calls) == 2  # asked again, not served the outage


async def test_repeat_requests_are_served_without_refetching(monkeypatch):
    calls = _count_fetches(monkeypatch)

    first = await action.get_country_news(Response())
    second = await action.get_country_news(Response())

    assert first == second
    assert len(calls) == 1


async def test_concurrent_misses_share_one_fetch(monkeypatch):
    calls = _count_fetches(monkeypatch)

    await asyncio.gather(*(action.get_country_news(Response()) for _ in range(5)))

    assert len(calls) == 1


async def test_the_answer_is_refetched_once_it_is_stale(monkeypatch):
    calls = _count_fetches(monkeypatch)
    await action.get_country_news(Response())

    stamped, payload = action._country_news
    monkeypatch.setattr(action, "_country_news", (stamped - action._COUNTRY_NEWS_TTL_S, payload))
    await action.get_country_news(Response())

    assert len(calls) == 2
