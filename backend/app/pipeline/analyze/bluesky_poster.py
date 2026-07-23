"""
Posts ActionIssues to Bluesky via the AT Protocol.

Posting triggers:
  - New issue: bsky_posted_at is None (either brand-new topic or topic with
    genuinely new articles since the last post, as determined by the pipeline)

The pipeline resets bsky_posted_at=None when a topic gets new articles
(primary_article_date advanced), so the poster never needs to evaluate
whether to re-post — that decision is already made upstream.

When the newest article driving an issue is from a prior day, the LLM is
instructed to make the timing clear in the post text.

Credentials: BSKY_HANDLE + BSKY_APP_PASSWORD in .env. If not set, this
module does nothing (allows running without a Bluesky account configured).
"""

import json
import logging
import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.analyze.bluesky_utils import publish_post
from app.pipeline.analyze.grounding import (
    grounding_violations,
    hedge_and_editorializing_violations,
)
from app.pipeline.analyze.ollama_client import call_llm
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

MAX_POST_CHARS = 240     # leaves room for the appended URL (~40 chars → total ~280)

_SYSTEM_PROMPT = (
    "You are a civic journalist writing brief, factual updates for the Civitas "
    "transparency platform. Civitas aggregates U.S. government data — voting "
    "records, campaign finance, floor speeches — into public scorecards. "
    "Your posts are non-partisan, data-grounded, and written for citizens who "
    "want to understand what their representatives are actually doing."
)


def _sanitize(text: str, budget: int) -> str:
    """Convert hashtags to plain text, enforce sentence-boundary character limit."""
    # Replace #word with word so inline hashtags don't break sentence grammar
    text = re.sub(r"#(\w+)", r"\1", text).strip()
    if len(text) > budget:
        trimmed = text[:budget]
        # Find the last sentence-ending punctuation anywhere in the trimmed text
        cut = -1
        for punct in (".", "!", "?"):
            idx = trimmed.rfind(punct)
            if idx > 0:
                cut = max(cut, idx + 1)
        if cut > 0:
            text = trimmed[:cut]
        else:
            last_space = trimmed.rfind(" ")
            text = trimmed[:last_space] if last_space > 0 else trimmed
    return text.strip()


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


def _build_source_context(source_names_json: str) -> str:
    names = json.loads(source_names_json or "[]")
    if not names:
        return "No sources listed."
    return ", ".join(names[:8])


def _build_facts_context(facts_json: str) -> str:
    facts = json.loads(facts_json or "[]")
    if not facts:
        return ""
    return "\n".join(f"- {f}" for f in facts[:4])


