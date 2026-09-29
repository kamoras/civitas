"""GET /api/action/country-news fetches every RSS feed live on a miss.

nginx's cache for it was keyed on the query string, so `?anything` reached
the backend every time; the answer is also held in process, and concurrent
misses share one fetch."""

import asyncio

from fastapi import Response

from app.api import action
from app.pipeline.fetch import news_feeds


def _count_fetches(monkeypatch) -> list[int]:
    calls: list[int] = []

    def fake_fetch():
        calls.append(1)
        return []

    monkeypatch.setattr(news_feeds, "fetch_news_articles", fake_fetch)
    monkeypatch.setattr(action, "_country_news", None)
    return calls


async def test_repeat_requests_are_served_without_refetching(monkeypatch):
    calls = _count_fetches(monkeypatch)

    first = await action.get_country_news(Response())
    second = await action.get_country_news(Response())

    assert first == second == {"countries": []}
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
