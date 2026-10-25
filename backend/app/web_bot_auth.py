"""Civitas's requests, signed so a site can verify they are Civitas's.

A User-Agent is a claim anyone can copy. Web Bot Auth (the IETF webbotauth
working group's draft-ietf-webbotauth-httpsig-protocol, built on HTTP
Message Signatures, RFC 9421) makes it a proof: each request carries an
Ed25519 signature over its own authority, and the public key sits at this
site's `/.well-known/http-message-signatures-directory`, so a bot manager
(Cloudflare's verified bots, Akamai's bot directory) can check that a
request naming Civitas came from the holder of Civitas's key. It identifies
us; it gets us past nothing a site chooses to refuse (AGENTS.md §7).

Three headers go on every https request the backend sends
(`http_client.make_async_client`'s request hook, `signed_headers` for the
few plain `httpx.get` calls, `sign_browser_page` for the Playwright eFD
browser):

    Signature-Agent: "<SITE_URL, https only>"
    Signature-Input: sig1=("@authority" "signature-agent");created=…;
        keyid="<JWK thumbprint>";alg="ed25519";expires=…;nonce="…";
        tag="web-bot-auth"
    Signature: sig1=:<base64 Ed25519 signature>:

Two choices, stated:

- **Signature-Agent is sent as a bare String, not the draft's Dictionary.**
  The working group's -00 (2026-09) says signers MUST send the dictionary
  form and verifiers MAY accept the string; Cloudflare's verifier
  (developers.cloudflare.com/bots/reference/bot-verification/web-bot-auth/,
  read 2026-10-25) documents only the string and says not to send the
  dictionary (its crawltest.com endpoint parsed both on 2026-10-25). The verifiers that matter decide; `AGENT_MEMBER` switches it
  when Cloudflare follows the draft. Both forms are tested against the
  draft's Appendix E.2 vectors.
- **Covered: @authority and signature-agent only**, with a one-minute
  expiry (Cloudflare: "a minute is often sufficient") and a fresh nonce.
  The draft lets a signer narrow to @method/@path; Cloudflare refuses some
  components (@query-params), so the minimum is what every verifier takes.
  Plain http is never signed: a signature on the wire in clear text could
  be replayed for its minute.

The key is the instance's own, never the repo's: WEB_BOT_AUTH_KEY in .env,
the 32-byte Ed25519 seed base64url-encoded (the private JWK's "d"), made
once by `scripts/generate_web_bot_auth_key.py`. Unset, nothing is signed and
the directory answers 404. tests/test_web_bot_auth.py fails if a private
key is committed.
"""

import base64
import hashlib
import json
import logging
import os
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.site_url import SITE_URL, domain_of

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

ENV_VAR = "WEB_BOT_AUTH_KEY"
DIRECTORY_PATH = "/.well-known/http-message-signatures-directory"
DIRECTORY_MEDIA_TYPE = "application/http-message-signatures-directory+json"
REQUEST_TAG = "web-bot-auth"
DIRECTORY_TAG = "http-message-signatures-directory"
LABEL = "sig1"
# None: Signature-Agent as a bare String (Cloudflare); a key such as "agent1"
# sends the draft's Dictionary member instead. See the module docstring.
AGENT_MEMBER: str | None = None
# Cloudflare's guidance for a request signature's life.
REQUEST_TTL_S = 60
# The directory's possession proof: valid a day, cached an hour (by nginx and
# Cloudflare), so a cached copy is always well inside its window.
DIRECTORY_TTL_S = 86400
DIRECTORY_MAX_AGE_S = 3600


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def load_key(raw: str | None) -> Ed25519PrivateKey | None:
    """The key in `raw` (a base64url Ed25519 seed), or None when unset or
    malformed (logged, never echoed)."""
    value = (raw or "").strip()
    if not value:
        return None
    try:
        seed = _b64url_decode(value)
        if len(seed) != 32:
            raise ValueError("not 32 bytes")
        return Ed25519PrivateKey.from_private_bytes(seed)
    except ValueError as exc:
        logger.warning("%s is set but is not a base64url Ed25519 seed (%s); requests go out unsigned", ENV_VAR, exc)
        return None


