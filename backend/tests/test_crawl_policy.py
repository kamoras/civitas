"""The crawler's manners (app/crawl_policy.py through http_client): pace,
one request at a time, robots.txt and Crawl-delay, refusal backoff shared
through the data volume, Retry-After, conditional GETs, and one eFD session."""

import asyncio
import json
import time

import httpx
import pytest

from app import crawl_policy, http_client
from app.http_client import make_async_client
from app.pipeline.fetch import robots
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

ROBOTS_OK = "User-agent: *\nDisallow: /private/\n"


@pytest.fixture
def policy(monkeypatch):
    monkeypatch.setattr(crawl_policy, "ENABLED", True)
    crawl_policy.reset_for_tests()
    monkeypatch.setattr(crawl_policy, "_intervals", {})
    monkeypatch.setattr(crawl_policy, "DEFAULT_INTERVAL_S", 0.2)
    yield crawl_policy
    crawl_policy.reset_for_tests()


def _client(handler):
    return make_async_client(transport=httpx.MockTransport(handler))


def _site(robots_txt=ROBOTS_OK, answer=None, log=None):
    def handler(request):
        if log is not None:
            log.append((request.method, request.url.path, time.monotonic()))
        if request.url.path == "/robots.txt":
            if isinstance(robots_txt, int):
                return httpx.Response(robots_txt)
            return httpx.Response(200, text=robots_txt)
        return answer(request) if answer else httpx.Response(200, text="ok")
    return handler


def test_governed():
    on = crawl_policy.ENABLED
    try:
        crawl_policy.ENABLED = True
        assert crawl_policy.governed(httpx.URL("https://www.fec.gov/x"))
        assert not crawl_policy.governed(httpx.URL("http://backend:8000/api"))
        assert not crawl_policy.governed(httpx.URL("http://127.0.0.1:3000/"))
        assert not crawl_policy.governed(httpx.URL("http://localhost/"))
        assert not crawl_policy.governed(httpx.URL(f"https://{crawl_policy.SITE_DOMAIN}/about"))
    finally:
        crawl_policy.ENABLED = on


def test_crawl_delay_is_parsed():
    rules = robots.parse("User-agent: *\nCrawl-delay: 10\n\nUser-agent: Civitas\nCrawl-delay: 2\nDisallow: /x\n")
    assert rules.crawl_delay("Civitas/1.0") == 2.0
    assert robots.parse("User-agent: *\nCrawl-delay: 4\n").crawl_delay("Civitas") == 4.0
    assert robots.parse("User-agent: *\nCrawl-delay: soon\n").crawl_delay("Civitas") is None


@pytest.mark.asyncio
async def test_requests_to_one_host_are_paced_and_one_at_a_time(policy):
    log, inflight, overlap = [], [0], [False]

    async def slow(request):
        inflight[0] += 1
        overlap[0] |= inflight[0] > 1
        await asyncio.sleep(0.05)
        inflight[0] -= 1
        return httpx.Response(200)

    handler = _site(answer=None, log=log)

    async def both(request):
        if request.url.path == "/robots.txt":
            return handler(request)
        log.append((request.method, request.url.path, time.monotonic()))
        return await slow(request)

    async with make_async_client(transport=httpx.MockTransport(both)) as client:
        await asyncio.gather(*(client.get(f"https://a.example/p{i}") for i in range(3)))
    starts = [t for _, path, t in log if path.startswith("/p")]
    assert len(starts) == 3 and not overlap[0]
    assert all(b - a >= 0.18 for a, b in zip(starts, starts[1:]))


@pytest.mark.asyncio
async def test_robots_txt_sets_the_pace_and_never_blocks_a_read(policy):
    """Owner decision (2026-10-25): a Disallow is not applied; the file is
    read once a day for its Crawl-delay."""
    log = []
    rules = "User-agent: *\nDisallow: /\nCrawl-delay: 0.3\n"
    async with _client(_site(robots_txt=rules, log=log)) as client:
        assert (await client.get("https://a.example/private/doc")).status_code == 200
        assert (await client.get("https://a.example/other")).status_code == 200
    paths = [p for _, p, _ in log]
    assert paths.count("/robots.txt") == 1 and "/private/doc" in paths
    assert crawl_policy.interval("a.example") == 0.3


