"""Project-wide httpx client with a hard wall-clock backstop per request.

Why this module exists — confirmed live 2026-08-02: a House pipeline run sat
wedged for 12h+ on a single congress.gov call. The connection was stuck in
CLOSE_WAIT (the peer had closed it; httpx had handed the dead connection back
out of its pool) and httpx's own `timeout=` never fired, because those
timeouts are enforced per socket *operation* — a socket that never becomes
readable and never errors has no operation to time out. The pipeline had no
upper bound on a single fetch, so one dead connection stopped everything
behind it indefinitely.

`asyncio.wait_for` around the send is the backstop that cannot be defeated
this way: it is wall-clock, enforced by the event loop, and completely
independent of what state the connection pool is in.

This lives at the top level (next to error_utils/time_utils), not under
`pipeline/fetch/`, so the API layer (health, admin, feedback) can import it
without pulling the pipeline package in.

## Why every client, not just `fetch_with_retry`

The wedge was reported through `fetch_with_retry`, but roughly twenty call
sites reach httpx directly (`await client.get(...)` in congress.py,
justice_votes.py, lda.py, govinfo.py, federal_register.py, …). They share the
exact failure mode, so the backstop belongs on the client every one of them
already uses rather than on one wrapper. `make_async_client()` is the single
constructor for the whole backend; constructing `httpx.AsyncClient` directly
anywhere in `app/` is a bug (there is a test that fails if one appears).

## Why a multiplier and not a fixed margin

The backstop must never fire on a request that is merely *slow but healthy*,
or it converts "this download takes a while" into a hard failure. Some fetches
legitimately run long against their own timeout: the House Clerk ZIP and the
presidential 278-T PDF both run with `timeout=60.0`, and a read timeout is per
chunk, so a large body streaming steadily can exceed the configured timeout
several times over without a single read ever stalling.

So the backstop is a *multiple* of whatever timeout the request already
carries, not timeout+delta. At 4x, a healthy transfer would have to be four
times slower than its own per-read budget to be cancelled, while a truly hung
request still dies in minutes instead of half a day — which is all this needs
to do. Streaming requests (`client.stream(...)`, the LLM token streams) are
unaffected past the response headers: `send()` returns once headers are in, so
a long generation is never on the clock here.

Two scope notes, stated rather than glossed:

- With `follow_redirects=True` the whole redirect chain resolves inside one
  `send()`, so the ceiling covers the chain rather than each hop. At 4x, a
  chain would need every hop running near its full timeout to trip it.
- The one *synchronous* httpx call left in the backend (news_feeds' RSS
  fetch) is not routed through here and does not need to be: module-level
  `httpx.get` builds a fresh client per call, so there is no pool to hand
  back a dead connection — the failure mode above structurally can't occur.
  Its own `timeout=` is a real per-socket-operation timeout and does fire.

## Completing a server's certificate chain (AIA)

A server is meant to send its certificate and the intermediate that signed
it. Some send the wrong intermediate or none: measured 2026-10-13, Alaska's,
Alabama's and Virginia's election sites, which every browser opens because
browsers fetch the missing intermediate from the address the certificate
itself names (its Authority Information Access "CA Issuers" URL). OpenSSL
doesn't, so for us those sites were simply unreachable — a ballot-measure
ingest failed and the source crawl skipped them.

So the client does what the browser does, once per host: on a verification
failure that says the issuer is missing, it reads the server's certificate,
fetches the issuer from that URL, checks the issuer's signature on it, adds it
to the shared context (TLS_CONTEXT) and tries once more. Verification is not
loosened: the context has partial chains turned off, so an added
intermediate is never a trust anchor — the chain must still end at a root in
certifi's bundle, and a self-signed certificate is never added. An issuer
fetched over plain HTTP that a network attacker replaced would fail exactly
there.
"""

import asyncio
import logging
import ssl
from typing import Any

import certifi
import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID

logger = logging.getLogger(__name__)

# Kept here rather than in pipeline/fetch/http_utils.py so this module has no
# imports from the pipeline package; http_utils re-exports it for its callers.
DEFAULT_FETCH_TIMEOUT_S = 30.0

