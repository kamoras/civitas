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
    would; at most MAX_BSKY_ATTEMPTS tries in all; a run stops after
    MAX_CONSECUTIVE_REFUSALS in a row, each try being a login;
  - except the kinds in NO_RETRY_KINDS (election-night counts), whose
    words are only true for minutes;
  - and only on the Eastern day it was written. A post can say
    "Yesterday: …" (bluesky_poster._staleness_prefix); delivered a day late
    it would say something false. The feed entry keeps its own
    `published` time, so the same words stay true there.

Timing. The kinds in SCHEDULED_KINDS — the spotlight, the Congress
posts and the feature posts, whose words name their own date or none and
stay true for days — go to
Bluesky inside the hours U.S. users are most active
(app/data/bluesky_post_windows.json, measured by
scripts/fetch_bluesky_activity.py). One written outside them is held, as a
`pending` row, until the next window opens that Eastern day, and sent by
`deliver_pending` (half-hourly from the Congress sync, which runs through
the nightly pipeline, and hourly from the Action Center run); when no window is left that day it is sent at
once, so a held post never crosses into the next day's posts. A held post
whose window was missed (the pipeline process down) goes in the next window,
even the next day, rather than being dropped: its words carry their own
date. Because the hold is the row itself, a deploy can't lose it. Every
other kind is news (issues, race updates, election-night counts) and goes
out the moment it is written. The feed entry is never held: a feed is
pulled, so its reader already chooses when to read it, and holding it would
only make it late. Only Bluesky's chronological Following feed rewards
arriving while people are looking.
"""

import json
import logging
import pathlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

import httpx

from app.config import settings
from app.contact import SELF_FETCH_USER_AGENT
from app.models import BroadcastPost
from app.pipeline.analyze.bluesky_utils import (
    BSKY_MAX_IMAGE_BYTES,
    PostImage,
    fetch_og_card,
    publish_post,
    strip_hashtags,
)
from app.time_utils import COMMENT_DEADLINE_TZ, utcnow

logger = logging.getLogger(__name__)

SITE_URL = "https://civitas-research.org"

# What each kind of post is, in a reader's words. Keys are stored in
# BroadcastPost.kind.
KINDS: dict[str, str] = {
    "issue": "Action Center issues, in their sources' own words",
    "congress_day": "What Congress did on a session day with a record vote or a measure passed, once its record is final",
    "congress_week": "What Congress did in a week, and the bills that became law",
    "congress_ahead": "When each chamber meets in the coming week, and the bills waiting on it",
    "spotlight": "One member of Congress a day, with their scores",
    "race": "News about a race on the November ballot",
    "result": "Election-night counts, in each state's own figures",
    "special": "A special election for a seat in Congress, when the FEC's election calendar lists it",
    "trend": "On the record: the official record behind a topic trending on Bluesky",
    "feature": "New pages and features on Civitas, once each",
}

MAX_BSKY_ATTEMPTS = 3
# deliver_pending stops after this many refusals in a row (its docstring).
MAX_CONSECUTIVE_REFUSALS = 2
# Kinds deliver_pending never resends. An election-night post describes a
# count that moves every few minutes: resent an hour later, word for word,
# it could say a seat is changing party after the count reverted and the
# correction went out. The next event's post carries the count as it
# stands instead (live_results/bluesky.py).
NO_RETRY_KINDS = frozenset({"result"})
# Under the hour between Action Center runs, so the next run always
# qualifies, but long enough that the run which just failed to send a post
# doesn't try it again seconds later.
RETRY_AFTER = timedelta(minutes=45)

# Kinds whose Bluesky send waits for the posting window (module docstring).
# Each names its own date or none at all, so its words are as true at the
# window as when written; news kinds are never here.
SCHEDULED_KINDS = frozenset({"spotlight", "congress_day", "congress_week", "congress_ahead", "feature"})
# How long a held post whose window was missed stays sendable: a hold is
# hours, so this is only reached by the pipeline process being down for
# days, after which the post stays in the feed and isn't sent.
HELD_MAX_AGE = timedelta(days=2)

_WINDOWS_PATH = pathlib.Path(__file__).resolve().parent / "data" / "bluesky_post_windows.json"
_windows_cache: list[tuple[int, int]] | None = None

# How far back the hourly pass fills in a card the page didn't give at
# publish time (a fetch that failed, or a post from before cards were kept):
# a week of posts covers every feed's newest entries, and a page still
# unreadable after a week is left without one.
CARD_BACKFILL_WINDOW = timedelta(days=7)

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
        Feed("all", "Civitas Research", "Everything Civitas Research publishes.", tuple(KINDS)),
        Feed("issues", "Civitas Research: Action Center", KINDS["issue"] + ".", ("issue",)),
        Feed("congress", "Civitas Research: Congress",
             "What Congress did on each session day that had business, each week, and what the coming week holds, "
             "from the official record.",
             ("congress_day", "congress_week", "congress_ahead")),
        Feed("members", "Civitas Research: Member spotlight", KINDS["spotlight"] + ".", ("spotlight",)),
        Feed("elections", "Civitas Research: Elections",
             "News about races on the November ballot, special elections for seats in Congress as the FEC "
             "lists them, and the count on election night.",
             ("race", "result", "special")),
        Feed("trends", "Civitas Research: On the record",
             "When a U.S. policy topic trends on Bluesky, the official record behind it: a vote, a bill, "
             "a rule or the day's congressional record, in the record's own words.",
             ("trend",)),
        Feed("new", "Civitas Research: What's new", KINDS["feature"] + ", as they ship.", ("feature",)),
    )
}


def _bluesky_configured() -> bool:
    return bool(getattr(settings, "BSKY_HANDLE", "")) and bool(getattr(settings, "BSKY_APP_PASSWORD", ""))


def _post_day(moment: datetime) -> str:
    """The Eastern calendar date of a naive-UTC timestamp."""
    return moment.replace(tzinfo=timezone.utc).astimezone(_POST_DAY_TZ).date().isoformat()


def _windows() -> list[tuple[int, int]]:
    """The posting windows, as [start, end) Eastern hours; an end at or
    before its start runs past midnight. Empty when the file can't be read,
    and then nothing is held."""
    global _windows_cache
    if _windows_cache is None:
        try:
            _windows_cache = [(w["start"], w["end"]) for w in json.loads(_WINDOWS_PATH.read_text())["windows"]]
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Bluesky posting windows unreadable (%s); scheduled posts go out at once", _WINDOWS_PATH)
            _windows_cache = []
    return _windows_cache


def _hour_in_window(hour: int) -> bool:
    return any(start <= hour < end if start < end else hour >= start or hour < end for start, end in _windows())


def in_window(moment: datetime) -> bool:
    """Whether a naive-UTC moment falls in a posting window (Eastern, so
    the window follows daylight saving)."""
    return _hour_in_window(moment.replace(tzinfo=timezone.utc).astimezone(_POST_DAY_TZ).hour)


def release_at(kind: str, written: datetime) -> datetime:
    """When a post of `kind` written at `written` (naive UTC) goes to
    Bluesky: at once for a news kind, inside a window, or when no window is
    left that Eastern day; otherwise when the next window opens that day."""
    if kind not in SCHEDULED_KINDS or not _windows() or in_window(written):
        return written
    local = written.replace(tzinfo=timezone.utc).astimezone(_POST_DAY_TZ)
    for hour in range(local.hour + 1, 24):
        if _hour_in_window(hour):
            opens = local.replace(hour=hour, minute=0, second=0, microsecond=0)
            return opens.astimezone(timezone.utc).replace(tzinfo=None)
    return written


def _held(post: BroadcastPost) -> bool:
    return release_at(post.kind, post.published_at) > post.published_at


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
    bluesky: bool = True,
    image_url: str | None = None,
    alt_text: str | None = None,
    embed_kind: str | None = None,
) -> BroadcastPost:
    """Record a post, then deliver it to Bluesky if an account is set up.

    Always returns the stored post: once it is in the feed it is published,
    whatever Bluesky does with it. Commits the session (the caller's pending
    changes with it) before any network call. `bluesky=False` keeps a post
    to the feed — one that only makes sense beside an earlier post Bluesky
    never took. A scheduled kind written outside the posting windows is
    stored `pending` and sent by `deliver_pending` (module docstring).

    `image_url` (with `alt_text`) is a picture the post carries itself: the
    feed entry shows it in place of the page's card, and Bluesky gets it as
    an image embed when `embed_kind` is "image" ("card" sends the link card
    instead; trend_poster alternates the two to measure them). The image is
    read from inside the stack at send time (_post_image); one that can't
    be read goes as the card, never holding the post.
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
        bsky_status="pending" if bluesky and _bluesky_configured() else "off",
        image_url=image_url,
        alt_text=alt_text,
        embed_kind=embed_kind,
    )
    db.add(post)
    db.commit()
    capture_card(db, post)
    if post.bsky_status == "pending":
        if _held(post):
            logger.info("Bluesky send of %s %r held until %s UTC (posting window)",
                        kind, post.title[:80], release_at(kind, post.published_at).isoformat(timespec="minutes"))
        else:
            _deliver_to_bluesky(db, post)
    return post


