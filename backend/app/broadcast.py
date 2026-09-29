"""Everything Civitas publishes goes through `publish`: one record, then
the channels.

A post is stored first, as a `BroadcastPost` — which is the Atom feed's
entry (`api/feed.py`) — and only then delivered to Bluesky, when an
account is configured. Before this, a post existed only as a Bluesky side
effect: it was never written anywhere Civitas could serve it again, and
each posting module skipped composing it at all when Bluesky credentials
were unset. Now the feed is the record, Bluesky is one reader of it, and
anyone else (a Discord bot, a feed reader) pulls the same entries without
Civitas knowing they exist (AGENTS.md §8).

The posting modules keep deciding WHAT to publish and WHEN (their own
dedupe and budget state, unchanged); this module decides where it goes.

Delivery to Bluesky is at most once per attempt and bounded:
  - the row is committed before any network call, so a crash mid-send can
    never produce a second post (a row left `sending` is not retried);
  - a failed send is retried by `deliver_pending` (run hourly, from the
    Action Center run), no sooner than RETRY_AFTER after the last try, so
    the tries are an hour apart and ride out a longer outage than a burst
    would; at most MAX_BSKY_ATTEMPTS tries in all;
  - and only on the Eastern day it was written. A post can say
    "Yesterday: …" (bluesky_poster._staleness_prefix); delivered a day late
    it would say something false. The feed entry keeps its own
    `published` time, so the same words stay true there.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models import BroadcastPost
from app.pipeline.analyze.bluesky_utils import publish_post, strip_hashtags
from app.time_utils import COMMENT_DEADLINE_TZ, utcnow

logger = logging.getLogger(__name__)

SITE_URL = "https://civitas-research.org"

# What each kind of post is, in a reader's words. Keys are stored in
# BroadcastPost.kind.
KINDS: dict[str, str] = {
    "issue": "Action Center issues, in their sources' own words",
    "congress_day": "What Congress did on a session day, once its record is final",
    "congress_week": "What Congress did in a week, and the bills that became law",
    "spotlight": "One member of Congress a day, with their scores",
    "race": "News about a race on the November ballot",
}

MAX_BSKY_ATTEMPTS = 3
# Under the hour between Action Center runs, so the next run always
# qualifies, but long enough that the run which just failed to send a post
# doesn't try it again seconds later.
RETRY_AFTER = timedelta(minutes=45)

# The day a post's words are true on (see the module docstring). Eastern,
# like every other day boundary the posts use.
_POST_DAY_TZ = COMMENT_DEADLINE_TZ


@dataclass(frozen=True)
class Feed:
    slug: str
    title: str
    description: str
    kinds: tuple[str, ...]


# The topic feeds. A reader who wants less than everything picks a feed,
# so the filtering lives in the URL they subscribe to and nothing about
# them is kept here. Per-state feeds (`/feed/states/XX.xml`) are generated
# from the `state` column rather than listed.
FEEDS: dict[str, Feed] = {
    f.slug: f for f in (
        Feed("all", "Civitas", "Everything Civitas publishes.", tuple(KINDS)),
        Feed("issues", "Civitas: Action Center", KINDS["issue"] + ".", ("issue",)),
        Feed("congress", "Civitas: Congress",
             "What Congress did each session day and each week, from the official record.",
             ("congress_day", "congress_week")),
        Feed("members", "Civitas: Member spotlight", KINDS["spotlight"] + ".", ("spotlight",)),
        Feed("elections", "Civitas: Elections", KINDS["race"] + ".", ("race",)),
    )
}


def _bluesky_configured() -> bool:
    return bool(getattr(settings, "BSKY_HANDLE", "")) and bool(getattr(settings, "BSKY_APP_PASSWORD", ""))


def _post_day(moment: datetime) -> str:
    """The Eastern calendar date of a naive-UTC timestamp."""
    return moment.replace(tzinfo=timezone.utc).astimezone(_POST_DAY_TZ).date().isoformat()


def publish(
    db: Session,
    *,
    kind: str,
    subject: str,
    title: str,
    text: str,
    url: str,
    state: str | None = None,
    source_url: str | None = None,
) -> BroadcastPost:
    """Record a post, then deliver it to Bluesky if an account is set up.

    Always returns the stored post: once it is in the feed it is published,
    whatever Bluesky does with it. Commits the session (the caller's pending
    changes with it) before any network call.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown broadcast kind {kind!r}")
    # Final guard at the publishing boundary, independent of what ran
    # upstream: no post carries a hashtag, on any channel.
    post = BroadcastPost(
        kind=kind,
        subject=subject,
        title=strip_hashtags(title),
        text=strip_hashtags(text),
        url=url,
        source_url=source_url,
        state=state.upper() if state else None,
        published_at=utcnow(),
        bsky_status="pending" if _bluesky_configured() else "off",
    )
    db.add(post)
    db.commit()
    if post.bsky_status == "pending":
        _deliver_to_bluesky(db, post)
    return post


