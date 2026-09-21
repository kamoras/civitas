"""Search Bluesky for posts about specific candidates/races via
app.bsky.feed.searchPosts.

Unlike bluesky_engagement.py (reads a fixed list of news-outlet author
feeds) and trending.py (reads platform-wide trending topics), this is
genuine keyword search — the read capability the midterm-elections
coverage feed needs and the only gap in this codebase's existing Bluesky
integration.

**searchPosts now requires authentication.** Until 2026-09 this ran
unauthenticated against the public AppView (public.api.bsky.app), which
needed no credentials at all. Bluesky has since withdrawn searchPosts
from that host: it answers `403` with a BunnyCDN HTML error page rather
than an atproto JSON error, and does so **even when a valid Bearer token
is supplied**, so no credential fixes that host. Measured on 2026-09-21
from the production box — every other endpoint on the same host, same
IP, same client still answers 200 (getProfile, getAuthorFeed,
searchActors, getPopularFeedGenerators), which is what rules out an IP
block or a User-Agent filter. Reads now go to the authenticated AppView
(api.bsky.app) with a Bearer token; bsky.social works identically by
proxying to it, but the AppView is the read path's proper home.

The rate-limit hazard that shaped the original design is unchanged and
still governs this one. Logging in PER SEARCH CALL meant one
createSession per candidate per pass — ~50 a run against Bluesky's
~30-per-5-min session-create limit, which would lock out the SAME
account the platform posts from (2026-07 review B1). So the session is
created ONCE per process and cached here: a run's 50 searches cost one
createSession, not 50, and an expired token is repaired with
refreshSession rather than a fresh login. `_session_lock` keeps a
concurrent first call from stampeding into several simultaneous logins —
the same hazard by a different route.

With no credentials configured, search is simply unavailable and says so
once. It does not degrade into 50 failing requests that each look like
"this candidate has no coverage".
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
CREATE_SESSION_URL = "https://bsky.social/xrpc/com.atproto.server.createSession"
REFRESH_SESSION_URL = "https://bsky.social/xrpc/com.atproto.server.refreshSession"

SEARCH_MAX_AGE_HOURS = 48
SEARCH_LIMIT_PER_QUERY = 25
SEARCH_TIMEOUT_S = 15.0

# Cached atproto session, shared by every search in this process.
_session: dict[str, str] = {}
_session_lock = asyncio.Lock()
# Set once when credentials are missing or a login is rejected, so the
# run reports the outage a single time instead of once per candidate.
_auth_unavailable: str | None = None


@dataclass
class BlueskyPost:
    text: str
    url: str
    author_handle: str
    published: datetime | None = None


def _post_url(handle: str, uri: str) -> str:
    """AT URI (at://did:plc:xxx/app.bsky.feed.post/<rkey>) to a bsky.app
    web link — the rkey is the last path segment regardless of DID vs.
    handle form."""
    rkey = uri.rsplit("/", 1)[-1]
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


def _parse_indexed_at(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


async def _login(client: httpx.AsyncClient) -> bool:
    """Create the one session this process will reuse. Caller holds the lock."""
    global _auth_unavailable

    handle = getattr(settings, "BSKY_HANDLE", "")
    app_password = getattr(settings, "BSKY_APP_PASSWORD", "")
    if not handle or not app_password:
        _auth_unavailable = "no BSKY_HANDLE/BSKY_APP_PASSWORD configured"
        return False

    try:
        resp = await client.post(
            CREATE_SESSION_URL,
            json={"identifier": handle, "password": app_password},
            timeout=SEARCH_TIMEOUT_S,
        )
        resp.raise_for_status()
        body = resp.json()
        _session["access"] = body["accessJwt"]
        _session["refresh"] = body["refreshJwt"]
        return True
    except Exception as exc:
        # Deliberately not exc_info: a credential rejection is a
        # configuration fact, and a stack trace per run buries it.
        _auth_unavailable = f"login rejected ({type(exc).__name__})"
        return False


async def _refresh(client: httpx.AsyncClient) -> bool:
    """Trade the refresh token for a new access token.

    Preferred over a second login: refreshSession is not bound by the
    session-CREATE rate limit that review B1 was about.
    """
    token = _session.get("refresh")
    if not token:
        return False
    try:
        resp = await client.post(
            REFRESH_SESSION_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=SEARCH_TIMEOUT_S,
        )
        resp.raise_for_status()
        body = resp.json()
        _session["access"] = body["accessJwt"]
        _session["refresh"] = body["refreshJwt"]
        return True
    except Exception:
        _session.clear()
        return False


async def _access_token(client: httpx.AsyncClient) -> str | None:
    """The cached access token, logging in once if there isn't one yet."""
    if _session.get("access"):
        return _session["access"]
    async with _session_lock:
        # Another coroutine may have logged in while we waited.
        if _session.get("access"):
            return _session["access"]
        if _auth_unavailable:
            return None
        if not await _login(client):
            logger.error(
                "Bluesky search unavailable — %s. searchPosts requires "
                "authentication since 2026-09; no candidate social coverage "
                "will be ingested this run.", _auth_unavailable,
            )
            return None
    return _session.get("access")


def search_is_available() -> bool:
    """False once this process has established it cannot authenticate.

    Lets a caller report "source unavailable" rather than presenting an
    empty result as a finding of no coverage.
    """
    return _auth_unavailable is None


async def search_posts(
    client: httpx.AsyncClient, query: str, limit: int = SEARCH_LIMIT_PER_QUERY,
) -> list[BlueskyPost]:
    """Search recent Bluesky posts matching `query` via the public AppView.

    Returns [] on any request/parse failure — same graceful-degradation
    shape as the rest of the Bluesky integration (bluesky_poster.py,
    bluesky_engagement.py), never raises.

    A post with a missing/unparseable indexed_at is KEPT despite the
    recency cutoff: the cutoff exists to skip stale posts, and treating
    "timestamp unknown" as "stale" would silently drop valid results on a
    field the AppView isn't contractually required to populate. The
    published field is then None, which downstream stores as NULL rather
    than a guessed time.
    """
    token = await _access_token(client)
    if token is None:
        return []

    async def _request(bearer: str) -> httpx.Response:
        return await client.get(
            SEARCH_URL,
            params={"q": query, "limit": limit},
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=SEARCH_TIMEOUT_S,
        )

    try:
        resp = await _request(token)
        # An access token lasts ~2h while this process lives for days, so
        # expiry mid-run is the expected case, not an error: refresh once
        # and retry before treating it as a failure.
        if resp.status_code == 401:
            async with _session_lock:
                refreshed = await _refresh(client) or await _login(client)
            if not refreshed:
                logger.error("Bluesky search: session expired and could not "
                             "be renewed — %s", _auth_unavailable or "unknown")
                return []
            resp = await _request(_session["access"])
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        logger.warning("Bluesky search failed for query %r", query, exc_info=True)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=SEARCH_MAX_AGE_HOURS)
    results: list[BlueskyPost] = []
    for post in payload.get("posts") or []:
        record = post.get("record") or {}
        text = (record.get("text") or "").strip()
        if not text:
            continue

        published = _parse_indexed_at(post.get("indexedAt"))
        if published and published < cutoff:
            continue

        author_handle = (post.get("author") or {}).get("handle") or ""
        results.append(BlueskyPost(
            text=text,
            url=_post_url(author_handle, str(post.get("uri") or "")),
            author_handle=author_handle,
            published=published,
        ))
    return results