def capture_card(db: Session, post: BroadcastPost) -> bool:
    """Keep the linked page's card on the post, for the feed entry: read
    after the post is stored, so a page that can't be read never holds the
    post back. False, and the card left unset for the hourly pass to fill
    (fill_missing_cards), when the page can't be read."""
    card = fetch_og_card(post.url)
    if card is None:
        return False
    post.card_image = card["image"]
    post.card_image_alt = card["image_alt"] or None
    post.card_description = card["description"] or None
    db.commit()
    return True


def fill_missing_cards(db: Session) -> int:
    """The card for each recent post that doesn't have one yet. Returns how
    many were filled."""
    rows = (
        db.query(BroadcastPost)
        .filter(
            BroadcastPost.card_image.is_(None),
            BroadcastPost.published_at >= utcnow() - CARD_BACKFILL_WINDOW,
            BroadcastPost.subject.notin_(withdrawn_subjects()),
        )
        .order_by(BroadcastPost.id)
        .all()
    )
    return sum(capture_card(db, post) for post in rows)


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
    image = _post_image(post) if post.embed_kind == "image" else None
    sent = publish_post(
        post.text, post.url,
        success_msg=f"Posted to Bluesky ({post.kind}): {post.title[:80]}",
        error_context=f"broadcast post {post.id}",
        **({"image": image} if image is not None else {}),
    )
    post.bsky_status = "sent" if sent else "failed"
    if sent:
        post.bsky_sent_at = utcnow()
        post.bsky_uri = getattr(sent, "uri", None) or None
        post.embed_kind = getattr(sent, "embed_kind", None) or post.embed_kind
    db.commit()
    return bool(sent)


