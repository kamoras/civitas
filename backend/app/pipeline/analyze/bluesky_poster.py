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
from app.pipeline.analyze.bluesky_utils import build_link_card
from app.pipeline.analyze.grounding import (
    grounding_violations,
    hedge_and_editorializing_violations,
)
from app.pipeline.analyze.ollama_client import call_llm

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

        logger.warning(
            "Bluesky post failed grounding for issue %s (attempt %d): %s | post: %s",
            issue.id, attempt + 1, "; ".join(reasons), post[:160],
        )
        retry_note = (
            "\n\nYour previous attempt was rejected because it included "
            f"{'; '.join(reasons)}. Rewrite using only the Title, Summary, and "
            "Key facts, report events directly instead of through phrases "
            "like 'sources show' or 'reports indicate,' and do not evaluate "
            "whether any action was warranted or justified."
        )

    return None  # ungrounded twice — skip; the next refresh cycle retries


def _publish(text: str, issue) -> bool:
    """Post to Bluesky. Returns True on success."""
    handle = getattr(settings, "BSKY_HANDLE", "")
    app_password = getattr(settings, "BSKY_APP_PASSWORD", "")
    if not handle or not app_password:
        logger.debug("Bluesky credentials not set — skipping publish")
        return False

    text = re.sub(r"#(\w+)", r"\1", text).strip()  # final hashtag guard
    url = f"https://civitas-research.org/issue/{issue.id}"
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

        embed = build_link_card(client, url)
        client.send_post(full_text, facets=facets, embed=embed)
        logger.info("Posted to Bluesky: %s", issue.title[:80])
        return True
    except ImportError:
        logger.error("atproto package not installed — cannot post to Bluesky")
        return False
    except Exception:
        logger.exception("Bluesky post failed for issue %s", issue.id)
        return False


def process_issues_for_bluesky(issues: list, db: Session) -> int:
    """Post new/updated issues to Bluesky.

    The pipeline already decides which issues deserve a post by setting
    bsky_posted_at=None (new topic or topic with genuinely new articles).
    This function just executes those posts.
    """
    if not getattr(settings, "BSKY_HANDLE", "") or not getattr(settings, "BSKY_APP_PASSWORD", ""):
        return 0  # fast-path: no credentials configured

    from zoneinfo import ZoneInfo
    _US_EAST = ZoneInfo("America/New_York")
    today = datetime.now(tz=_US_EAST).strftime("%Y-%m-%d")

    now = datetime.utcnow()
    posted = 0

    for issue in issues:
        if issue.bsky_posted_at is not None:
            continue  # pipeline didn't flag this issue for posting

        text = _generate_new_post(issue, today)
        if text and _publish(text, issue):
            issue.bsky_posted_at = now
            issue.bsky_posted_rank = issue.rank
            posted += 1

    if posted:
        db.commit()
    return posted
