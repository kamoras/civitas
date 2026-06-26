"""
Posts ActionIssues to Bluesky via the AT Protocol.

Two triggers:
  - New issue: first time this date+rank slot has been posted
  - Surge: issue climbed to rank #1 from rank 3+ since last post, with
    a 6-hour cooldown between posts on the same issue

The local LLM synthesizes the collection of related articles and writes
the post text rather than filling a template. For surges, the LLM also
decides whether the change is substantive enough to warrant a post.

Credentials: BSKY_HANDLE + BSKY_APP_PASSWORD in .env. If not set, this
module does nothing (allows running without a Bluesky account configured).
"""

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.analyze.ollama_client import call_llm

logger = logging.getLogger(__name__)

SURGE_COOLDOWN_HOURS = 6
SURGE_MIN_RANK_DROP = 2  # old rank must exceed current rank by at least this
MAX_POST_CHARS = 240     # leaves room for the appended URL (~40 chars → total ~280)

_SYSTEM_PROMPT = (
    "You are a civic journalist writing brief, factual updates for the Civitas "
    "transparency platform. Civitas aggregates U.S. government data — voting "
    "records, campaign finance, floor speeches — into public scorecards. "
    "Your posts are non-partisan, data-grounded, and written for citizens who "
    "want to understand what their representatives are actually doing."
)


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


def _generate_new_post(issue) -> str | None:
    """Ask the LLM to synthesize the issue and write a Bluesky post."""
    facts_text = _build_facts_context(issue.facts)
    sources_text = _build_source_context(issue.source_names)

    user_prompt = f"""A new civic issue has emerged and is being covered by multiple news outlets.
Write a Bluesky post that explains it clearly.

Issue: {issue.title}
Summary: {issue.summary or '(none)'}
Key facts:
{facts_text or '(none)'}
News sources covering this: {sources_text}

Requirements:
- STRICT MAXIMUM: {MAX_POST_CHARS} characters total (count carefully — this is a hard limit)
- Write 1-3 complete sentences that end with proper punctuation
- Factual and non-partisan
- No hashtags, no exclamation points, no editorializing
- Write in present tense

Return JSON: {{"post": "<text — must be complete sentences and under {MAX_POST_CHARS} chars>"}}"""

    result = call_llm(
        prompt_version="bsky_new_post_v1",
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        cache_key=None,  # never cache — these are time-sensitive
        db_session=None,
        max_tokens=256,
        num_ctx=2048,
    )
    if not result or not isinstance(result.get("post"), str):
        return None
    text = result["post"].strip()
    # Enforce character limit: trim to last sentence boundary within budget
    if len(text) > MAX_POST_CHARS:
        trimmed = text[:MAX_POST_CHARS]
        cut = -1
        for punct in (".", "!", "?"):
            idx = trimmed.rfind(punct)
            if idx > len(trimmed) // 2:
                cut = max(cut, idx + 1)
        if cut > 0:
            text = trimmed[:cut]
        else:
            last_space = trimmed.rfind(" ")
            text = trimmed[:last_space] if last_space > 0 else trimmed
    return text


def _generate_surge_post(issue, old_rank: int) -> tuple[bool, str | None]:
    """
    Ask the LLM to evaluate whether a surge is worth posting and write the text.
    Returns (should_post, post_text).
    """
    facts_text = _build_facts_context(issue.facts)
    sources_text = _build_source_context(issue.source_names)
    source_count = len(json.loads(issue.source_names or "[]"))

    user_prompt = f"""A civic issue previously ranked #{old_rank} has surged to rank #1 in our analysis.
It is now the top story based on news volume and legislative activity.

Issue: {issue.title}
Summary: {issue.summary or '(none)'}
Key facts:
{facts_text or '(none)'}
Now covered by {source_count} news sources: {sources_text}

Decide whether this surge represents a genuinely new development worth posting about
(e.g. new legislation introduced, vote happened, significant escalation in news coverage)
versus routine churn. If yes, write a post focused on what has changed or escalated.

Requirements for the post:
- Under {MAX_POST_CHARS} characters
- Factual and non-partisan
- Explain what is new or has escalated, not just what the issue is
- No hashtags, no editorializing

Return JSON: {{"should_post": true/false, "post": "<text if should_post>", "reasoning": "<one sentence>"}}"""

    result = call_llm(
        prompt_version="bsky_surge_post_v1",
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        cache_key=None,
        db_session=None,
        max_tokens=256,
        num_ctx=2048,
    )
    if not result:
        return False, None
    should = bool(result.get("should_post", False))
    raw = result.get("post", "").strip() if should else None
    if raw and len(raw) > MAX_POST_CHARS:
        trimmed = raw[:MAX_POST_CHARS]
        cut = -1
        for punct in (".", "!", "?"):
            idx = trimmed.rfind(punct)
            if idx > len(trimmed) // 2:
                cut = max(cut, idx + 1)
        raw = trimmed[:cut] if cut > 0 else trimmed[:trimmed.rfind(" ") or MAX_POST_CHARS]
    text = raw
    logger.debug(
        "Surge judgment for '%s': should_post=%s reason=%s",
        issue.title[:60], should, result.get("reasoning", "")
    )
    return should, text