def _png_size(data: bytes) -> tuple[int, int] | None:
    """A PNG's width and height from its IHDR chunk, or None for anything
    that isn't a PNG."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _post_image(post: BroadcastPost) -> PostImage | None:
    """The post's image, read from the frontend inside the stack (the
    public URL with FRONTEND_INTERNAL_URL in place of the site), or None —
    and the link card — when it can't be read, isn't a PNG, is over
    Bluesky's size limit, or has no alt text."""
    if not post.image_url or not post.alt_text or not post.image_url.startswith(SITE_URL):
        return None
    internal = settings.FRONTEND_INTERNAL_URL.rstrip("/") + post.image_url[len(SITE_URL):]
    try:
        resp = httpx.get(internal, timeout=30, headers={"User-Agent": SELF_FETCH_USER_AGENT})
        resp.raise_for_status()
    except Exception:
        logger.warning("Post image unreadable (%s); sending the link card", internal)
        return None
    size = _png_size(resp.content)
    if size is None or len(resp.content) > BSKY_MAX_IMAGE_BYTES:
        logger.warning("Post image unusable (%s, %d bytes); sending the link card", internal, len(resp.content))
        return None
    return PostImage(data=resp.content, alt=post.alt_text, width=size[0], height=size[1])


def deliver_pending(db: Session) -> int:
    """Send held posts whose window is open, and retry today's posts that
    Bluesky didn't take. Returns how many went out.

    A held post (a scheduled kind written outside the windows) goes only
    inside a window — the next one, even the next day, if its own was
    missed — for up to HELD_MAX_AGE; every other post only on the Eastern
    day it was written (module docstring).

    Stops after two refusals in a row: each try is a fresh login, and a
    Bluesky refusing twice an hour after the last tries is still down —
    trying every other post in the same burst only spends the account's
    login allowance, and the rest wait for the next run. Not after one: a
    refusal can be about the post itself (a link card Bluesky rejects),
    and that post would otherwise hold back every later one until its
    tries ran out."""
    if not _bluesky_configured():
        return 0
    today = _post_day(utcnow())
    rows = (
        db.query(BroadcastPost)
        .filter(
            BroadcastPost.bsky_status.in_(("pending", "failed")),
            BroadcastPost.bsky_attempts < MAX_BSKY_ATTEMPTS,
            or_(
                BroadcastPost.bsky_last_attempt_at.is_(None),
                BroadcastPost.bsky_last_attempt_at <= utcnow() - RETRY_AFTER,
            ),
            BroadcastPost.subject.notin_(withdrawn_subjects()),
            BroadcastPost.kind.notin_(NO_RETRY_KINDS),
            # Bounds the scan; the checks below are the rule.
            BroadcastPost.published_at >= utcnow() - HELD_MAX_AGE,
        )
        .order_by(BroadcastPost.id)
        .all()
    )
    sent = refused = 0
    open_now = in_window(utcnow())
    for post in rows:
        if _held(post):
            if not open_now:
                continue
        elif _post_day(post.published_at) != today:
            continue
        if _deliver_to_bluesky(db, post):
            sent += 1
            refused = 0
            continue
        db.refresh(post)
        if post.bsky_status != "failed":
            continue  # claimed by another sender
        refused += 1
        if refused >= MAX_CONSECUTIVE_REFUSALS:
            break
    return sent


