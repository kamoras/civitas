"""Tests for fetch_with_retry_requests — the requests-based fetch helper
UCSB-sourced president fetchers use instead of fetch_with_retry (httpx),
after presidency.ucsb.edu started blanket-403ing httpx's requests while
leaving `requests` unaffected (confirmed live, 2026-07-25). Mirrors
fetch_with_retry's retry/backoff contract closely enough that these mostly
double-check the swap didn't change caller-visible behavior.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.pipeline.fetch.http_utils import fetch_with_retry_requests
from app.pipeline.rate_limiter import RateLimiter


def _limiter():
    return RateLimiter(rps=1000.0)  # effectively unthrottled for tests


class TestFetchWithRetryRequests:
    @pytest.mark.asyncio
    async def test_returns_response_on_success(self):
        resp = MagicMock(status_code=200, text="ok")
        with patch("app.pipeline.fetch.http_utils.requests.request", return_value=resp):
            result = await fetch_with_retry_requests(_limiter(), "GET", "https://example.test")
        assert result is resp

    @pytest.mark.asyncio
    async def test_returns_none_after_exhausting_retries_on_4xx(self):
        resp = MagicMock(status_code=403, text="")
        with patch("app.pipeline.fetch.http_utils.requests.request", return_value=resp):
            result = await fetch_with_retry_requests(
                _limiter(), "GET", "https://example.test", retries=2, backoff_s=0.001,
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_recovers_after_a_transient_failure(self):
        ok = MagicMock(status_code=200, text="ok")
        with patch(
            "app.pipeline.fetch.http_utils.requests.request",
            side_effect=[ConnectionError("boom"), ok],
        ):
            result = await fetch_with_retry_requests(
                _limiter(), "GET", "https://example.test", retries=2, backoff_s=0.001,
            )
        assert result is ok

    @pytest.mark.asyncio
    async def test_429_retries_then_succeeds(self):
        limited = MagicMock(status_code=429, text="")
        ok = MagicMock(status_code=200, text="ok")
        with patch(
            "app.pipeline.fetch.http_utils.requests.request",
            side_effect=[limited, ok],
        ):
            result = await fetch_with_retry_requests(
                _limiter(), "GET", "https://example.test", retries=2, backoff_s=0.001,
            )
        assert result is ok


if __name__ == "__main__":
    import asyncio

    async def demo():
        await TestFetchWithRetryRequests().test_returns_response_on_success()
        await TestFetchWithRetryRequests().test_returns_none_after_exhausting_retries_on_4xx()
        await TestFetchWithRetryRequests().test_recovers_after_a_transient_failure()
        await TestFetchWithRetryRequests().test_429_retries_then_succeeds()
        print("OK")

    asyncio.run(demo())
