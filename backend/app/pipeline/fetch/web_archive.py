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
with the contact), one request every few seconds, a handful per run.
"""

import base64
import hashlib
import logging
from dataclasses import dataclass
from datetime import date

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


@dataclass(frozen=True)
class Capture:
    timestamp: str  # 14 digits, UTC, as the Archive records it
    original: str  # the state's own URL as captured
    digest: str  # base32 SHA-1 of the captured body

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
    return Capture(newest["timestamp"], newest["original"], newest["digest"])


async def newest_capture(client: httpx.AsyncClient, url: str) -> Capture | None:
    """The Archive's newest 200 capture of exactly `url`, or None (none
    held, or the index couldn't be read: the caller treats both as no copy,
    never as an answer about the state's ballot)."""
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", CDX_URL, log_label=f"Wayback index for {url}", headers=HEADERS,
        params={"url": url, "output": "json", "fl": "timestamp,original,statuscode,digest", "filter": "statuscode:200"},
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


async def read_capture(client: httpx.AsyncClient, capture: Capture, *, verify: bool = True) -> bytes | None:
    """The capture's body exactly as the state served it, or None when it
    can't be read or isn't byte for byte the body the index recorded.

    `verify=False` only for a page read to find a link, never for a
    document whose words are quoted: the index's digest is of the body as
    the server sent it on the wire, and an HTML page served compressed
    comes back decoded (measured 2026-10-23: Ohio's directives page never
    matched, a PDF from the same host matched byte for byte). What such a
    page points at is read and verified on its own."""
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", capture.raw_url, log_label=f"Wayback capture {capture.page_url}",
        headers=HEADERS,
    )
    if resp is None:
        return None
    if verify and not matches_digest(resp.content, capture.digest):
        logger.warning("Wayback capture %s does not match its recorded digest — refusing", capture.page_url)
        return None
    return resp.content