def withdrawn_subjects() -> frozenset[str]:
    """Subjects of posts about withdrawn issues (app/retractions.py): kept
    in the table, since they were published, but never served in a feed or
    sent anywhere again."""
    from app.retractions import entries

    return frozenset(f"issue:{public_id}" for entry in entries() for public_id in entry["publicIds"])


def was_published(db: Session, subject: str) -> bool:
    """Whether anything about `subject` was ever published."""
    return db.query(BroadcastPost.id).filter(BroadcastPost.subject == subject).first() is not None


def source_was_published(db: Session, source_url: str) -> bool:
    """Whether a post restating this outside item was ever published."""
    return db.query(BroadcastPost.id).filter(BroadcastPost.source_url == source_url).first() is not None


def text_was_published(db: Session, text: str) -> bool:
    """Whether these exact words were ever published (as `publish` stores
    them, hashtags stripped), alone or as the start of a post: a race post
    cites its outlet after the sentence, and the same wire sentence from a
    second outlet is the same news."""
    t = strip_hashtags(text)
    return db.query(BroadcastPost.id).filter(
        or_(BroadcastPost.text == t, BroadcastPost.text.startswith(t + " ", autoescape=True)),
    ).first() is not None


def subjects_published_since(db: Session, kind: str, since: datetime) -> list[str]:
    """The subject of every `kind` post published since `since` (naive UTC),
    one per post, so its length is a count of posts."""
    return [
        row[0]
        for row in db.query(BroadcastPost.subject)
        .filter(BroadcastPost.kind == kind, BroadcastPost.published_at >= since)
        .all()
    ]


# Bluesky's public AppView, which answers getPosts without an account.
_BSKY_GET_POSTS = "https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts"
# How long after sending a post's likes are still read, how often, and the
# kinds read: the trend posts' image-vs-card trial (trend_poster) compares
# them by embed_kind, and the feature posts' volume rule (app/whats_new.py)
# is to be replayed with them. A post's likes come in its first days.
ENGAGEMENT_WINDOW = timedelta(days=14)
ENGAGEMENT_REFRESH = timedelta(hours=6)
ENGAGEMENT_KINDS = ("trend", "feature")


def refresh_engagement(db: Session) -> int:
    """Read the public like, repost and reply counts of recently sent posts
    of ENGAGEMENT_KINDS. Returns how many were updated; a failed read
    updates nothing (the counts keep their last values) rather than zeroing
    them."""
    rows = (
        db.query(BroadcastPost)
        .filter(
            BroadcastPost.kind.in_(ENGAGEMENT_KINDS),
            BroadcastPost.bsky_uri.isnot(None),
            BroadcastPost.bsky_sent_at >= utcnow() - ENGAGEMENT_WINDOW,
            or_(BroadcastPost.engagement_checked_at.is_(None),
                BroadcastPost.engagement_checked_at <= utcnow() - ENGAGEMENT_REFRESH),
        )
        .all()
    )
    updated = 0
    for start in range(0, len(rows), 25):  # getPosts takes at most 25
        batch = {post.bsky_uri: post for post in rows[start:start + 25]}
        try:
            resp = httpx.get(_BSKY_GET_POSTS, params=[("uris", uri) for uri in batch], timeout=15,
                             headers={"User-Agent": SELF_FETCH_USER_AGENT})
            resp.raise_for_status()
            found = resp.json()["posts"]
        except Exception:
            logger.warning("Bluesky engagement read failed; counts left as they were")
            continue
        for item in found:
            post = batch.get(item.get("uri"))
            if post is None:
                continue
            post.bsky_likes = int(item.get("likeCount") or 0)
            post.bsky_reposts = int(item.get("repostCount") or 0)
            post.bsky_replies = int(item.get("replyCount") or 0)
            post.engagement_checked_at = utcnow()
            updated += 1
        db.commit()
    return updated
