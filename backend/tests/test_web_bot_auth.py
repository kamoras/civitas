"""Web Bot Auth signing (app/web_bot_auth.py) against the draft's own test
vectors, the client hook, the directory route, and no committed key."""

import base64
import os
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from app import web_bot_auth as wba
from app.http_client import make_async_client

# RFC 9421 Appendix B.1.4's published test-key-ed25519 (its JWK "d" and "x"),
# the key draft-ietf-webbotauth-httpsig-protocol-00 Appendix E.2 signs with.
TEST_D = "n4Ni-HpISpVObnQMW0wOhCKROaIKqKtW_2ZYb2p9KcU"
TEST_X = "JrQLj5P_89iXES9-vFgrIy29clF9CC_oPPsw3c5D0bs"
TEST_KID = "poqkLGiymh_W0uP6PZFw-dvez3QJT5SolqXBCW38r0U"
TEST_KEY = wba.load_key(TEST_D)
AGENT = "https://signature-agent.test"


def test_test_key_and_thumbprint():
    assert wba.public_x(TEST_KEY) == TEST_X
    assert wba.thumbprint(TEST_X) == TEST_KID


def test_vector_e21_dictionary_signature_agent():
    """Appendix E.2.1: Signature-Agent as the Dictionary member "agent2"."""
    headers = wba.sign_request_headers(
        TEST_KEY, authority="example.com", agent=AGENT, created=1735689600, expires=4889289600,
        nonce="n9p433xm+NJ3ph3upfBIGmsuwHw387YV7Q/F+6BSpGCVjYCqQw6rznNA8PVVLySrAWsv0hQtFioQb6E1YsauiA==",
        label="sig2", agent_member="agent2",
    )
    assert headers["Signature-Agent"] == 'agent2="https://signature-agent.test"'
    assert headers["Signature-Input"] == (
        'sig2=("@authority" "signature-agent";key="agent2");created=1735689600'
        f';keyid="{TEST_KID}";alg="ed25519";expires=4889289600'
        ';nonce="n9p433xm+NJ3ph3upfBIGmsuwHw387YV7Q/F+6BSpGCVjYCqQw6rznNA8PVVLySrAWsv0hQtFioQb6E1YsauiA=="'
        ';tag="web-bot-auth"'
    )
    assert headers["Signature"] == (
        "sig2=:RdNFx5Bj6au3YgAMQL/RzmUlZE8QZLIaXGRpw985hWnwPfMxT228NMk6ehRS1PSl4e8PhbNZACSanGdhEwYCCg==:"
    )


def test_vector_e22_string_signature_agent():
    """Appendix E.2.2: the bare-String form, the one sent (Cloudflare)."""
    headers = wba.sign_request_headers(
        TEST_KEY, authority="example.com", agent=AGENT, created=1735689600, expires=1735693200,
        nonce="e8N7S2MFd/qrd6T2R3tdfAuuANngKI7LFtKYI/vowzk4lAZYadIX6wW25MwG7DCT9RUKAJ0qVkU0mEeLElW1qg==",
        label="sig2", agent_member=None,
    )
    assert headers["Signature-Agent"] == '"https://signature-agent.test"'
    assert headers["Signature-Input"].startswith('sig2=("@authority" "signature-agent");created=1735689600;')
    assert headers["Signature"] == (
        "sig2=:jdq0SqOwHdyHr9+r5jw3iYZH6aNGKijYp/EstF4RQTQdi5N5YYKrD+mCT1HA1nZDsi6nJKuHxUi/5Syp3rLWBA==:"
    )


def test_vector_e23_signed_directory():
    """Appendix E.2.3: the directory body and its possession proof."""
    body = wba.directory_body(TEST_KEY)
    assert body == (
        b'{"keys":[{"kty":"OKP","crv":"Ed25519","kid":"poqkLGiymh_W0uP6PZFw-dvez3QJT5SolqXBCW38r0U",'
        b'"x":"JrQLj5P_89iXES9-vFgrIy29clF9CC_oPPsw3c5D0bs","use":"sig"}]}'
    )
    headers = wba.directory_headers(TEST_KEY, body, authority="signature-agent.test",
                                    created=1735689600, expires=4889289600)
    assert headers["Content-Digest"] == "sha-256=:CADMT2aBdV/rqQr/NIru64ERQkCobVvllA4V0fLFDu0=:"
    assert headers["Signature-Input"] == (
        'binding=("@authority";req "content-digest");created=1735689600;expires=4889289600'
        f';keyid="{TEST_KID}";tag="http-message-signatures-directory"'
    )
    assert headers["Signature"] == (
        "binding=:l6P8R67tm3kujAxbHWio7ll01qrEZ0dKD/WWlGhNYEmTnFZM8Wt0VQ9zqGfvo7T/UMkBxsigzChM1Gpz7gOVBg==:"
    )


