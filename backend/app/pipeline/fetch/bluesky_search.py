"""Search Bluesky for posts about specific candidates/races via
app.bsky.feed.searchPosts.

Unlike bluesky_engagement.py (reads a fixed list of news-outlet author
feeds) and trending.py (reads platform-wide trending topics), this is
genuine keyword search — the read capability the midterm-elections
coverage feed needs and the only gap in this codebase's existing Bluesky
integration.

Uses Bluesky's PUBLIC AppView endpoint (public.api.bsky.app) with the
caller's plain httpx client — deliberately NOT the authenticated
atproto.Client the posting path uses. The original design logged in per
search call, which at one search per candidate per ingestion pass meant
hundreds of createSession calls per run against Bluesky's ~30-per-5-min
session-create rate limit — a guaranteed lockout of the SAME account the
platform posts from (2026-07 review B1). searchPosts requires no auth at
all per Bluesky's own docs, so the read path now shares nothing with the
posting account: search volume can never jeopardize posting credentials.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

PUBLIC_SEARCH_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"

SEARCH_MAX_AGE_HOURS = 48
SEARCH_LIMIT_PER_QUERY = 25
SEARCH_TIMEOUT_S = 15.0


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
    from datetime import timedelta, timezone

    try:
        resp = await client.get(
            PUBLIC_SEARCH_URL,
            params={"q": query, "limit": limit},
            timeout=SEARCH_TIMEOUT_S,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        logger.warning("Bluesky public search failed for query %r", query, exc_info=True)
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
