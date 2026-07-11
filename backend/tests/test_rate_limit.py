"""Tests for shared rate-limiting IP resolution and the write-endpoint limiter.

client_ip is the security-critical piece: it must not trust
X-Forwarded-For from an untrusted direct peer, or a spoofed header
trivially defeats every rate limit built on top of it (2026-07 audit —
public.py had an unguarded duplicate of this logic that did exactly
that, on 8+ live endpoints).
"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.rate_limit import client_ip, write_rate_limit, _window


def _make_request(peer_ip: str, forwarded_for: str | None = None) -> MagicMock:
    req = MagicMock()
    req.client.host = peer_ip
    req.headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
    return req


class TestClientIp:
    def test_untrusted_peer_ignores_forwarded_header(self):
        # A direct internet client can set any X-Forwarded-For it wants —
        # if it isn't relayed through our own proxy, it must not be trusted.
        req = _make_request("203.0.113.5", forwarded_for="1.2.3.4")
        assert client_ip(req) == "203.0.113.5"

    def test_trusted_proxy_uses_forwarded_header(self):
        req = _make_request("127.0.0.1", forwarded_for="203.0.113.5")
        assert client_ip(req) == "203.0.113.5"

    def test_trusted_proxy_uses_last_hop_not_first(self):
        # A malicious client can prepend its own fake entry before the
        # request reaches our proxy; our proxy appends the real IP after
        # it. The last entry is the one our trust boundary actually saw.
        req = _make_request("127.0.0.1", forwarded_for="1.2.3.4, 203.0.113.5")
        assert client_ip(req) == "203.0.113.5"

    def test_no_client_falls_back_to_unknown(self):
        req = MagicMock()
        req.client = None
        req.headers = {}
        assert client_ip(req) == "unknown"


class TestWriteRateLimit:
    def setup_method(self):
        _window.clear()

    def test_allows_under_limit(self):
        req = _make_request("198.51.100.1")
        for _ in range(20):
            write_rate_limit(req)  # should not raise

    def test_blocks_over_limit(self):
        req = _make_request("198.51.100.2")
        for _ in range(20):
            write_rate_limit(req)
        with pytest.raises(HTTPException) as exc:
            write_rate_limit(req)
        assert exc.value.status_code == 429

    def test_limit_is_per_ip(self):
        req_a = _make_request("198.51.100.3")
        req_b = _make_request("198.51.100.4")
        for _ in range(20):
            write_rate_limit(req_a)
        write_rate_limit(req_b)  # different IP, should not raise

    def test_spoofed_forwarded_header_does_not_bypass_limit(self):
        # Same untrusted peer, different claimed X-Forwarded-For each
        # request — if client_ip trusted this blindly, every request
        # would look like a "new" IP and the limit would never trigger.
        for i in range(25):
            req = _make_request("198.51.100.5", forwarded_for=f"10.0.0.{i}")
            if i < 20:
                write_rate_limit(req)
            else:
                with pytest.raises(HTTPException):
                    write_rate_limit(req)