# See the module docstring for why this is a multiplier. 4x is deliberately
# generous: the goal is bounding a hang, not tightening a timeout. Anything
# that would make a healthy-but-slow transfer fail belongs in the request's
# own `timeout=`, which is the knob that is actually meant for it.
HANG_BACKSTOP_MULTIPLIER = 4.0


def hang_backstop_seconds(timeout: Any) -> float:
    """Wall-clock ceiling for one request carrying `timeout`.

    Accepts anything httpx accepts as a timeout: a number, an
    `httpx.Timeout`, or the `{"connect": …, "read": …}` dict form that
    `Request.extensions["timeout"]` holds. The largest configured phase wins,
    since that is the longest any single socket operation is allowed to take.

    A request with no numeric timeout at all (`timeout=None`, "wait forever")
    falls back to the default budget rather than staying unbounded — an
    unbounded request is precisely the failure this module exists to stop.
    """
    if isinstance(timeout, httpx.Timeout):
        timeout = timeout.as_dict()
    if isinstance(timeout, dict):
        phases = [v for v in timeout.values() if isinstance(v, (int, float))]
        budget = max(phases) if phases else None
    elif isinstance(timeout, (int, float)):
        budget = timeout
    else:
        budget = None

    if budget is None or budget <= 0:
        budget = DEFAULT_FETCH_TIMEOUT_S
    return budget * HANG_BACKSTOP_MULTIPLIER


async def bounded(awaitable, timeout: Any, *, label: str = "request"):
    """Await `awaitable` under the hang backstop derived from `timeout`.

    Raises `TimeoutError` if the backstop fires — callers treat that as a
    failed attempt like any other transport error.
    """
    ceiling = hang_backstop_seconds(timeout)
    try:
        return await asyncio.wait_for(awaitable, timeout=ceiling)
    except TimeoutError:
        logger.error(
            "%s exceeded the %.1fs hang backstop and was cancelled — the "
            "connection never completed and never errored",
            label, ceiling,
        )
        raise


# A bot wall's challenge: the site refusing an automated client. Cloudflare
# says so in a header (`cf-mitigated: challenge`); Imperva/Incapsula marks
# every response it serves (`x-iinfo`), so there only a refusal counts, or
# a page that is its challenge script. AGENTS.md §7: getting past a wall is
# not an option, it is the site saying no.
_CHALLENGE_BODY_MARKERS = (b"_Incapsula_Resource", b"Just a moment...", b"/cdn-cgi/challenge-platform/")


def is_bot_challenge(response: httpx.Response) -> bool:
    """Whether `response` is a bot wall's challenge rather than the page."""
    headers = response.headers
    if headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    refused = response.status_code in (401, 403, 429, 503)
    if refused and "x-iinfo" in headers:
        return True
    try:
        head = response.content[:4096]
    except httpx.ResponseNotRead:  # a stream nobody has read: headers only
        return False
    return (refused or "x-iinfo" in headers) and any(m in head for m in _CHALLENGE_BODY_MARKERS)


def _tls_context() -> ssl.SSLContext:
    """httpx's own default trust (certifi), with partial chains off: a CA
    certificate added for chain completion is chain material, never an
    anchor (Python 3.13 turns partial chains on by default)."""
    context = ssl.create_default_context(cafile=certifi.where())
    context.verify_flags &= ~ssl.VERIFY_X509_PARTIAL_CHAIN
    return context


TLS_CONTEXT = _tls_context()
# X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY and ..._VERIFY_LEAF_SIGNATURE:
# the server's chain is missing the certificate that signed its own.
_MISSING_ISSUER = (20, 21)
# Hosts whose chain was completed, or is being: tried once per process each.
_chains_completed: set[str] = set()
# A chain is rarely more than two intermediates deep.
_AIA_MAX_DEPTH = 3


def _missing_issuer(exc: BaseException) -> bool:
    """Whether a connect error is a certificate whose issuer we lack."""
    seen = exc
    while seen is not None:
        if isinstance(seen, ssl.SSLCertVerificationError):
            return seen.verify_code in _MISSING_ISSUER
        seen = seen.__cause__ or seen.__context__
    return False


def ca_issuers(cert: x509.Certificate) -> list[str]:
    """The http(s) URLs a certificate names for its issuer (AIA)."""
    try:
        access = cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
    except x509.ExtensionNotFound:
        return []
    return [
        d.access_location.value for d in access
        if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS
        and d.access_location.value.startswith(("http://", "https://"))
    ]


