"""Tests for the wall-clock hang backstop (app/http_client.py).

The bug being guarded against (confirmed live 2026-08-02): a House pipeline
run sat wedged 12h+ on one congress.gov call whose connection was stuck in
CLOSE_WAIT. httpx's own `timeout=` is enforced per socket operation, so a
socket that never becomes readable and never errors never trips it. Every
test here is about the property that fixes that — a request always completes
or raises within a bounded wall-clock time — plus the property that the bound
never fires on a request that is merely slow.
"""

import asyncio
import pathlib
import re

import httpx
import pytest

from app.http_client import (
    DEFAULT_FETCH_TIMEOUT_S,
    HANG_BACKSTOP_MULTIPLIER,
    BackstoppedAsyncClient,
    hang_backstop_seconds,
    make_async_client,
)


class _HangingTransport(httpx.AsyncBaseTransport):
    """A transport that accepts the request and then never returns — the
    in-process equivalent of the dead pooled connection."""

    def __init__(self):
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")


class TestHangBackstopSeconds:
    def test_plain_number_is_scaled_by_the_multiplier(self):
        assert hang_backstop_seconds(30.0) == 30.0 * HANG_BACKSTOP_MULTIPLIER

    def test_httpx_timeout_object_uses_its_longest_phase(self):
        timeout = httpx.Timeout(connect=5.0, read=60.0, write=5.0, pool=5.0)
        assert hang_backstop_seconds(timeout) == 60.0 * HANG_BACKSTOP_MULTIPLIER

    def test_extensions_dict_form_uses_its_longest_phase(self):
        # This is the shape httpx puts in Request.extensions["timeout"],
        # which is what BackstoppedAsyncClient.send actually reads.
        assert hang_backstop_seconds(
            {"connect": 5.0, "read": 60.0, "write": 5.0, "pool": None}
        ) == 60.0 * HANG_BACKSTOP_MULTIPLIER

    @pytest.mark.parametrize(
        "timeout",
        [None, {}, {"connect": None, "read": None}, httpx.Timeout(None), 0, -1],
        ids=["none", "empty", "all-phases-none", "timeout-none", "zero", "negative"],
    )
    def test_no_usable_budget_falls_back_to_the_default_rather_than_unbounded(self, timeout):
        """`timeout=None` means "wait forever" to httpx — which is exactly the
        failure mode here, so it gets a bound anyway."""
        assert hang_backstop_seconds(timeout) == (
            DEFAULT_FETCH_TIMEOUT_S * HANG_BACKSTOP_MULTIPLIER
        )

    def test_ceiling_always_exceeds_the_request_timeout_it_derives_from(self):
        """The bound must sit above the timeout it backstops, or it would
        pre-empt httpx's own (more informative) timeout on every slow call."""
        for timeout in (0.05, 5.0, 15.0, 30.0, 60.0, 300.0):
            assert hang_backstop_seconds(timeout) > timeout


class TestBackstoppedClientBoundsDirectCalls:
    """`fetch_with_retry` is not the only way this codebase reaches httpx —
    ~20 call sites do `await client.get(...)` directly. The bound lives on
    the client so those are covered without each one opting in."""

    @pytest.mark.asyncio
    async def test_hung_get_raises_instead_of_hanging_forever(self):
        transport = _HangingTransport()
        async with make_async_client(transport=transport, timeout=0.05) as client:
            with pytest.raises(TimeoutError):
                await client.get("https://example.test/thing")
        assert transport.calls == 1

    @pytest.mark.asyncio
    async def test_the_bound_is_actually_wall_clock(self):
        transport = _HangingTransport()
        async with make_async_client(transport=transport, timeout=0.05) as client:
            start = asyncio.get_running_loop().time()
            with pytest.raises(TimeoutError):
                await client.get("https://example.test/thing")
            elapsed = asyncio.get_running_loop().time() - start
        ceiling = hang_backstop_seconds(0.05)
        # Bounded by the ceiling (with slack for scheduling), and not
        # returning instantly either — a bound that fires immediately would
        # kill healthy requests.
        assert ceiling <= elapsed < ceiling + 1.0

    @pytest.mark.asyncio
    async def test_per_request_timeout_overrides_the_client_default(self):
        """A caller passing `timeout=` on the call (which most of this
        codebase does) must get a ceiling derived from *that* value, not the
        client-level default."""
        transport = _HangingTransport()
        async with make_async_client(transport=transport, timeout=30.0) as client:
            start = asyncio.get_running_loop().time()
            with pytest.raises(TimeoutError):
                await client.get("https://example.test/thing", timeout=0.05)
            elapsed = asyncio.get_running_loop().time() - start
        assert elapsed < hang_backstop_seconds(30.0)

    @pytest.mark.asyncio
    async def test_a_healthy_response_is_untouched(self):
        """The backstop must be invisible on the happy path — status, body
        and headers all come back exactly as httpx produced them."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True}, headers={"x-trace": "abc"})

        async with make_async_client(
            transport=httpx.MockTransport(handler), timeout=0.05
        ) as client:
            resp = await client.get("https://example.test/thing")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert resp.headers["x-trace"] == "abc"

    @pytest.mark.asyncio
    async def test_transport_errors_still_surface_as_themselves(self):
        """Wrapping send() must not convert a real connection error into a
        TimeoutError — callers (and fetch_with_retry's retry logic) branch on
        the real exception type."""
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        async with make_async_client(
            transport=httpx.MockTransport(handler), timeout=0.05
        ) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get("https://example.test/thing")

    @pytest.mark.asyncio
    async def test_streaming_body_is_not_on_the_clock(self):
        """`client.stream(...)` returns once headers are in, so the LLM token
        streams (ollama_client) are bounded on time-to-headers only — a long
        generation must never be cancelled mid-stream."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"data: one\ndata: two\n")

        async with make_async_client(
            transport=httpx.MockTransport(handler), timeout=0.05
        ) as client:
            async with client.stream("POST", "https://example.test/gen") as resp:
                # Longer than the 0.2s ceiling for timeout=0.05: if the body
                # were inside the bound, this would raise.
                await asyncio.sleep(0.3)
                body = await resp.aread()
        assert b"data: two" in body

    def test_make_async_client_returns_the_backstopped_subclass(self):
        client = make_async_client()
        assert isinstance(client, BackstoppedAsyncClient)
        assert isinstance(client, httpx.AsyncClient)


class TestEveryClientInTheAppIsBackstopped:
    """The fix is only worth anything if it stays applied. A plain
    `httpx.AsyncClient(...)` added later would silently reintroduce the
    unbounded path this module exists to close, and nothing else in the
    build would notice."""

    def test_no_module_constructs_a_raw_httpx_asyncclient(self):
        app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
        # Construction only — `client: httpx.AsyncClient` annotations and
        # `class X(httpx.AsyncClient)` are not what this is looking for.
        construction = re.compile(r"(?<!class )\bhttpx\.AsyncClient\s*\(")
        offenders = []
        for path in sorted(app_dir.rglob("*.py")):
            if path.name == "http_client.py":
                continue  # the one place the subclass is legitimately defined
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                if construction.search(line):
                    offenders.append(f"{path.relative_to(app_dir.parent)}:{lineno}")
        assert not offenders, (
            "These construct httpx.AsyncClient directly, so their requests have "
            "no wall-clock hang backstop. Use app.http_client.make_async_client "
            f"instead: {offenders}"
        )
