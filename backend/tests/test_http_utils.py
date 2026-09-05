"""Tests for http_utils' two fetch helpers.

Two things are covered here: the wall-clock hang backstop that both helpers
apply (see app/http_client.py for what it's for), and fetch_with_retry_requests
itself — the requests-based helper UCSB-sourced president fetchers use instead
of fetch_with_retry (httpx), after presidency.ucsb.edu started blanket-403ing
httpx's requests while leaving `requests` unaffected (confirmed live,
2026-07-25). That second group mirrors fetch_with_retry's retry/backoff
contract closely enough that those tests mostly double-check the swap didn't
change caller-visible behavior.
"""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pipeline.fetch.http_utils import fetch_json_with_retry, fetch_with_retry, fetch_with_retry_requests
from app.pipeline.rate_limiter import RateLimiter


def _limiter():
    return RateLimiter(rps=1000.0)  # effectively unthrottled for tests


class TestFetchWithRetryHangBackstop:
    """Confirmed live 2026-08-02: a House pipeline run sat wedged 12h+ on a
    congress.gov call that never returned and never raised — a stale pooled
    httpx connection in CLOSE_WAIT that the client's own `timeout=` failed to
    catch. fetch_with_retry applies the shared wall-clock backstop (see
    app/http_client.py) so a call that never completes is treated as a failed
    attempt within a bounded time instead of hanging the pipeline forever.

    The backstop also lives on the client itself (make_async_client), which
    is what covers the ~20 call sites that use httpx directly; it is kept
    here as well because this function takes whatever AsyncClient a caller
    hands it — including the plain ones these tests pass.
    """

    @staticmethod
    def _hanging_client():
        async def _hang(*args, **kwargs):
            await asyncio.sleep(3600)

        client = MagicMock()
        client.request = AsyncMock(side_effect=_hang)
        return client

    @pytest.mark.asyncio
    async def test_hung_request_is_bounded_and_gives_up(self):
        client = self._hanging_client()
        result = await fetch_with_retry(
            client, _limiter(), "GET", "https://example.test",
            retries=1, timeout=0.01,
        )
        assert result is None
        assert client.request.call_count == 1

    @pytest.mark.asyncio
    async def test_hung_request_is_retried_like_any_other_failed_attempt(self):
        """The backstop raises into the same `except` the retry loop already
        uses, so a hang burns attempts rather than aborting the fetch on the
        first one."""
        client = self._hanging_client()
        result = await fetch_with_retry(
            client, _limiter(), "GET", "https://example.test",
            retries=3, backoff_s=0.001, timeout=0.01,
        )
        assert result is None
        assert client.request.call_count == 3

    @pytest.mark.asyncio
    async def test_hung_requests_backstop_is_bounded_in_wall_clock(self):
        client = self._hanging_client()
        start = asyncio.get_running_loop().time()
        await fetch_with_retry(
            client, _limiter(), "GET", "https://example.test",
            retries=2, backoff_s=0.001, timeout=0.01,
        )
        elapsed = asyncio.get_running_loop().time() - start
        assert elapsed < 1.0  # vs. the unbounded 12h+ this replaces

    @pytest.mark.asyncio
    async def test_requests_variant_is_bounded_too(self):
        """The `requests`-based sibling (used by every UCSB-sourced president
        fetcher) has the same unbounded shape and the same bound.

        The worker thread is released explicitly at the end rather than left
        sleeping: `asyncio.to_thread` runs on the default ThreadPoolExecutor,
        whose threads are joined at interpreter exit — a test that abandoned a
        sleeping one would hang the whole pytest process on the way out. That
        is also the honest shape of the production caveat documented in
        http_utils: the bound frees the event loop, not the thread.
        """
        release = threading.Event()

        def _hang(*args, **kwargs):
            release.wait(30)
            raise AssertionError("test did not release the worker thread")

        try:
            with patch("app.pipeline.fetch.http_utils.requests.request", side_effect=_hang):
                start = asyncio.get_running_loop().time()
                result = await fetch_with_retry_requests(
                    _limiter(), "GET", "https://example.test",
                    retries=1, timeout=0.01,
                )
                elapsed = asyncio.get_running_loop().time() - start
        finally:
            release.set()
        assert result is None
        assert elapsed < 1.0


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


class TestFetchJsonWithRetry:
    """Extracted from three near-identical per-state _get_json copies
    (state_candidates_ar.py, state_candidates_ct.py, state_candidates_in.py)
    — the two things those copies each did on top of fetch_with_retry:
    parse the response body, and turn a bad body into a logged None
    instead of a raised ValueError."""

    @pytest.mark.asyncio
    async def test_returns_the_parsed_json_body_on_success(self):
        resp = MagicMock(status_code=200, json=lambda: {"ok": True})
        client = MagicMock()
        client.request = AsyncMock(return_value=resp)
        result = await fetch_json_with_retry(client, _limiter(), "https://example.test", "label")
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_a_body_that_is_not_valid_json_returns_none_not_a_raise(self):
        def _raise():
            raise ValueError("no JSON object could be decoded")

        resp = MagicMock(status_code=200, json=_raise)
        client = MagicMock()
        client.request = AsyncMock(return_value=resp)
        result = await fetch_json_with_retry(client, _limiter(), "https://example.test", "label")
        assert result is None

    # A fetch failure (fetch_with_retry returning None) is not retested
    # here -- fetch_json_with_retry's only new behavior over fetch_with_
    # retry is the .json() parse and its error handling, both covered
    # above; fetch_with_retry's own retry/failure behavior is covered by
    # TestFetchWithRetryHangBackstop.


if __name__ == "__main__":
    import asyncio

    async def demo():
        await TestFetchWithRetryHangBackstop().test_hung_request_is_bounded_and_gives_up()
        await TestFetchWithRetryRequests().test_returns_response_on_success()
        await TestFetchJsonWithRetry().test_returns_the_parsed_json_body_on_success()
        await TestFetchWithRetryRequests().test_returns_none_after_exhausting_retries_on_4xx()
        await TestFetchWithRetryRequests().test_recovers_after_a_transient_failure()
        await TestFetchWithRetryRequests().test_429_retries_then_succeeds()
        print("OK")

    asyncio.run(demo())
