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
    @pytest.mark.parametrize("timeout, longest", [
        pytest.param(30.0, 30.0, id="plain_number_is_scaled_by_the_multiplier"),
        pytest.param(httpx.Timeout(connect=5.0, read=60.0, write=5.0, pool=5.0), 60.0,
                     id="httpx_timeout_object_uses_its_longest_phase"),
        # This is the shape httpx puts in Request.extensions["timeout"],
        # which is what BackstoppedAsyncClient.send actually reads.
        pytest.param({"connect": 5.0, "read": 60.0, "write": 5.0, "pool": None}, 60.0,
                     id="extensions_dict_form_uses_its_longest_phase"),
    ])
    def test_scales_the_longest_phase_by_the_multiplier(self, timeout, longest):
        assert hang_backstop_seconds(timeout) == longest * HANG_BACKSTOP_MULTIPLIER

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
    async def test_hung_get_raises_within_the_wall_clock_bound(self):
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
        assert transport.calls == 1

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


class TestIncompleteCertificateChains:
    """Alaska's, Alabama's and Virginia's election sites (2026-10-13) sent
    no intermediate, or the wrong one: every browser opens them by fetching
    the issuer the certificate names (AIA), and so does the client now —
    without loosening verification. The certificates are the real ones."""

    TLS = pathlib.Path(__file__).parent / "fixtures" / "tls"

    def _leaf(self):
        from cryptography import x509

        return x509.load_der_x509_certificate((self.TLS / "ak_leaf.der").read_bytes())

    def test_an_added_intermediate_is_never_a_trust_anchor(self):
        import ssl

        from app.http_client import TLS_CONTEXT

        assert not TLS_CONTEXT.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN
        assert TLS_CONTEXT.verify_mode == ssl.CERT_REQUIRED and TLS_CONTEXT.check_hostname

    def test_the_issuer_url_is_read_from_the_certificate(self):
        from app.http_client import ca_issuers

        assert ca_issuers(self._leaf()) == ["http://secure.globalsign.com/cacert/gsgccr46ovtlsca2025.crt"]

    def test_only_the_certificate_that_signed_it_is_taken(self):
        from app.http_client import load_issuer

        issuer = load_issuer(self._leaf(), (self.TLS / "ak_issuer.der").read_bytes())
        assert issuer is not None and "GCC R46 OV TLS CA 2025" in issuer.subject.rfc4514_string()
        # Another state's (real, valid) intermediate did not sign this one.
        assert load_issuer(self._leaf(), (self.TLS / "al_issuer.der").read_bytes()) is None
        assert load_issuer(self._leaf(), b"<html>not a certificate</html>") is None

    def test_a_self_signed_certificate_from_the_network_is_never_added(self):
        import datetime

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding
        from cryptography.x509.oid import NameOID

        from app.http_client import load_issuer

        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Root")])
        start = datetime.datetime(2026, 1, 1)
        root = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(1).not_valid_before(start).not_valid_after(start + datetime.timedelta(days=1))
                .sign(key, hashes.SHA256()))
        assert load_issuer(root, root.public_bytes(Encoding.DER)) is None

    @staticmethod
    def _missing_issuer_error():
        import ssl

        cause = ssl.SSLCertVerificationError(1, "certificate verify failed: unable to get local issuer certificate")
        cause.verify_code = 20
        try:
            raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]") from cause
        except httpx.ConnectError as exc:
            return exc

    @pytest.mark.asyncio
    async def test_the_request_is_sent_again_once_the_chain_is_completed(self, monkeypatch):
        attempts = []

        def handler(request):
            attempts.append(request.url.host)
            if len(attempts) == 1:
                raise self._missing_issuer_error()
            return httpx.Response(200, text="ok")

        completed = []

        async def complete(self_, host, port):
            completed.append((host, port))
            return True

        monkeypatch.setattr(BackstoppedAsyncClient, "_complete_chain", complete)
        async with make_async_client(transport=httpx.MockTransport(handler)) as client:
            client._completes_chains = True  # a mock transport stands in for the TLS one
            resp = await client.get("https://elections.alaska.gov/robots.txt")
        assert resp.text == "ok"
        assert completed == [("elections.alaska.gov", 443)]
        assert attempts == ["elections.alaska.gov"] * 2

    @pytest.mark.asyncio
    async def test_any_other_connect_error_is_left_alone(self, monkeypatch):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        async def complete(self_, host, port):
            raise AssertionError("not a certificate problem")

        monkeypatch.setattr(BackstoppedAsyncClient, "_complete_chain", complete)
        async with make_async_client(transport=httpx.MockTransport(handler)) as client:
            client._completes_chains = True
            with pytest.raises(httpx.ConnectError):
                await client.get("https://x.gov/")

    @pytest.mark.asyncio
    async def test_a_chain_that_cannot_be_completed_keeps_its_error(self, monkeypatch):
        def handler(request):
            raise self._missing_issuer_error()

        async def complete(self_, host, port):
            return False

        monkeypatch.setattr(BackstoppedAsyncClient, "_complete_chain", complete)
        async with make_async_client(transport=httpx.MockTransport(handler)) as client:
            client._completes_chains = True
            with pytest.raises(httpx.ConnectError):
                await client.get("https://x.gov/")
