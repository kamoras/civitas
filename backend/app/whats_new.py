"""New on Civitas Research: each user-facing feature, announced once.

The source is app/data/whats_new.json, one entry per feature (its
`_contract` names the fields). Writing the entry is part of shipping the
feature; nothing here, or anywhere else, is written per feature. The same
entries are /changelog's "New on the site" list (GET /api/whats-new), the
What's new feed and, once each, a post (kind "feature", through
app.broadcast.publish, so the feed first and then Bluesky).

The post is a fixed template around the entry's own words:
"New: <title>. <summary>" and the page's link (the account already says
whose site it is; the feed entry's title keeps "New on Civitas Research:"), whose card
(the page's Open Graph image) Bluesky and the feed entry show. No model
writes any of it; tests/test_whats_new.py holds every entry to the
shown-text rules (no em dash, no emoji, no pronoun) and to the post's length.

When, at most (post_whats_new, hourly from the scheduler):
  - inside a posting window (app/data/bluesky_post_windows.json): a
    feature is not news, so it waits for the hours readers are on;
  - at most MAX_PER_DAY a day (Eastern), newest entry first;
  - only while the last 24 hours carried no more of every other kind of post
    than the median day of the TYPICAL_DAYS before today. A feature post
    then never makes a busy day busier, and the cap moves with the other
    posting rules rather than being typed here (AGENTS.md §3a);
  - only an entry released within ANNOUNCE_WITHIN: one that waited longer
    is no longer news and stays on /changelog unposted. That is also the
    backfill: on the day this shipped, entries from the fortnight before
    drained one a day and older ones were never posted.

Why that volume. Replayed against the account's public post history
(public.api.bsky.app getAuthorFeed, 1,129 posts from 2026-06-26 to
2026-10-16): interactions a post do not fall with how many posts went out
that day (0.24 for the day's first post, 0.20 second, 0.13 third, 0.28
fourth, 0.26 sixth and later), so the history gives no cap to fit, and 96.6%
of the last fortnight's posts drew no interaction at all. The rule therefore
adds the least it can: one post, on days no busier than usual. Over the 28
days to 2026-10-15 it would have posted on 25 and held 3, about one a day at
10:00 Eastern, so a fortnight's dozen features drain in under two weeks,
inside ANNOUNCE_WITHIN. Feature posts' likes are read like trend posts'
(broadcast.ENGAGEMENT_KINDS), so the rule can be replayed again with them.

A page that can't be reached is an outage, not a launch: the run stops and
the next hour tries again. A page that answers 404 (an entry naming the
wrong path) is skipped, with one ops alert per entry, and the next entry is
tried.
"""

import json
import logging
import statistics
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from app import broadcast
from app.config import settings
from app.contact import SELF_FETCH_USER_AGENT
from app.models import BroadcastPost
from app.ops_alerts import send_ops_alert
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

KIND = "feature"
SUBJECT_PREFIX = "feature:"
MAX_PER_DAY = 1
# The days whose median sets the busy-day cap: two weeks, so a weekday and
# a weekend of each kind are in it.
TYPICAL_DAYS = 14
# How long after its release an entry may still be posted (module docstring).
ANNOUNCE_WITHIN = timedelta(days=28)

_PATH = Path(__file__).parent / "data" / "whats_new.json"


@lru_cache(maxsize=1)
def entries() -> list[dict]:
    """Every entry, newest first, as the file lists them."""
    return json.loads(_PATH.read_text())["entries"]


def post_text(entry: dict) -> str:
    return f"New: {entry['title']}. {entry['summary']}"


def page_url(entry: dict) -> str:
    return f"{broadcast.SITE_URL}{entry['url']}"


def _subject(entry: dict) -> str:
    return SUBJECT_PREFIX + entry["id"]


def _eastern_day(moment: datetime) -> date:
    return date.fromisoformat(broadcast._post_day(moment))


def _other_posts_since(db: Session, since: datetime) -> list[datetime]:
    return [
        row[0]
        for row in db.query(BroadcastPost.published_at)
        .filter(BroadcastPost.kind != KIND, BroadcastPost.published_at >= since)
        .all()
    ]


def busy_day_cap(db: Session, now: datetime) -> float:
    """The median count of every other kind of post over the TYPICAL_DAYS
    Eastern days before today (days with none count as none)."""
    today = _eastern_day(now)
    counts = {today - timedelta(days=i): 0 for i in range(1, TYPICAL_DAYS + 1)}
    # A day's margin on the scan: the bucketing below is the rule.
    for moment in _other_posts_since(db, now - timedelta(days=TYPICAL_DAYS + 1)):
        day = _eastern_day(moment)
        if day in counts:
            counts[day] += 1
    return statistics.median(counts.values())


def _due(db: Session, now: datetime) -> list[dict]:
    today = now.date()
    return [
        e for e in entries()
        if today - ANNOUNCE_WITHIN <= date.fromisoformat(e["date"]) <= today
        and not broadcast.was_published(db, _subject(e))
    ]


def page_status(entry: dict) -> int | None:
    """The page's HTTP status, read from the frontend inside the stack, or
    None when it can't be reached or answers 5xx (an outage)."""
    path = urlsplit(entry["url"]).path
    try:
        resp = httpx.get(settings.FRONTEND_INTERNAL_URL.rstrip("/") + path, timeout=30,
                         follow_redirects=True, headers={"User-Agent": SELF_FETCH_USER_AGENT})
    except Exception:
        return None
    return None if resp.status_code >= 500 else resp.status_code


def post_whats_new(db: Session) -> str:
    """Post the newest due entry if the volume rule allows one now
    (module docstring). Returns what it did, for the log."""
    now = utcnow()
    if not broadcast.in_window(now):
        return "outside the posting window"
    today = _eastern_day(now)
    posted_today = sum(
        _eastern_day(row[0]) == today
        for row in db.query(BroadcastPost.published_at)
        .filter(BroadcastPost.kind == KIND, BroadcastPost.published_at >= now - timedelta(days=1))
        .all()
    )
    if posted_today >= MAX_PER_DAY:
        return "already posted today"
    due = _due(db, now)
    if not due:
        return "nothing due"
    recent, cap = len(_other_posts_since(db, now - timedelta(hours=24))), busy_day_cap(db, now)
    if recent > cap:
        return f"held: {recent} posts in the last 24 hours, typical day {cap:g}"
    for entry in due:
        status = page_status(entry)
        if status is None:
            return f"held: {entry['url']} unreachable"
        if status != 200:
            send_ops_alert(
                f"What's new entry {entry['id']!r} points at a page answering {status}",
                f"app/data/whats_new.json names {entry['url']}, which answers {status}; "
                "the entry is skipped until it names a page that loads.",
                dedupe_key=f"whats-new-page:{entry['id']}",
            )
            continue
        broadcast.publish(
            db, kind=KIND, subject=_subject(entry), title=f"New on Civitas Research: {entry['title']}",
            text=post_text(entry), url=page_url(entry),
        )
        return f"posted {entry['id']}"
    return "no due entry has a page that loads"