@pytest.mark.asyncio
async def test_an_unreadable_robots_txt_blocks_nothing_and_keeps_the_last_delay(policy):
    async with _client(_site(robots_txt=503)) as client:
        assert (await client.get("https://a.example/page")).status_code == 200
    assert crawl_policy.interval("a.example") == crawl_policy.DEFAULT_INTERVAL_S
    crawl_policy.classify_robots("b.example", httpx.Response(200, text="User-agent: *\nCrawl-delay: 0.4\n"), False)
    crawl_policy._robots["b.example"] = (0.4, 0.0, "read")  # expired
    async with _client(_site(robots_txt=503)) as client:
        assert (await client.get("https://b.example/page")).status_code == 200
    assert crawl_policy.interval("b.example") == 0.4


@pytest.mark.asyncio
async def test_crawl_delay_sets_the_pace(policy):
    async with _client(_site(robots_txt="User-agent: *\nCrawl-delay: 0.5\n")) as client:
        await client.get("https://a.example/one")
        assert crawl_policy.interval("a.example") == 0.5


@pytest.mark.asyncio
async def test_a_crawl_delay_too_long_to_wait_fails_the_request(policy, monkeypatch):
    monkeypatch.setattr(crawl_policy, "MAX_WAIT_S", 1.0)
    async with _client(_site(robots_txt="User-agent: *\nCrawl-delay: 600\n")) as client:
        await client.get("https://a.example/one")
        with pytest.raises(crawl_policy.HostRefusing, match="pace"):
            await client.get("https://a.example/two")


def _challenge(request):
    return httpx.Response(403, headers={"cf-mitigated": "challenge"}, text="Just a moment...")


@pytest.mark.asyncio
async def test_a_challenge_backs_the_host_off_then_one_probe(policy, monkeypatch):
    log = []
    answer = {"fn": _challenge}
    async with _client(_site(log=log, answer=lambda r: answer["fn"](r))) as client:
        assert (await client.get("https://w.example/a")).status_code == 403
        with pytest.raises(crawl_policy.HostRefusing, match="refusing Civitas"):
            await client.get("https://w.example/b")
        assert [p for _, p, _ in log if p != "/robots.txt"] == ["/a"]
        entry = crawl_policy.refusing("w.example")
        assert entry["count"] == 1 and entry["until"] - time.time() == pytest.approx(crawl_policy.REFUSAL_BASE_S, abs=5)

        # Kept on the data volume: another process (or a restart) sees it.
        saved = json.loads(open(crawl_policy._path(), encoding="utf-8").read())
        assert saved["w.example"]["count"] == 1
        crawl_policy._state, crawl_policy._state_stamp = {}, None
        assert crawl_policy.refusing("w.example")

        # The backoff ends: one probe, refused again, and the wait doubles.
        later = time.time() + crawl_policy.REFUSAL_BASE_S + 1
        monkeypatch.setattr(crawl_policy.time, "time", lambda: later)
        assert (await client.get("https://w.example/c")).status_code == 403
        assert crawl_policy.refusing("w.example")["count"] == 2
        assert crawl_policy.refusing("w.example")["until"] - later == pytest.approx(2 * crawl_policy.REFUSAL_BASE_S, abs=5)

        # A probe that is answered clears the record.
        later2 = later + 2 * crawl_policy.REFUSAL_BASE_S + 1
        monkeypatch.setattr(crawl_policy.time, "time", lambda: later2)
        answer["fn"] = lambda r: httpx.Response(200)
        assert (await client.get("https://w.example/d")).status_code == 200
        assert crawl_policy.refusing("w.example") is None and "w.example" not in crawl_policy._state


@pytest.mark.asyncio
async def test_retry_after_paces_or_backs_off(policy):
    async with _client(_site(answer=lambda r: httpx.Response(429, headers={"Retry-After": "1"}))) as client:
        await client.get("https://r.example/a")
        assert crawl_policy.refusing("r.example") is None
        assert crawl_policy._hosts["r.example"].next_start - time.monotonic() > 0.5
    async with _client(_site(answer=lambda r: httpx.Response(429, headers={"Retry-After": "7200"}))) as client:
        await client.get("https://q.example/a")
        assert crawl_policy.refusing("q.example")["until"] - time.time() >= 7190
    async with _client(_site(answer=lambda r: httpx.Response(429))) as client:
        await client.get("https://p.example/a")
        assert crawl_policy.refusing("p.example")["reason"].endswith("(HTTP 429)")