def _publish(text: str, issue) -> bool:
    """Post to Bluesky. Returns True on success."""
    handle = getattr(settings, "BSKY_HANDLE", "")
    app_password = getattr(settings, "BSKY_APP_PASSWORD", "")
    if not handle or not app_password:
        logger.debug("Bluesky credentials not set — skipping publish")
        return False

    url = f"https://civitas.paramain.com/issue/{issue.id}"
    full_text = f"{text}\n\n{url}"

    # Hard truncate the generated body if the LLM overshot, preserving the URL.
    # Prefer sentence boundary > word boundary so we never cut mid-sentence.
    if len(full_text) > 300:
        budget = 300 - len(url) - 2  # 2 for \n\n
        trimmed = text[:budget]
        # Try to end on a sentence boundary
        cut = -1
        for punct in (".", "!", "?"):
            idx = trimmed.rfind(punct)
            if idx > len(trimmed) // 2:
                cut = max(cut, idx + 1)  # include the punctuation char
        if cut > 0:
            trimmed = trimmed[:cut]
        else:
            last_space = trimmed.rfind(" ")
            if last_space > 0:
                trimmed = trimmed[:last_space]
        full_text = f"{trimmed}\n\n{url}"

    try:
        from atproto import Client, models  # imported here so missing package only fails at post time
        client = Client()
        client.login(handle, app_password)

        # Build a rich-text facet so the URL renders as a clickable link.
        # Bluesky facet byte offsets are UTF-8 encoded positions.
        encoded = full_text.encode("utf-8")
        url_bytes = url.encode("utf-8")
        url_start = encoded.find(url_bytes)
        facets = [
            models.AppBskyRichtextFacet.Main(
                features=[models.AppBskyRichtextFacet.Link(uri=url)],
                index=models.AppBskyRichtextFacet.ByteSlice(
                    byte_start=url_start,
                    byte_end=url_start + len(url_bytes),
                ),
            )
        ]

        client.send_post(full_text, facets=facets)
        logger.info("Posted to Bluesky: %s", issue.title[:80])
        return True
    except ImportError:
        logger.error("atproto package not installed — cannot post to Bluesky")
        return False
    except Exception:
        logger.exception("Bluesky post failed for issue %s", issue.id)
        return False


def process_issues_for_bluesky(issues: list, db: Session) -> None:
    """
    Called after each action center refresh. Generates and publishes posts
    for new issues and qualified surges. Mutates bsky_posted_* columns in place.
    """
    if not getattr(settings, "BSKY_HANDLE", "") or not getattr(settings, "BSKY_APP_PASSWORD", ""):
        return  # fast-path: no credentials configured

    now = datetime.utcnow()
    cooldown = timedelta(hours=SURGE_COOLDOWN_HOURS)
    dirty = False

    for issue in issues:
        is_new = issue.bsky_posted_at is None

        if is_new:
            text = _generate_new_post(issue)
            if text and _publish(text, issue):
                issue.bsky_posted_at = now
                issue.bsky_posted_rank = issue.rank
                dirty = True
            continue

        # Surge check: hit #1, previously ranked lower, cooldown elapsed
        last_rank = issue.bsky_posted_rank or issue.rank
        rank_improved = last_rank - issue.rank  # positive = better rank now
        cooled_down = (now - issue.bsky_posted_at) >= cooldown

        if issue.rank == 1 and rank_improved >= SURGE_MIN_RANK_DROP and cooled_down:
            should, text = _generate_surge_post(issue, old_rank=last_rank)
            if should and text and _publish(text, issue):
                issue.bsky_posted_at = now
                issue.bsky_posted_rank = issue.rank
                dirty = True

    if dirty:
        db.commit()