def _generate_new_post(issue, today: str) -> str | None:
    """Ask the LLM to write a Bluesky post for this issue.

    When the newest article driving the issue predates today, the LLM is
    instructed to frame the post so readers know the event isn't happening
    right now (e.g. "Yesterday: ..." or "On June 27: ...").
    """
    facts_text = _build_facts_context(issue.facts)
    sources_text = _build_source_context(issue.source_names)

    article_date = getattr(issue, "primary_article_date", None) or today
    is_stale = article_date < today

    staleness_instruction = ""
    if is_stale:
        staleness_instruction = (
            f"\nIMPORTANT: The events described occurred on {article_date}, not today ({today}). "
            "Open the post with a clear date reference so readers are not misled into thinking "
            "this is happening right now. Use a phrasing like 'Yesterday: ...' or "
            f"'On {article_date}: ...' at the start of the post."
        )

    user_prompt = f"""Write a Bluesky post summarizing this civic news issue.

Title: {issue.title}
Summary: {issue.summary or '(none)'}
Key facts:
{facts_text or '(none)'}
Sources: {sources_text}{staleness_instruction}

RULES — violating any rule means your response is unusable:
1. Use ONLY information from the Title, Summary, and Key facts above. \
Do not add details, statistics, or claims not stated there.
2. If the title or summary says something was dropped, ended, or resolved — \
write it as dropped/ended/resolved. Never contradict the title.
3. STRICT MAXIMUM: {MAX_POST_CHARS} characters total.
4. Write 1-3 complete sentences ending with proper punctuation.
5. No hashtags, no exclamation points, no editorializing, no "breaking news".
6. Neutral and non-partisan.
7. Report directly — never write "sources say," "reports indicate," "coverage \
shows," or similar. State facts as facts, not as something reports/coverage/
sources are saying.
8. Do not evaluate whether an action was warranted or justified, and do not \
speculate about its political purpose or effect.
9. Write about what actually happened or was said — not about "the coverage," \
"the discussion," or "the reporting" itself. Use specific names and numbers \
from the Key facts rather than vaguer substitutes.

Return JSON: {{"post": "<your post text>"}}"""

    # Everything the model was shown, plus the article date it was told to
    # reference — the grounding universe for the generated post.
    source_material = f"{issue.title}\n{issue.summary or ''}\n{facts_text}\n{article_date} {today}"

    retry_note = ""
    for attempt in range(2):
        result = call_llm(
            prompt_version="bsky_new_post_v3",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt + retry_note,
            # Public-facing surface: story-tier model when configured
            # (settings.OLLAMA_STORY_MODEL — two-tier design, 2026-07).
            model=settings.OLLAMA_STORY_MODEL or None,
            cache_key=None,  # never cache — these are time-sensitive
            db_session=None,
            max_tokens=256,
            num_ctx=2048,
        )
        if not result or not isinstance(result.get("post"), str):
            return None
        post = _sanitize(result["post"], MAX_POST_CHARS)

        # Posts publish publicly under the platform's name — verify rules
        # mechanically instead of trusting them. Any number or titled-official
        # reference the source material doesn't contain is a hallucination;
        # hedging attribution ("sources show") and editorializing ("was
        # warranted") are prompt-only rules the local model doesn't reliably
        # follow, same as _generate_full_story in action_center.py.
        reasons = grounding_violations(post, source_material) + hedge_and_editorializing_violations(post)
        if not reasons:
            return post

        from app.pipeline.analyze import action_metrics
        action_metrics.increment("bsky_post_grounding_rejections")
        logger.warning(
            "Bluesky post failed grounding for issue %s (attempt %d): %s | post: %s",
            issue.id, attempt + 1, "; ".join(reasons), post[:160],
        )
        retry_note = (
            "\n\nYour previous attempt was rejected because it included "
            f"{'; '.join(reasons)}. Rewrite using only the Title, Summary, and "
            "Key facts, report events directly instead of through phrases "
            "like 'sources show' or 'reports indicate,' do not describe any "
            "election, race, campaign, or challenge for office unless the Key "
            "facts say so, and do not evaluate whether any action was "
            "warranted or justified."
        )

    return None  # ungrounded twice — skip; the next refresh cycle retries


def _publish(text: str, issue) -> bool:
    """Post to Bluesky. Returns True on success."""
    text = re.sub(r"#(\w+)", r"\1", text).strip()  # final hashtag guard
    url = f"https://civitas-research.org/issue/{issue.id}"
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

    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from app.models import ActionIssue

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

        text = _generate_new_post(issue, today)
        if not text:
            continue

        if _is_near_duplicate(text, recent_texts):
            # Same story, nothing materially new to say — mark it handled so
            # the hourly pipeline doesn't regenerate and re-check it every run,
            # but publish nothing.
            from app.pipeline.analyze import action_metrics
            action_metrics.increment("bsky_posts_suppressed_near_duplicate")
            logger.info(
                "Suppressing near-duplicate Bluesky post for issue %s: %s",
                issue.id, issue.title[:80],
            )
            issue.bsky_posted_at = now
            issue.bsky_posted_rank = issue.rank
            continue

        if _publish(text, issue):
            issue.bsky_posted_at = now
            issue.bsky_posted_rank = issue.rank
            issue.bsky_last_post_text = text
            recent_texts.append(text)
            posted += 1

    # Commit unconditionally: a suppressed near-duplicate sets bsky_posted_at
    # without incrementing `posted`, and that state must persist so the issue
    # isn't regenerated and re-checked on every subsequent run.
    db.commit()
    return posted
