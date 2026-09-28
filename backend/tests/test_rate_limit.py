"""Tests for shared rate-limiting IP resolution and the write-endpoint limiter.

client_ip is the security-critical piece: it must not trust
X-Forwarded-For from an untrusted direct peer, or a spoofed header
trivially defeats every rate limit built on top of it (2026-07 audit —
public.py had an unguarded duplicate of this logic that did exactly
that, on 8+ live endpoints).

As of the Swarm IP-recovery fix, the trust boundary is "the direct peer
is a private/loopback/link-local address" — our own nginx reaches the
backend over the Docker overlay network (a private range), never as a
public client, and the backend publishes no host port of its own. A
genuinely public direct peer is therefore never trusted. These tests use
real globally-routable addresses (8.8.8.8, 1.2.3.4) for the untrusted
cases — note that Python 3.13's `ipaddress.is_private` classifies the
TEST-NET documentation ranges (198.51.100/24, 203.0.113/24) as private,
so those are not valid stand-ins for a public peer here.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api import throttle
from app.api.rate_limit import client_ip, write_rate_limit


def _make_request(peer_ip: str, forwarded_for: str | None = None) -> MagicMock:
    req = MagicMock()
    req.client.host = peer_ip
    req.headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
    return req


class TestClientIp:
    def test_untrusted_peer_ignores_forwarded_header(self):
        # A direct internet client can set any X-Forwarded-For it wants —
        # if it isn't relayed through our own proxy, it must not be trusted.
        req = _make_request("8.8.8.8", forwarded_for="1.2.3.4")
        assert client_ip(req) == "8.8.8.8"

    def test_trusted_loopback_proxy_uses_forwarded_header(self):
        req = _make_request("127.0.0.1", forwarded_for="8.8.8.8")
        assert client_ip(req) == "8.8.8.8"

    def test_trusted_overlay_proxy_uses_forwarded_header(self):
        # The production case: nginx reaches the backend over the Docker
        # overlay network, so the peer is a private 10.0.x.x address. Its
        # X-Forwarded-For last hop must be trusted or per-IP limiting
        # collapses to one global bucket.
        req = _make_request("10.0.1.7", forwarded_for="8.8.8.8")
        assert client_ip(req) == "8.8.8.8"

    def test_trusted_proxy_uses_last_hop_not_first(self):
        # A malicious client can prepend its own fake entry before the
        # request reaches our proxy; our proxy appends the real IP after
        # it. The last entry is the one our trust boundary actually saw.
        req = _make_request("10.0.1.7", forwarded_for="1.2.3.4, 8.8.8.8")
        assert client_ip(req) == "8.8.8.8"

    def test_no_client_falls_back_to_unknown(self):
        req = MagicMock()
        req.client = None
        req.headers = {}
        assert client_ip(req) == "unknown"


@pytest.mark.usefixtures("throttle_store")
class TestWriteRateLimit:
    async def test_allows_under_limit(self):
        req = _make_request("8.8.4.1")
        for _ in range(20):
            await write_rate_limit(req)  # should not raise

    async def test_blocks_over_limit(self):
        req = _make_request("8.8.4.2")
        for _ in range(20):
            await write_rate_limit(req)
        with pytest.raises(HTTPException) as exc:
            await write_rate_limit(req)
        assert exc.value.status_code == 429

    async def test_limit_is_per_ip(self):
        req_a = _make_request("8.8.4.3")
        req_b = _make_request("8.8.4.4")
        for _ in range(20):
            await write_rate_limit(req_a)
        await write_rate_limit(req_b)  # different IP, should not raise

    async def test_spoofed_forwarded_header_does_not_bypass_limit(self):
        # Same untrusted public peer, different claimed X-Forwarded-For
        # each request — a public peer is never trusted, so all 25
        # requests bucket under the peer and the limit still triggers.
        for i in range(25):
            req = _make_request("8.8.4.5", forwarded_for=f"1.2.3.{i}")
            if i < 20:
                await write_rate_limit(req)
            else:
                with pytest.raises(HTTPException):
                    await write_rate_limit(req)

    async def test_the_ip_itself_is_never_stored(self, throttle_store):
        import sqlite3

        await write_rate_limit(_make_request("8.8.4.7"))
        conn = sqlite3.connect(throttle_store)
        keys = [row[0] for row in conn.execute("SELECT key FROM windows")]
        conn.close()
        assert keys and all("8.8.4.7" not in k for k in keys)
        assert keys == [throttle.client_key("8.8.4.7", "write")]


@pytest.mark.usefixtures("throttle_store")
class TestPublicApiRateLimit:
    async def test_counts_down_then_refuses_with_headers(self):
        from types import SimpleNamespace

        from app.api.public import _RATE_LIMIT, _rate_limit_dep

        req = _make_request("8.8.4.8")
        req.state = SimpleNamespace()
        await _rate_limit_dep(req)
        assert req.state.rl_remaining == _RATE_LIMIT - 1
        assert req.state.rl_reset > 0
        for _ in range(_RATE_LIMIT - 1):
            await _rate_limit_dep(req)
        assert req.state.rl_remaining == 0
        with pytest.raises(HTTPException) as exc:
            await _rate_limit_dep(req)
        assert exc.value.status_code == 429
        assert exc.value.headers["X-RateLimit-Remaining"] == "0"
        assert exc.value.headers["X-RateLimit-Reset"] == str(req.state.rl_reset)
        # Retry-After agrees with the reset it gives, not a fixed guess.
        assert 1 <= int(exc.value.headers["Retry-After"]) <= 120

    async def test_writes_do_not_use_up_the_read_limit(self):
        from types import SimpleNamespace

        from app.api.public import _rate_limit_dep

        req = _make_request("8.8.4.9")
        req.state = SimpleNamespace()
        for _ in range(20):
            await write_rate_limit(req)
        await _rate_limit_dep(req)  # a separate bucket


@pytest.mark.usefixtures("throttle_store")
async def test_a_write_refusal_says_when_to_retry(monkeypatch):
    # Retry-After is the moment the sliding estimate first lets a request
    # through (throttle._retry_at), not a fixed period.
    now = 1_000_000 * 60.0 + 30.0
    monkeypatch.setattr(throttle.time, "time", lambda: now)
    monkeypatch.setattr("app.api.rate_limit.time.time", lambda: now)
    req = _make_request("8.8.4.12")
    for _ in range(20):
        await write_rate_limit(req)
    with pytest.raises(HTTPException) as exc:
        await write_rate_limit(req)
    decision = throttle.hit("write", throttle.client_key("8.8.4.12", "write"), limit=20, period=60.0)
    assert not decision.allowed
    assert exc.value.headers["Retry-After"] == str(int(decision.reset_at - now))


def test_an_uncounted_public_request_reports_no_remaining_quota():
    """A limiter that couldn't count (its store unavailable) must not
    advertise a full quota."""
    from types import SimpleNamespace

    from app.api.public import _rl_headers

    counted = SimpleNamespace(state=SimpleNamespace(rl_remaining=5, rl_reset=100, rl_counted=True))
    uncounted = SimpleNamespace(state=SimpleNamespace(rl_remaining=60, rl_reset=100, rl_counted=False))
    assert _rl_headers(counted)["X-RateLimit-Remaining"] == "5"
    assert "X-RateLimit-Remaining" not in _rl_headers(uncounted)
    assert _rl_headers(uncounted)["X-RateLimit-Limit"] == "60"
