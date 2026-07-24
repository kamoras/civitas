"""Search Bluesky for posts about specific candidates/races via
app.bsky.feed.searchPosts.

Unlike bluesky_engagement.py (reads a fixed list of news-outlet author
feeds) and trending.py (reads platform-wide trending topics), this is
genuine keyword search — the read capability the midterm-elections
coverage feed needs and the only gap in this codebase's existing Bluesky
integration (searchPosts is public/unauthenticated per Bluesky's own
docs, but this reuses the same authenticated atproto.Client the rest of
the integration already logs in with, rather than managing a second,
unauthenticated client just for this one call).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config import settings

logger = logging.getLogger(__name__)

SEARCH_MAX_AGE_HOURS = 48
SEARCH_LIMIT_PER_QUERY = 25


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


def search_posts(query: str, limit: int = SEARCH_LIMIT_PER_QUERY) -> list[BlueskyPost]:
    """Search recent Bluesky posts matching `query`.

    Returns [] if credentials aren't configured, atproto isn't installed,
    login fails, or the search call fails — same graceful-degradation
    shape as the rest of the Bluesky integration (bluesky_poster.py,
    bluesky_engagement.py), never raises.
    """
    handle = getattr(settings, "BSKY_HANDLE", "")
    app_password = getattr(settings, "BSKY_APP_PASSWORD", "")
    if not handle or not app_password:
        return []

    try:
        from atproto import Client
    except ImportError:
        logger.error("atproto not installed — cannot search Bluesky")
        return []

    client = Client()
    try:
        client.login(handle, app_password)
    except Exception:
        logger.exception("Bluesky login failed in search module")
        return []

    try:
        resp = client.app.bsky.feed.search_posts(params={"q": query, "limit": limit})
    except Exception:
        logger.warning("Bluesky search failed for query %r", query, exc_info=True)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=SEARCH_MAX_AGE_HOURS)
    results: list[BlueskyPost] = []
    for post in (resp.posts or []):
        text = getattr(post.record, "text", "") or ""
        if not text.strip():
            continue

        published = None
        indexed_at = getattr(post, "indexed_at", None)
        if indexed_at:
            try:
                published = datetime.fromisoformat(str(indexed_at).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        if published and published < cutoff:
            continue

        author_handle = getattr(post.author, "handle", "") or ""
        results.append(BlueskyPost(
            text=text,
            url=_post_url(author_handle, str(post.uri)),
            author_handle=author_handle,
            published=published,
        ))
    return results