def load_issuer(cert: x509.Certificate, body: bytes) -> x509.Certificate | None:
    """The certificate in `body` (DER or PEM), if it really signed `cert`
    and isn't self-signed — a root is never taken from the network."""
    try:
        issuer = x509.load_der_x509_certificate(body)
    except ValueError:
        try:
            issuer = x509.load_pem_x509_certificate(body)
        except ValueError:
            return None
    try:
        cert.verify_directly_issued_by(issuer)
    except (ValueError, TypeError, InvalidSignature):
        return None
    return None if issuer.subject == issuer.issuer else issuer


class BackstoppedAsyncClient(httpx.AsyncClient):
    """`httpx.AsyncClient` whose every request has a wall-clock ceiling.

    The bound is applied in `send()`, which is the single funnel every
    `get`/`post`/`request`/`stream` call goes through, so no call site can
    accidentally opt out of it — and where a server's incomplete certificate
    chain is completed (module docstring).
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("verify", TLS_CONTEXT)
        self._completes_chains = kwargs["verify"] is TLS_CONTEXT
        super().__init__(**kwargs)

    async def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        try:
            return await self._send_once(request, **kwargs)
        except httpx.ConnectError as exc:
            if not (self._completes_chains and _missing_issuer(exc)):
                raise
            try:
                failed = exc.request  # a redirect hop's own host, not the first one's
            except RuntimeError:
                failed = request
            if not await self._complete_chain(failed.url.host, failed.url.port or 443):
                raise
            return await self._send_once(request, **kwargs)

    async def _complete_chain(self, host: str, port: int) -> bool:
        """Fetch and add the issuers `host`'s certificate names. True when
        at least one was added, so the request is worth sending again."""
        if host in _chains_completed:
            return False
        _chains_completed.add(host)
        added = []
        try:
            pem = await asyncio.to_thread(ssl.get_server_certificate, (host, port), timeout=10)
            cert = x509.load_pem_x509_certificate(pem.encode())
            for _ in range(_AIA_MAX_DEPTH):
                issuer = None
                for url in ca_issuers(cert):
                    resp = await self.get(url, timeout=10, follow_redirects=True)
                    issuer = load_issuer(cert, resp.content) if resp.status_code == 200 else None
                    if issuer is not None:
                        break
                if issuer is None:
                    break
                TLS_CONTEXT.load_verify_locations(cadata=issuer.public_bytes(Encoding.DER))
                added.append(issuer.subject.rfc4514_string())
                cert = issuer
        except Exception as exc:  # any failure here leaves the original error standing
            logger.info("%s: could not complete its certificate chain (%s)", host, exc)
        if not added:
            _chains_completed.discard(host)  # asked again next time
            return False
        logger.info("%s sends an incomplete certificate chain — completed it with %s", host, "; ".join(added))
        return True

    async def _send_once(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        url = request.url
        # Scheme/host/path only — deliberately never the query string, which
        # is where callers like congress.py carry their API key. Building the
        # label without the credential beats redacting it afterwards (see
        # error_utils' docstring on why the after-the-fact form doesn't
        # satisfy py/clear-text-logging-sensitive-data), and the path alone
        # is all anyone needs to identify which fetch hung.
        response = await bounded(
            super().send(request, **kwargs),
            request.extensions.get("timeout"),
            label=f"{request.method} {url.scheme}://{url.host}{url.path}",
        )
        if is_bot_challenge(response):
            # Never carry a challenge's cookies to the next request: sent
            # back, they let the client through the wall the site put up
            # (Arizona's Secretary of State, measured 2026-10-08: a second
            # request on the same client answered 200).
            self._forget_cookies(url.host)
        return response

    def _forget_cookies(self, host: str) -> None:
        jar = self.cookies.jar
        for cookie in list(jar):
            domain = cookie.domain.lstrip(".")
            if host == domain or host.endswith("." + domain):
                jar.clear(cookie.domain, cookie.path, cookie.name)


def make_async_client(**kwargs: Any) -> BackstoppedAsyncClient:
    """Construct the backend's httpx client. Use this everywhere.

    Takes the same keyword arguments as `httpx.AsyncClient`.
    """
    return BackstoppedAsyncClient(**kwargs)
