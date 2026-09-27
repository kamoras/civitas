"""
Posts ActionIssues to Bluesky via the AT Protocol.

Posting triggers:
  - New issue: bsky_posted_at is None (either brand-new topic or topic with
    genuinely new articles since the last post, as determined by the pipeline)

The pipeline resets bsky_posted_at=None when a topic gets new articles
(primary_article_date advanced), so the poster never needs to evaluate
whether to re-post — that decision is already made upstream.

The post is the issue's verified lede, verbatim (or its real headline), not
model prose — see _compose_new_post. When the newest article driving an
issue is from a prior day, the post opens with "Yesterday:" or "On <date>:".

Credentials: BSKY_HANDLE + BSKY_APP_PASSWORD in .env. If not set, this
module does nothing (allows running without a Bluesky account configured).
"""

import json
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import settings
from app.issue_ids import to_public_id
from app.models import ActionIssue
from app.pipeline.analyze import action_metrics
from app.pipeline.analyze.bluesky_utils import publish_post, strip_hashtags
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

MAX_POST_CHARS = 240     # leaves room for the appended URL (~40 chars → total ~280)

# A repost whose body shares at least this fraction of its words with an
# already-published post is treated as a near-duplicate and suppressed. Set
# high enough that a genuine development (a new vote, a resolution, a fresh
# number) still introduces enough new vocabulary to clear the bar, but low
# enough to catch the same facts reworded. The failure directions are
# asymmetric and both tolerable: a missed duplicate posts exactly as it did
# before this guard existed, and a false positive suppresses one update
# (logged, so it's observable) — the user asked us to err against repeats.
_NEAR_DUP_JACCARD = 0.65

_WORD_RE = re.compile(r"[a-z0-9]+")


def _post_word_set(text: str) -> set[str]:
    """Lowercased alphanumeric word set for near-duplicate comparison."""
    return set(_WORD_RE.findall((text or "").lower()))


def _is_near_duplicate(candidate: str, prior_texts: list[str]) -> bool:
    """True if ``candidate`` reads as essentially the same post as any of
    ``prior_texts``, by word-set Jaccard overlap.

    Reposts fire whenever a topic gets a newer-dated article (the pipeline
    resets bsky_posted_at upstream), but ongoing coverage of one story often
    carries the same title/summary/facts day to day, so the generated post
    says the same thing again. Comparing against what we actually published
    catches that regardless of which row or run it came from.
    """
    cand = _post_word_set(candidate)
    if not cand:
        return False
    for prior in prior_texts:
        other = _post_word_set(prior)
        if not other:
            continue
        overlap = len(cand & other) / len(cand | other)
        if overlap >= _NEAR_DUP_JACCARD:
            return True
    return False


def _staleness_prefix(article_date: str | None, today: str) -> str:
    """"Yesterday: " or "On July 24: " when the newest article predates
    today, so a reader doesn't take a past event for a live one; "" when it
    is today's (or the date is unreadable). Only the one phrasing that is
    true is ever used — "yesterday" two days on is a wrong fact."""
    if not article_date or article_date >= today:
        return ""
    try:
        event = datetime.strptime(article_date, "%Y-%m-%d")
        days = (datetime.strptime(today, "%Y-%m-%d") - event).days
    except ValueError:
        return ""
    if days == 1:
        return "Yesterday: "
    return f"On {event:%B} {event.day}: "


def _facts(raw) -> list[str]:
    try:
        facts = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [f.strip() for f in facts if isinstance(f, str) and f.strip()] if isinstance(facts, list) else []


def _new_facts(issue) -> list[str]:
    """Facts added since the last post, in order. A repost is released
    upstream only when the facts gained new information (a name, a figure,
    a development) over `bsky_posted_facts`, the facts as of that post — so
    one of these is what the repost has to say. The lede often hasn't
    changed, and reposting it would only be suppressed as a duplicate."""
    if issue.bsky_posted_facts is None:
        return []
    posted = set(_facts(issue.bsky_posted_facts))
    return [f for f in _facts(issue.facts) if f not in posted]


