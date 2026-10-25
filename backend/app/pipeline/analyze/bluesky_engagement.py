"""
Engage with news outlet posts on Bluesky.

An outlet's recent post is reposted and liked when it links an article a
live Action Center issue cites as a source: the outlet's own words about a
story the site is already showing, matched by the article the post links
(its address after redirects, without scheme, query or trailing slash),
never by a similarity threshold.

It replaced a cosine threshold (0.78, set by hand). Measured 2026-10-24 on
the outlets' public feeds against production's stored issues (119 days,
6,531 posts, each matched against the issues of its own day and the day
before): the cosine gate would have reposted 623 posts, 36.6 a week, and
only 51 of them linked an article the matched issue cites. Its precision
could not be measured at all, since a post about no issue has no label. The
link rule reposts 80 posts, 4.7 a week (on 50 days, at most 5 a day), and is right by construction: the post
links a source of the issue. That is also within the posting volume the
account keeps to (AGENTS.md: posting everything taught readers to skip
it).

This keeps the Civitas feed active between original posts and surfaces
relevant reporting within the civic transparency context.

Design constraints:
- Only original posts (no replies, no reposts of others) from the last 24h
- At most MAX_REPOSTS_PER_RUN reposts per hourly pipeline run
- Each post is reposted at most once, and its link followed once it answers (tracked via ApiCache)
- Runs silently if credentials are not configured
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.contact import USER_AGENT
from app.models import ActionIssue, ApiCache
from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S

logger = logging.getLogger(__name__)

# Hard cap on reposts per pipeline run (runs hourly) — keeps the feed human-paced
MAX_REPOSTS_PER_RUN = 3

# Only consider posts from within this window
POST_MAX_AGE_HOURS = 24

# Cache tier used to record which Bluesky posts have been processed
_CACHE_TIER = "bsky_engagement"

# Major news outlets with reliable Bluesky presence.
# Handles are resolved by atproto — use the domain-style handles that
# verified outlets register (e.g. apnews.com maps to their Bluesky account).
# "newshour.bsky.social" was listed until 2026-10-21: an account with no
# posts at all, so it contributed nothing; an outlet whose feed comes back
# empty is now logged as a warning.
NEWS_OUTLET_HANDLES: list[str] = [
    "apnews.com",
    "npr.org",
]


def article_key(url: str | None) -> str | None:
    """An article's address without scheme, query, fragment or trailing
    slash, so one article reads the same however it was linked."""
    if not url:
        return None
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    return f"{host}{parts.path.rstrip('/')}" if host else None


def issue_sources(issues: list[ActionIssue]) -> dict[str, ActionIssue]:
    """Each article the issues cite, by article_key, to its issue."""
    out: dict[str, ActionIssue] = {}
    for issue in issues:
        try:
            urls = json.loads(issue.source_urls or "[]")
        except (TypeError, ValueError):
            continue
        for url in urls:
            key = article_key(url)
            if key:
                out.setdefault(key, issue)
    return out


def linked_article(post) -> str | None:
    """The URL of the article a post links (an external embed, alone or
    beside media), or None."""
    embed = getattr(post, "embed", None)
    external = getattr(embed, "external", None) or getattr(getattr(embed, "media", None), "external", None)
    return getattr(external, "uri", None)


def resolve_key(url: str, known: dict, client: httpx.Client) -> str | None:
    """The linked article's key: as linked when an issue cites it, else
    after following its redirects (outlets post short links: n.pr, apne.ws).
    None when the link can't be followed now (a failed request, a rate
    limit or a server error: the shorteners answer a burst with 429), so
    the post is tried again next run rather than recorded as linking
    nothing. (The article's own site may refuse a bot, 403, at an address
    that is still the article's.)"""
    key = article_key(url)
    if key in known:
        return key
    try:
        resp = client.head(url)
    except httpx.HTTPError:
        logger.info("Engagement: link %s could not be followed", url, exc_info=True)
        return None
    if resp.status_code == 429 or resp.status_code >= 500:
        logger.info("Engagement: link %s answered %d; tried again next run", url, resp.status_code)
        return None
    return article_key(str(resp.url))


# What a post's cache row holds: REPOSTED once it has been reposted, else
# the key of the article it links ("" for none), so a post seen before an
# issue cited its article is matched on a later run without following its
# link again.
REPOSTED = "reposted"


def _seen(cid: str, db: Session) -> str | None:
    row = db.get(ApiCache, (_CACHE_TIER, cid))
    return row.data_json if row is not None else None


def _record(cid: str, value: str, db: Session) -> None:
    try:
        row = db.get(ApiCache, (_CACHE_TIER, cid))
        if row is None:
            db.add(ApiCache(tier=_CACHE_TIER, cache_key=cid, data_json=value))
        else:
            row.data_json = value
        db.commit()
    except Exception:
        db.rollback()


def engage_with_news_posts(db: Session) -> None:
    """Repost and like outlet posts that link a source of a live issue.

    Called from the action center refresh pipeline after issues are updated.
    No-op if Bluesky credentials are not configured.
    """
    handle = getattr(settings, "BSKY_HANDLE", "")
    app_password = getattr(settings, "BSKY_APP_PASSWORD", "")
    if not handle or not app_password:
        return

    active_issues = (
        db.query(ActionIssue)
        .filter(ActionIssue.is_current == True)  # noqa: E712
        .all()
    )
    sources = issue_sources(active_issues)
    if not sources:
        return

    cutoff = datetime.now(UTC) - timedelta(hours=POST_MAX_AGE_HOURS)

    try:
        from atproto import Client
    except ImportError:
        logger.error("atproto not installed — cannot run news engagement")
        return

    client = Client()
    try:
        client.login(handle, app_password)
    except Exception:
        logger.exception("Bluesky login failed in engagement module")
        return

    reposts_this_run = 0
    logger.info(
        "Bluesky engagement: checking %d outlets against %d articles %d active issues cite",
        len(NEWS_OUTLET_HANDLES), len(sources), len(active_issues),
    )

    with httpx.Client(timeout=DEFAULT_FETCH_TIMEOUT_S, follow_redirects=True,
                      headers={"User-Agent": USER_AGENT}) as http:
        for outlet_handle in NEWS_OUTLET_HANDLES:
            if reposts_this_run >= MAX_REPOSTS_PER_RUN:
                break

            try:
                feed_resp = client.get_author_feed(actor=outlet_handle, limit=25, filter="posts_no_replies")
            except Exception:
                logger.warning("Failed to fetch feed for %s", outlet_handle, exc_info=True)
                continue

            n_posts = len(feed_resp.feed or [])
            logger.info("@%s: %d posts in feed", outlet_handle, n_posts)
            if not n_posts:
                logger.warning("@%s returned no posts: a dead or renamed handle reads as nothing to repost",
                               outlet_handle)
            for feed_item in (feed_resp.feed or []):
                if reposts_this_run >= MAX_REPOSTS_PER_RUN:
                    break

                # Skip reposts of other accounts' posts
                if getattr(feed_item, "reason", None) is not None:
                    continue

                post = feed_item.post
                cid = str(post.cid)
                uri = str(post.uri)

                # Age filter — indexed_at is an ISO string like "2026-06-28T02:34:12.345Z"
                indexed_at = getattr(post, "indexed_at", None)
                if indexed_at:
                    try:
                        post_time = datetime.fromisoformat(str(indexed_at).replace("Z", "+00:00"))
                        if post_time < cutoff:
                            continue
                    except (ValueError, TypeError):
                        pass

                seen = _seen(cid, db)
                if seen == REPOSTED:
                    continue
                if seen is None:
                    link = linked_article(post)
                    seen = resolve_key(link, sources, http) if link else ""
                    if seen is None:
                        continue  # not followed now: tried again next run
                    _record(cid, seen, db)
                matched_issue = sources.get(seen) if seen else None
                if matched_issue is None:
                    continue

                try:
                    client.repost(uri=uri, cid=cid)
                    client.like(uri=uri, cid=cid)
                    _record(cid, REPOSTED, db)
                    reposts_this_run += 1
                    logger.info(
                        "Engaged with @%s → issue '%s' (it cites %s)",
                        outlet_handle, (matched_issue.title or "")[:50], seen,
                    )
                except Exception:
                    logger.exception("Failed to repost/like post %s from %s", cid, outlet_handle)

    logger.info("Bluesky engagement done: %d repost(s) this run", reposts_this_run)
