"""A bot wall's challenge is the site saying no (AGENTS.md §7): never
retried within a fetch, and its cookies never sent back."""

import httpx
import pytest

from app.http_client import is_bot_challenge, make_async_client
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter


def _resp(status, headers=None, body=b""):
    return httpx.Response(status, headers=headers or {}, content=body, request=httpx.Request("GET", "https://x.gov/"))


@pytest.mark.parametrize("resp,challenge", [
    (_resp(403, {"cf-mitigated": "challenge"}), True),
    (_resp(403, {"x-iinfo": "1-2-3"}), True),
    (_resp(200, {"x-iinfo": "1-2-3"}, b"<html>page</html>"), False),
    (_resp(200, {"x-iinfo": "1-2-3"}, b"<script src='/_Incapsula_Resource?x'>"), True),
    (_resp(403, {}, b"<title>Just a moment...</title>"), True),
    (_resp(403, {}, b"Forbidden"), False),
    (_resp(200), False),
])
def test_a_challenge_is_recognized(resp, challenge):
    assert is_bot_challenge(resp) is challenge


@pytest.mark.asyncio
async def test_a_challenge_is_not_retried_and_its_cookie_not_sent_back():
    seen = []

    def handler(request):
        seen.append(request.headers.get("cookie"))
        return httpx.Response(403, headers={"cf-mitigated": "challenge", "set-cookie": "__cf_bm=abc; Path=/"})

    async with make_async_client(transport=httpx.MockTransport(handler)) as client:
        got = await fetch_with_retry(client, RateLimiter(rps=1000), "GET", "https://wall.example.gov/a", backoff_s=0)
        assert got is None and len(seen) == 1
        await client.get("https://wall.example.gov/b")
    assert seen[1] is None  # the challenge's cookie was forgotten