def _compose_new_post(issue, today: str) -> str | None:
    """The post for this issue: a verified claim, verbatim, or None.

    Not written by a model. The lede (issue.summary) is a claim
    claims.build_lede took word for word from a source and post_composer
    verified is asserted of its actor; the fallback is the top article's
    real headline (issue.title). Model prose written from those same verified
    facts is what published a relationship no source stated (issues 748,
    750, 751) past the shared grounding checks, which is why the full story
    stopped being written (claims.build_story) — and a post is the most
    public surface of all. The only words added are the date prefix.

    A repost leads with the first fact the last post didn't carry — also a
    verbatim claim (claims.build_facts) — since new information is what
    released it. A claim too long for a post falls through to the next
    candidate, ending at the headline, rather than being cut: truncating a
    claim can drop the qualifier that makes it true. None when nothing
    fits, counted so the gap is visible.
    """
    prefix = _staleness_prefix(getattr(issue, "primary_article_date", None), today)
    for body in (*_new_facts(issue), issue.summary, issue.title):
        text = strip_hashtags((body or "").strip())
        if text and len(prefix) + len(text) <= MAX_POST_CHARS:
            return prefix + text
    action_metrics.increment("bsky_posts_skipped_too_long")
    return None


def _publish(text: str, issue) -> bool:
    """Post to Bluesky. Returns True on success."""
    text = strip_hashtags(text)  # final guard, independent of what ran upstream
    url = f"https://civitas-research.org/issue/{to_public_id(issue.id)}"
    return publish_post(
        text, url,
        success_msg=f"Posted to Bluesky: {issue.title[:80]}",
        error_context=f"issue {issue.id}",
    )


def process_issues_for_bluesky(issues: list, db: Session) -> int:
    """Post new/updated issues to Bluesky.

    The pipeline already decides which issues deserve a post by setting
    bsky_posted_at=None (new topic or topic with genuinely new articles).
    This function just executes those posts.
    """
    if not getattr(settings, "BSKY_HANDLE", "") or not getattr(settings, "BSKY_APP_PASSWORD", ""):
        return 0  # fast-path: no credentials configured

    _US_EAST = ZoneInfo("America/New_York")
    today = datetime.now(tz=_US_EAST).strftime("%Y-%m-%d")

    now = utcnow()
    posted = 0

    # Bodies of every post published in the last few days, so a repost (or a
    # near-identical second trending topic) that would say the same thing as a
    # recent post is suppressed instead of duplicated. Loaded once per run.
    recent_cutoff = now - timedelta(days=3)
    recent_texts: list[str] = [
        row[0]
        for row in db.query(ActionIssue.bsky_last_post_text)
        .filter(
            ActionIssue.bsky_posted_at.isnot(None),
            ActionIssue.bsky_posted_at >= recent_cutoff,
            ActionIssue.bsky_last_post_text.isnot(None),
        )
        .all()
        if row[0]
    ]

    for issue in issues:
        if issue.bsky_posted_at is not None:
            continue  # pipeline didn't flag this issue for posting

        text = _compose_new_post(issue, today)
        if not text:
            continue

        if _is_near_duplicate(text, recent_texts):
            # Same story, nothing materially new to say — mark it handled so
            # the hourly pipeline doesn't regenerate and re-check it every run,
            # but publish nothing.
            action_metrics.increment("bsky_posts_suppressed_near_duplicate")
            logger.info(
                "Suppressing near-duplicate Bluesky post for issue %s: %s",
                issue.id, issue.title[:80],
            )
            issue.bsky_posted_at = now
            issue.bsky_posted_rank = issue.rank
            # These facts were judged to have nothing new to say, so they
            # become the baseline the next repost is measured against —
            # otherwise the same suppressed update is regenerated (an LLM
            # call) and re-suppressed on every later article-date advance.
            issue.bsky_posted_facts = issue.facts
            continue

        if _publish(text, issue):
            issue.bsky_posted_at = now
            issue.bsky_posted_rank = issue.rank
            issue.bsky_last_post_text = text
            # Pin what readers have now been told; the repost gate upstream
            # measures the next run's facts against this, NOT against the
            # `facts` column, which every refresh overwrites whether or not
            # anything was posted (see _apply_matched_issue_update).
            issue.bsky_posted_facts = issue.facts
            recent_texts.append(text)
            posted += 1

    # Commit unconditionally: a suppressed near-duplicate sets bsky_posted_at
    # without incrementing `posted`, and that state must persist so the issue
    # isn't regenerated and re-checked on every subsequent run.
    db.commit()
    return posted
