"""A state's own document as the Internet Archive captured it, for a reader
whose state host is behind a bot wall (AGENTS.md §7).

A Wayback Machine capture is a copy of the state's own URL, made by the
Archive's crawler on the date it records, independently of Civitas and
before anything here asked for it. Reading that copy is reading a
republication of the state's document, the same as a county clerk's copy
of a state booklet (`republished_by`), not getting past the wall: the
request goes to archive.org, which publishes it for anyone, and nothing
asks the Archive to fetch the state's site now. So this module never
calls Save Page Now (which would have the Archive fetch the walled host
on our behalf, a proxy past the wall).

Only captures the Archive answered 200 for are used, and the newest. The
bytes are read in the Archive's raw mode (`id_`, the document exactly as
captured, no Wayback toolbar or rewritten links) and checked against the
capture's own SHA-1 digest from the CDX index, so what the reader parses
is byte for byte what the state served the Archive. Which document it is,
for which election, is still the reader's to decide by reading it, with
every check it applies to the state's own copy.

Politely: the CDX and raw-capture endpoints, the bot User-Agent (named,
with the contact), one request every few seconds: a handful a run for the
per-state readers, and for a measures reader run on the Archive's copy
(ArchiveTransport) two requests a page it asks for.
"""

import base64
import gzip
import hashlib
import logging
import zlib
from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin

import httpx

from app.contact import USER_AGENT
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

CDX_URL = "https://web.archive.org/cdx/search/cdx"
WAYBACK = "https://web.archive.org/web/"
PUBLISHER = "the Internet Archive"
HEADERS = {"User-Agent": USER_AGENT}

# The Archive asks API clients to keep to about one request a second and
# answers 429 well before that from one address; a few seconds apart keeps
# a run's handful of requests far from it.
_rate_limiter = RateLimiter(rps=0.25)
# A capture read refused by a 429 or a 5xx is tried again, this many times in all.
_CAPTURE_ATTEMPTS = 3


@dataclass(frozen=True)
class Capture:
    timestamp: str  # 14 digits, UTC, as the Archive records it
    original: str  # the state's own URL as captured
    digest: str  # base32 SHA-1 of the captured body
    mimetype: str = ""  # the Content-Type the state served, as the index records it

    @property
    def captured_on(self) -> date:
        return date(int(self.timestamp[:4]), int(self.timestamp[4:6]), int(self.timestamp[6:8]))

    @property
    def page_url(self) -> str:
        """The capture's public address: what a reader follows to check it."""
        return f"{WAYBACK}{self.timestamp}/{self.original}"

    @property
    def raw_url(self) -> str:
        return f"{WAYBACK}{self.timestamp}id_/{self.original}"

    @property
    def replay_url(self) -> str:
        """The capture as the Archive replays it: decoded, no toolbar, its
        links rewritten to captures (original_url maps them back)."""
        return f"{WAYBACK}{self.timestamp}if_/{self.original}"


def captured_on(url: str | None) -> date | None:
    """The capture date a Wayback page address carries, or None for any
    other address (the API credits an archived measure with it)."""
    if not url or not url.startswith(WAYBACK):
        return None
    stamp = url[len(WAYBACK):].split("/", 1)[0]
    if len(stamp) < 8 or not stamp[:8].isdigit():
        return None
    try:
        return date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))
    except ValueError:
        return None


def original_url(url: str) -> str:
    """The state's own URL a Wayback page address is a capture of; any
    other address as it is."""
    if captured_on(url) is None:
        return url
    return url[len(WAYBACK):].split("/", 1)[1]


def resolve(capture: "Capture", href: str) -> str:
    """A link on a replayed page (read_page) as the state's own address:
    the replay rewrites links to captures, which map back; a link it left
    as it was resolves against the state's page."""
    url = urljoin(capture.replay_url, href)
    return original_url(url) if captured_on(url) else urljoin(capture.original, href)


def newest_from_cdx(rows: list) -> Capture | None:
    """The newest 200 capture in a CDX JSON answer (header row first)."""
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    header = rows[0]
    try:
        records = [dict(zip(header, row)) for row in rows[1:]]
        ok = [r for r in records if r["statuscode"] == "200" and len(r["timestamp"]) == 14]
    except (KeyError, TypeError):
        return None
    if not ok:
        return None
    newest = max(ok, key=lambda r: r["timestamp"])
    return Capture(newest["timestamp"], newest["original"], newest["digest"], newest.get("mimetype") or "")


async def newest_capture(client: httpx.AsyncClient, url: str) -> Capture | None:
    """The Archive's newest 200 capture of exactly `url`, or None (none
    held, or the index couldn't be read: the caller treats both as no copy,
    never as an answer about the state's ballot)."""
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", CDX_URL, log_label=f"Wayback index for {url}", headers=HEADERS,
        params={"url": url, "output": "json", "fl": "timestamp,original,statuscode,digest,mimetype", "filter": "statuscode:200"},
    )
    if resp is None:
        return None
    try:
        return newest_from_cdx(resp.json() if resp.content.strip() else [])
    except ValueError:
        logger.warning("Wayback index for %s did not answer JSON", url)
        return None


