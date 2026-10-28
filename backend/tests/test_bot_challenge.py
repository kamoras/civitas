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
    # Imperva's challenge page (NJ, MA, 2026-10-26) against its script on
    # the site's own page: PA's and CA's real 404 for robots.txt.
    (_resp(200, {"x-iinfo": "1-2-3"}, b'<html style="height:100%"><head><META NAME="ROBOTS" '
           b'CONTENT="NOINDEX, NOFOLLOW"><script src="/_Incapsula_Resource?SWJIYLWA=x"></script>'), True),
    (_resp(200, {"x-iinfo": "1-2-3"}, b"<iframe>Request unsuccessful. Incapsula incident ID: 1</iframe>"), True),
    (_resp(404, {"x-iinfo": "1-2-3"}, b"<title>404 - File or directory not found.</title>"
           b'<script src="/_Incapsula_Resource?SWJIYLWA=x&ns=4" async></script>'), False),
    (_resp(200, {"x-iinfo": "1-2-3"}, b"<p>page</p><script src='/_Incapsula_Resource?x&ns=1'>"), False),
    # Cloudflare's header on sos.iowa.gov's real robots.txt, and its
    # detection script on an ordinary page: not challenges. Its page is.
    (_resp(200, {"cf-mitigated": "challenge"}, b"User-agent: *\nAllow: /\n"), False),
    (_resp(200, {}, b"<p>page</p><script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'>"), False),
    (_resp(200, {"cf-mitigated": "challenge"}, b"<title>Just a moment...</title>"), True),
    (_resp(403, {}, b"<title>Just a moment...</title>"), True),
    (_resp(403, {}, b"Forbidden"), False),
    # sosmt.gov's Cloudflare firewall page (2026-10-27): no cf-mitigated
    # header, the site's own script first, Cloudflare's past 4 KB.
    (_resp(403, {"server": "cloudflare"}, b"<head><script>" + b"x" * 7000 + b"</script></head>"
           b"Firewall Event Triggered<script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'>"), True),
    (_resp(200), False),
    # Akamai's edge answering for the origin (Senate eFD, 2026-10-23).
    (_resp(403, {"server": "AkamaiGHost"}, b"<HTML><HEAD>\n<TITLE>Access Denied</TITLE>"), True),
    (_resp(403, {"server": "gunicorn"}, b"<TITLE>Access Denied</TITLE>"), False),
    (_resp(404, {"server": "AkamaiGHost"}, b"Not Found"), False),
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


@pytest.mark.asyncio
async def test_senate_efd_terms_refused_by_akamai_is_a_wall_not_none():
    from app.pipeline.fetch import senate_ptr

    home = b'<form><input type="hidden" name="csrfmiddlewaretoken" value="t0k"></form>'
    posts = []

    def handler(request):
        if request.method == "POST":
            posts.append(request)
            return httpx.Response(403, headers={"server": "AkamaiGHost", "x-reference-error": "18.ab"},
                                  content=b"<HTML><HEAD>\n<TITLE>Access Denied</TITLE>")
        return httpx.Response(200, content=home)

    async with make_async_client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(senate_ptr.EfdRefused, match="next stock-trades run asks again.*18.ab"):
            await senate_ptr.accept_terms(client)
    assert len(posts) == 1  # asked once, never retried