def _deliver_to_bluesky(db: Session, post: BroadcastPost) -> bool:
    # Claimed with one conditional UPDATE, not a read then a write: the
    # hourly retry (deliver_pending) runs on the Action Center's thread while
    # the Congress sync and the election refresh publish on theirs, and a
    # retry that read this row as "pending" between publish()'s two commits
    # would otherwise send it a second time. Whoever flips it to "sending"
    # sends it; everyone else leaves it alone.
    claimed = (
        db.query(BroadcastPost)
        .filter(BroadcastPost.id == post.id, BroadcastPost.bsky_status.in_(("pending", "failed")))
        .update(
            {
                "bsky_status": "sending",
                "bsky_attempts": BroadcastPost.bsky_attempts + 1,
                "bsky_last_attempt_at": utcnow(),
            },
            synchronize_session=False,
        )
    )
    db.commit()
    if claimed != 1:
        return False
    db.refresh(post)
    ok = publish_post(
        post.text, post.url,
        success_msg=f"Posted to Bluesky ({post.kind}): {post.title[:80]}",
        error_context=f"broadcast post {post.id}",
    )
    post.bsky_status = "sent" if ok else "failed"
    if ok:
        post.bsky_sent_at = utcnow()
    db.commit()
    return ok


def deliver_pending(db: Session) -> int:
    """Retry today's posts that Bluesky didn't take. Returns how many went out."""
    if not _bluesky_configured():
        return 0
    today = datetime.now(_POST_DAY_TZ).date().isoformat()
    rows = (
        db.query(BroadcastPost)
        .filter(
            BroadcastPost.bsky_status.in_(("pending", "failed")),
            BroadcastPost.bsky_attempts < MAX_BSKY_ATTEMPTS,
            or_(
                BroadcastPost.bsky_last_attempt_at.is_(None),
                BroadcastPost.bsky_last_attempt_at <= utcnow() - RETRY_AFTER,
            ),
            # Bounds the scan; the Eastern-day check below is the rule.
            BroadcastPost.published_at >= utcnow() - timedelta(days=1),
        )
        .order_by(BroadcastPost.id)
        .all()
    )
    sent = 0
    for post in rows:
        if _post_day(post.published_at) != today:
            continue
        if _deliver_to_bluesky(db, post):
            sent += 1
    return sent


def was_published(db: Session, subject: str) -> bool:
    """Whether anything about `subject` was ever published."""
    return db.query(BroadcastPost.id).filter(BroadcastPost.subject == subject).first() is not None


def source_was_published(db: Session, source_url: str) -> bool:
    """Whether a post restating this outside item was ever published."""
    return db.query(BroadcastPost.id).filter(BroadcastPost.source_url == source_url).first() is not None


def subjects_published_since(db: Session, kind: str, since: datetime) -> list[str]:
    """The subject of every `kind` post published since `since` (naive UTC),
    one per post, so its length is a count of posts."""
    return [
        row[0]
        for row in db.query(BroadcastPost.subject)
        .filter(BroadcastPost.kind == kind, BroadcastPost.published_at >= since)
        .all()
    ]