def public_x(key: Ed25519PrivateKey) -> str:
    """The public key as the JWK's "x"."""
    return _b64url(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def thumbprint(x: str) -> str:
    """RFC 8037 Appendix A.3 / RFC 7638 JWK SHA-256 thumbprint of an Ed25519
    public key: the draft's required keyid."""
    canonical = json.dumps({"crv": "Ed25519", "kty": "OKP", "x": x}, separators=(",", ":"))
    return _b64url(hashlib.sha256(canonical.encode()).digest())


def _signature_agent_origin() -> str | None:
    """The origin serving our directory, https only (the draft requires it)."""
    return SITE_URL if SITE_URL.startswith("https://") else None


KEY = load_key(os.environ.get(ENV_VAR))
SIGNATURE_AGENT = _signature_agent_origin()
if KEY is not None and SIGNATURE_AGENT is None:
    logger.warning("%s is set but SITE_URL is not https; requests go out unsigned", ENV_VAR)


def authority(url: str) -> str:
    """RFC 9421 §2.2.3 @authority: the host, lowercased (IDNA for a non-ASCII
    name, brackets for IPv6), with the port only when it isn't the scheme's
    default."""
    parts = urlsplit(url)
    host = (parts.hostname or "").encode("idna").decode("ascii")
    if ":" in host:
        host = f"[{host}]"
    default = {"https": 443, "http": 80}.get(parts.scheme)
    return f"{host}:{parts.port}" if parts.port and parts.port != default else host


def sign_request_headers(
    key: Ed25519PrivateKey, *, authority: str, agent: str, created: int, expires: int,
    nonce: str, label: str = LABEL, agent_member: str | None = AGENT_MEMBER,
) -> dict[str, str]:
    """The three Web Bot Auth headers for a request to `authority`, signed
    by `key` with `agent` as the Signature-Agent origin. Every input is an
    argument so the draft's test vectors reproduce byte for byte."""
    agent_value = f'"{agent}"'
    agent_component = '"signature-agent"' + (f';key="{agent_member}"' if agent_member else "")
    params = (
        f'("@authority" {agent_component});created={created};keyid="{thumbprint(public_x(key))}"'
        f';alg="ed25519";expires={expires};nonce="{nonce}";tag="{REQUEST_TAG}"'
    )
    base = f'"@authority": {authority}\n{agent_component}: {agent_value}\n"@signature-params": {params}'
    signature = base64.b64encode(key.sign(base.encode())).decode()
    return {
        "Signature-Agent": f"{agent_member}={agent_value}" if agent_member else agent_value,
        "Signature-Input": f"{label}={params}",
        "Signature": f"{label}=:{signature}:",
    }


def signed_headers(url: Any, headers: dict[str, str] | None = None) -> dict[str, str]:
    """`headers` plus the Web Bot Auth headers for a request to `url`, when
    a key is configured and the URL is https; `headers` unchanged otherwise.

    For the few module-level `httpx.get`/`urllib` calls, whose headers are
    fixed before the request goes. ponytail: a cross-host redirect there
    carries the first host's signature, which the second reads as
    unverified (as if unsigned); move such a call onto a client with
    `resign` as its hook if a walled source ever sits behind one."""
    merged = dict(headers or {})
    url = str(url)
    if KEY is None or SIGNATURE_AGENT is None or urlsplit(url).scheme != "https":
        return merged
    created = int(time.time())
    merged.update(sign_request_headers(
        KEY, authority=authority(url), agent=SIGNATURE_AGENT, created=created,
        expires=created + REQUEST_TTL_S, nonce=base64.b64encode(os.urandom(64)).decode(),
    ))
    return merged


def resign(request: "httpx.Request") -> None:
    """httpx request hook (a sync `httpx.Client`'s): sign each request, every
    redirect hop included (httpx runs request hooks per hop, and a hop to
    another host needs a signature over that host). A hop copies the
    previous hop's headers, so the old signature is replaced, never left
    beside the new one."""
    for name in ("Signature", "Signature-Input", "Signature-Agent"):
        request.headers.pop(name, None)
    request.headers.update(signed_headers(request.url))


async def sign_request(request: "httpx.Request") -> None:
    """`resign` as an `httpx.AsyncClient` request hook."""
    resign(request)


async def sign_browser_page(page) -> None:
    """Sign every https request a Playwright page makes (the eFD search):
    each request goes on with the three headers added, nothing else changed."""
    if KEY is None or SIGNATURE_AGENT is None:
        return

    async def _add(route, request):
        await route.continue_(headers=signed_headers(request.url, request.headers))

    await page.route("**/*", _add)


def directory_body(key: Ed25519PrivateKey) -> bytes:
    """The JWKS the directory serves: the public key only, never "d"."""
    x = public_x(key)
    jwk = {"kty": "OKP", "crv": "Ed25519", "kid": thumbprint(x), "x": x, "use": "sig"}
    return json.dumps({"keys": [jwk]}, separators=(",", ":")).encode()


def directory_headers(key: Ed25519PrivateKey, body: bytes, *, authority: str, created: int, expires: int,
                      label: str = "binding") -> dict[str, str]:
    """The draft's Appendix B possession proof on the directory response:
    a signature over @authority (of the request, `req`) and the body's
    Content-Digest (RFC 9530), so the key set can't be re-served elsewhere
    or swapped under a captured signature."""
    digest = f"sha-256=:{base64.b64encode(hashlib.sha256(body).digest()).decode()}:"
    params = (
        f'("@authority";req "content-digest");created={created};expires={expires}'
        f';keyid="{thumbprint(public_x(key))}";tag="{DIRECTORY_TAG}"'
    )
    base = f'"@authority";req: {authority}\n"content-digest": {digest}\n"@signature-params": {params}'
    signature = base64.b64encode(key.sign(base.encode())).decode()
    return {
        "Content-Digest": digest,
        "Signature-Input": f"{label}={params}",
        "Signature": f"{label}=:{signature}:",
    }


def directory_response() -> tuple[bytes, dict[str, str]] | None:
    """The directory's body and headers, signed for this site's own domain
    (never the request's Host: a proof for any name asked would let another
    site re-serve our key set as its own), or None with no key."""
    if KEY is None or SIGNATURE_AGENT is None:
        return None
    body = directory_body(KEY)
    created = int(time.time())
    headers = directory_headers(KEY, body, authority=domain_of(SITE_URL), created=created,
                                expires=created + DIRECTORY_TTL_S)
    # no-transform: Content-Digest is over these exact bytes, so no proxy may
    # recompress them (Cloudflare honours it, as for every page here).
    headers["Cache-Control"] = f"public, max-age={DIRECTORY_MAX_AGE_S}, no-transform"
    return body, headers