def matches_digest(body: bytes, digest: str) -> bool:
    return base64.b32encode(hashlib.sha1(body).digest()).decode() == digest


async def read_capture(client: httpx.AsyncClient, capture: Capture) -> bytes | None:
    """The capture's body exactly as the state served it, decoded, or None
    when it can't be read or isn't byte for byte the body the index
    recorded.

    For a document whose words are quoted. The index's digest is of the
    body as the Archive's crawler stored it, which is sometimes the bytes
    on the wire (gzip, for a page served compressed) and sometimes the
    decoded body (measured 2026-10-29 on California's voter guide: its
    propositions index matched decoded, a proposition's title page
    matched gzip), so the raw-mode body is checked both ways. Until then
    only the decoded body was checked, and no compressed page ever
    verified (Ohio's directives page, 2026-10-23)."""
    for _ in range(_CAPTURE_ATTEMPTS):
        await _rate_limiter.acquire()
        try:
            async with client.stream("GET", capture.raw_url, headers=HEADERS, timeout=60.0) as resp:
                if resp.status_code == 429 or resp.status_code >= 500:
                    continue
                if resp.status_code != 200:
                    logger.warning("Wayback capture %s answered HTTP %d", capture.page_url, resp.status_code)
                    return None
                try:
                    wire = b"".join([chunk async for chunk in resp.aiter_raw()])
                    encoding = (resp.headers.get("content-encoding") or "").lower()
                except httpx.StreamConsumed:  # already read (and decoded) on the way in
                    wire, encoding = resp.content, ""
        except httpx.TransportError:
            continue
        decoded = _decode(wire, encoding)
        if matches_digest(wire, capture.digest) or (decoded is not None and matches_digest(decoded, capture.digest)):
            if decoded is None:
                logger.warning("Wayback capture %s is %s-encoded, which can't be decoded here — refusing", capture.page_url, encoding)
            return decoded
        logger.warning("Wayback capture %s does not match its recorded digest — refusing", capture.page_url)
        return None
    logger.warning("Wayback capture %s could not be read", capture.page_url)
    return None


def _decode(wire: bytes, encoding: str) -> bytes | None:
    """The body under its Content-Encoding (gzip or deflate; brotli has no
    decoder in the image), or None when it can't be decoded."""
    try:
        if encoding in ("", "identity"):
            return wire
        if encoding in ("gzip", "x-gzip"):
            return gzip.decompress(wire)
        if encoding == "deflate":
            try:
                return zlib.decompress(wire)
            except zlib.error:
                return zlib.decompress(wire, -zlib.MAX_WBITS)
    except (OSError, EOFError, zlib.error):
        return None
    return None


class NoCapture(httpx.TransportError):
    """No verified capture of a URL: never an answer about the page (a
    reader reads it as the failed fetch it is, never as a 404)."""


class ArchiveTransport(httpx.AsyncBaseTransport):
    """Answers every GET from the Archive's newest verified capture of its
    URL (newest_capture, read_capture), so a reader written for the state's
    own site reads the Archive's copy of it unchanged and nothing is sent to
    the state's host. `captures` records which capture answered each URL,
    for crediting what was read. A URL with no verified capture, or any
    other method, raises NoCapture."""

    def __init__(self, client: httpx.AsyncClient):
        self._client = client
        self.captures: dict[str, Capture] = {}
        self._bodies: dict[str, bytes] = {}
        self._missing: set[str] = set()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "GET" and url not in self._missing and url not in self._bodies:
            capture = await newest_capture(self._client, url)
            body = await read_capture(self._client, capture) if capture else None
            if body is None:
                self._missing.add(url)
            else:
                self.captures[url], self._bodies[url] = capture, body
        if url not in self._bodies:
            raise NoCapture(f"no verified Internet Archive capture of {url}", request=request)
        mimetype = self.captures[url].mimetype or "application/octet-stream"
        return httpx.Response(200, content=self._bodies[url], headers={"Content-Type": mimetype}, request=request)


async def read_page(client: httpx.AsyncClient, capture: Capture) -> bytes | None:
    """A captured page read only to find its links, from the Archive's
    replay rather than raw mode. Raw mode sends the body in whatever
    encoding the state's server used, and httpx can't decode brotli here
    (measured 2026-10-28: sosmt.gov's preview pages were captured `br`, so
    a raw read was undecodable bytes and no link was found); the replay is
    decoded by the Archive. Its links point at captures: resolve() maps
    each back to the state's address. Never for a
    document whose words are quoted: those are read_capture, verified."""
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", capture.replay_url, log_label=f"Wayback replay {capture.page_url}",
        headers=HEADERS,
    )
    return resp.content if resp is not None else None