def test_retry_after_reads_seconds_and_dates():
    assert crawl_policy.retry_after_s(httpx.Response(429, headers={"Retry-After": "120"})) == 120
    when = "Wed, 21 Oct 2026 07:28:00 GMT"
    now = crawl_policy.parsedate_to_datetime(when).timestamp() - 30
    assert crawl_policy.retry_after_s(httpx.Response(503, headers={"Retry-After": when}), now) == pytest.approx(30)
    assert crawl_policy.retry_after_s(httpx.Response(503)) is None


@pytest.mark.asyncio
async def test_a_plain_403_is_not_a_hosts_refusal_and_is_not_retried(policy):
    log = []
    async with _client(_site(log=log, answer=lambda r: httpx.Response(403))) as client:
        assert await fetch_with_retry(client, RateLimiter(1000, name="t-403"), "GET", "https://n.example/f.xml") is None
    assert [p for _, p, _ in log if p != "/robots.txt"] == ["/f.xml"]
    assert crawl_policy.refusing("n.example") is None


@pytest.mark.asyncio
async def test_fetch_with_retry_reads_a_refusal_as_a_failure_without_retrying(policy):
    crawl_policy.record_refusal("x.example", "test")
    log = []
    async with _client(_site(log=log)) as client:
        assert await fetch_with_retry(client, RateLimiter(1000, name="t-ref"), "GET", "https://x.example/a") is None
    assert log == []


@pytest.mark.asyncio
async def test_a_gzipped_requests_answer_is_read(policy, monkeypatch):
    """requests has decoded the body, and its headers still say gzip: the
    observed copy must not be decoded again (2026-10-26: every Wikipedia
    read for the election map failed with a DecodingError)."""
    import requests

    from app.pipeline.fetch import http_utils

    def request(method, url, **kw):
        resp = requests.Response()
        resp.status_code, resp.url, resp._content = 200, url, b'{"ok": 1}'
        resp.headers.update({"Content-Encoding": "gzip", "Content-Length": "29"})
        return resp

    monkeypatch.setattr(http_utils.requests, "request", request)
    crawl_policy._robots["w.example"] = (None, time.monotonic() + 999, "read")
    resp = await http_utils.fetch_with_retry_requests(
        RateLimiter(1000, name="t-gz"), "GET", "https://w.example/api", retries=1, backoff_s=0)
    assert resp is not None and resp.json() == {"ok": 1}


def test_conditional_get_asks_only_for_a_change(monkeypatch):
    sent = []
    answers = [
        httpx.Response(200, content=b"<rss>1</rss>", headers={"ETag": '"v1"', "Content-Type": "application/rss+xml"}),
        httpx.Response(304),
    ]

    def get(url, headers=None, **kw):
        sent.append(dict(headers))
        return answers.pop(0)

    monkeypatch.setattr(httpx, "get", get)
    first = http_client.conditional_get("https://feeds.example/rss")
    second = http_client.conditional_get("https://feeds.example/rss")
    assert first.content == second.content == b"<rss>1</rss>" and second.status_code == 200
    assert "if-none-match" not in {k.lower() for k in sent[0]}
    assert {k.lower(): v for k, v in sent[1].items()}["if-none-match"] == '"v1"'


@pytest.mark.asyncio
async def test_efd_is_asked_once_a_night(policy, monkeypatch):
    """One accepted session per client for every step, and none at all
    while eFD's refusal holds: no request, no browser."""
    from app.pipeline.fetch import senate_ptr

    log = []
    home = '<input name="csrfmiddlewaretoken" value="tok">'

    def answer(request):
        return httpx.Response(200, text=home)

    async with _client(_site(robots_txt=404, log=log, answer=answer)) as client:
        assert await senate_ptr.accept_terms(client) == "tok"
        n = len(log)
        assert await senate_ptr.accept_terms(client) == "tok"
        assert len(log) == n  # the same session, no request
        assert await senate_ptr.accept_terms(client, renew=True) == "tok"
        assert len(log) > n

    crawl_policy.record_refusal(senate_ptr.EFD_HOST, "a bot wall's challenge (HTTP 403)")
    log.clear()
    async with _client(_site(robots_txt=404, log=log, answer=answer)) as client:
        with pytest.raises(senate_ptr.EfdRefused, match="not asked again before"):
            await senate_ptr.accept_terms(client)
    started = []
    monkeypatch.setattr(senate_ptr, "async_playwright", lambda: started.append(1))
    with pytest.raises(senate_ptr.EfdRefused):
        await senate_ptr.search_filings("2026-01-01", "Annual")
    assert log == [] and started == []