def test_malformed_key_is_unset():
    assert wba.load_key("") is None
    assert wba.load_key("not-a-key") is None


def _verify(headers: dict, authority: str) -> None:
    """Rebuild the signature base from the headers and check it."""
    params = headers["Signature-Input"].split("=", 1)[1]
    base = (f'"@authority": {authority}\n"signature-agent": {headers["Signature-Agent"]}\n'
            f'"@signature-params": {params}')
    sig = base64.b64decode(headers["Signature"].split("=:", 1)[1].rstrip(":"))
    TEST_KEY.public_key().verify(sig, base.encode())


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setattr(wba, "KEY", TEST_KEY)
    monkeypatch.setattr(wba, "SIGNATURE_AGENT", "https://civitas.example")


async def test_client_signs_every_hop_and_never_plain_http(keyed):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.host == "a.example":
            return httpx.Response(302, headers={"Location": "https://b.example:8443/next"})
        return httpx.Response(200)

    async with make_async_client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        await client.get("https://a.example/start")
        await client.get("http://plain.example/")
    first, hop, plain = seen
    _verify(first.headers, "a.example")
    _verify(hop.headers, "b.example:8443")
    assert first.headers["Signature-Agent"] == '"https://civitas.example"'
    assert "web-bot-auth" in first.headers["Signature-Input"]
    assert "Signature" not in plain.headers


async def test_unkeyed_client_sends_nothing(monkeypatch):
    monkeypatch.setattr(wba, "KEY", None)
    seen = []
    async with make_async_client(transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200))) as c:
        await c.get("https://a.example/")
    assert "Signature" not in seen[0].headers


def test_directory_route(keyed, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setattr(wba, "SITE_URL", "https://civitas.example")
    resp = TestClient(app).get(wba.DIRECTORY_PATH)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == wba.DIRECTORY_MEDIA_TYPE
    assert '"d"' not in resp.text and TEST_X in resp.text
    assert 'tag="http-message-signatures-directory"' in resp.headers["signature-input"]
    monkeypatch.setattr(wba, "KEY", None)
    assert TestClient(app).get(wba.DIRECTORY_PATH).status_code == 404


def test_no_private_key_is_committed():
    """The signing key belongs to the instance (.env), never to git."""
    root = Path(__file__).resolve().parents[2]
    if not shutil.which("git") or not (root / ".git").exists():
        pytest.skip("not a git checkout")
    tracked = subprocess.run(
        ["git", "grep", "-I", "-l", "-E", r"PRIVATE[ ]KEY-----|^[[:space:]]*WEB_BOT_AUTH_KEY[[:space:]]*=[[:space:]]*[A-Za-z0-9_-]{20,}"],
        cwd=root, capture_output=True, text=True,
    ).stdout.split()
    assert tracked == [], f"a private key is committed in {tracked}"
    configured = os.environ.get(wba.ENV_VAR, "").strip()
    if configured:
        found = subprocess.run(["git", "grep", "-I", "-l", "-F", configured], cwd=root,
                               capture_output=True, text=True).stdout.split()
        assert found == [], f"the configured {wba.ENV_VAR} is committed in {found}"


async def test_browser_page_requests_are_signed(keyed):
    class Route:
        async def continue_(self, headers):
            self.headers = headers

    class Request:
        url = "https://efdsearch.senate.gov/search/"
        headers = {"user-agent": "x"}

    class Page:
        async def route(self, pattern, handler):
            self.handler = handler

    page, route = Page(), Route()
    await wba.sign_browser_page(page)
    await page.handler(route, Request())
    assert route.headers["user-agent"] == "x"
    _verify(route.headers, "efdsearch.senate.gov")


def test_authority_forms():
    assert wba.authority("https://Example.COM/x?y") == "example.com"
    assert wba.authority("https://example.com:443/") == "example.com"
    assert wba.authority("https://example.com:8443/") == "example.com:8443"
    assert wba.authority("https://[::1]:8443/") == "[::1]:8443"
