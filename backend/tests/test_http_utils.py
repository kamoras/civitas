"""Tests for fetch_with_retry_requests — the requests-based fetch helper
UCSB-sourced president fetchers use instead of fetch_with_retry (httpx),
after presidency.ucsb.edu started blanket-403ing httpx's requests while
leaving `requests` unaffected (confirmed live, 2026-07-25). Mirrors
fetch_with_retry's retry/backoff contract closely enough that these mostly
double-check the swap didn't change caller-visible behavior.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pipeline.fetch.http_utils import fetch_with_retry, fetch_with_retry_requests
from app.pipeline.rate_limiter import RateLimiter


def _limiter():
    return RateLimiter(rps=1000.0)  # effectively unthrottled for tests


class TestFetchWithRetryHangBackstop:
    """Confirmed live 2026-08-02: a House pipeline run sat wedged 12h+ on a
    congress.gov call that never returned and never raised — a stale pooled
    httpx connection in CLOSE_WAIT that the client's own `timeout=` failed to
    catch. fetch_with_retry now wraps the request in asyncio.wait_for as a
    hard backstop so a call that never completes still gets treated as a
    failed attempt within a bounded time, instead of hanging the pipeline
    forever."""

    @pytest.mark.asyncio
    async def test_hung_request_is_bounded_and_exhausts_retries(self):
        async def _hang(*args, **kwargs):
            await asyncio.sleep(3600)

        client = MagicMock()
        client.request = AsyncMock(side_effect=_hang)
        result = await fetch_with_retry(
            client, _limiter(), "GET", "https://example.test",
            retries=1, timeout=0.05,
        )
        assert result is None
        assert client.request.call_count == 1


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
        await TestFetchWithRetryHangBackstop().test_hung_request_is_bounded_and_exhausts_retries()
        await TestFetchWithRetryRequests().test_returns_response_on_success()
        await TestFetchWithRetryRequests().test_returns_none_after_exhausting_retries_on_4xx()
        await TestFetchWithRetryRequests().test_recovers_after_a_transient_failure()
        await TestFetchWithRetryRequests().test_429_retries_then_succeeds()
        print("OK")

    asyncio.run(demo())
