"""
Engage with news outlet posts on Bluesky.

When a major outlet's recent post matches an active Civitas action center
issue (cosine similarity >= ENGAGE_THRESHOLD), we repost it and like it.
This keeps the Civitas feed active between original posts and surfaces
relevant reporting within the civic transparency context.

Design constraints:
- Only original posts (no replies, no reposts of others) from the last 24h
- At most MAX_REPOSTS_PER_RUN reposts per hourly pipeline run
- Each post is processed at most once (tracked via ApiCache)
- Runs silently if credentials are not configured
"""

import logging
from datetime import datetime, timedelta, UTC

import numpy as np
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ActionIssue, ApiCache

logger = logging.getLogger(__name__)

# Minimum cosine similarity between outlet post text and active issue title
# to trigger engagement. Set conservatively to avoid off-topic reposts.
ENGAGE_THRESHOLD = 0.78

# Hard cap on reposts per pipeline run (runs hourly) — keeps the feed human-paced
MAX_REPOSTS_PER_RUN = 3

# Only consider posts from within this window
POST_MAX_AGE_HOURS = 24

# Cache tier used to record which Bluesky posts have been processed
_CACHE_TIER = "bsky_engagement"

# Major news outlets with reliable Bluesky presence.
# Handles are resolved by atproto — use the domain-style handles that
# verified outlets register (e.g. apnews.com maps to their Bluesky account).
NEWS_OUTLET_HANDLES: list[str] = [
    "apnews.com",
    "npr.org",
    "newshour.bsky.social",
]


def _already_processed(cid: str, db: Session) -> bool:
    return db.get(ApiCache, (_CACHE_TIER, cid)) is not None


def _mark_processed(cid: str, db: Session) -> None:
    if not _already_processed(cid, db):
        try:
            db.add(ApiCache(tier=_CACHE_TIER, cache_key=cid, data_json="1"))
            db.commit()
        except Exception:
            db.rollback()


def engage_with_news_posts(db: Session) -> None:
    """Repost and like outlet posts that match active action center issues.

    Called from the action center refresh pipeline after issues are updated.
    No-op if Bluesky credentials are not configured.
    """
    handle = getattr(settings, "BSKY_HANDLE", "")
    app_password = getattr(settings, "BSKY_APP_PASSWORD", "")
    if not handle or not app_password:
        return

    # Load currently active issues
    active_issues = (
        db.query(ActionIssue)
        .filter(ActionIssue.is_current == True)  # noqa: E712
        .all()
    )
    if not active_issues:
        return

    from app.pipeline.analyze.action_center import _embed_texts

    issue_titles = [i.title for i in active_issues]
    issue_embs = np.array(_embed_texts(issue_titles))  # shape (N, D)

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
        "Bluesky engagement: checking %d outlets against %d active issues",
        len(NEWS_OUTLET_HANDLES), len(active_issues),
    )

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
                except Exception:
                    pass

            if _already_processed(cid, db):
                continue

            text = getattr(post.record, "text", "") or ""
            if not text.strip():
                _mark_processed(cid, db)
                continue

            # Embed post text and find best-matching active issue
            post_emb = _embed_texts([text])[0]
            sims = issue_embs @ post_emb
            best_idx = int(np.argmax(sims))
            best_sim = float(sims[best_idx])

            # Always mark as processed so we don't re-evaluate next run
            _mark_processed(cid, db)

            if best_sim < ENGAGE_THRESHOLD:
                logger.debug(
                    "@%s post below threshold (%.2f < %.2f): %s",
                    outlet_handle, best_sim, ENGAGE_THRESHOLD, text[:60],
                )
                continue

            matched_issue = active_issues[best_idx]

            try:
                client.repost(uri=uri, cid=cid)
                client.like(uri=uri, cid=cid)
                reposts_this_run += 1
                logger.info(
                    "Engaged with @%s (sim=%.2f) → issue '%s' | post: %s",
                    outlet_handle, best_sim, matched_issue.title[:50], text[:80],
                )
            except Exception:
                logger.exception("Failed to repost/like post %s from %s", cid, outlet_handle)

    logger.info("Bluesky engagement done: %d repost(s) this run", reposts_this_run)
