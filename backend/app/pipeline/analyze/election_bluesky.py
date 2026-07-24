"""Posts race-coverage updates to Bluesky (2026-07, midterm-elections feature).

Same shape as bluesky_poster.py's ActionIssue posting: one grounded,
LLM-generated sentence per notable coverage item, verified mechanically
via grounding.py before it ships. Never a bare link — the user's own
requirement is that a post must say something, briefly and accurately,
not just paste a URL.

Verified directly (not assumed): grounding_violations' electoral-claims
check is already permissive when the SOURCE material itself contains
genuine electoral vocabulary (see tests/test_grounding.py's
test_electoral_framing_grounded_when_source_covers_election) — it only
rejects electoral framing invented from non-electoral source text. Since
every post here is generated from a real article/post about that exact
race, grounding_violations is reused completely as-is; no bypass needed.

Only a small, capped batch of coverage items get posted per run (most
matched coverage never becomes a Bluesky post at all) — this is a
volume/quality control, not a hallucination guard; the grounding checks
below are what enforce accuracy.
"""

import logging
import re

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Race, RaceCoverageItem
from app.pipeline.analyze.bluesky_utils import publish_post
from app.pipeline.analyze.grounding import (
    grounding_violations,
    hedge_and_editorializing_violations,
)
from app.pipeline.analyze.ollama_client import call_llm
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

MAX_POST_CHARS = 240

# Hard cap on how many coverage items get a Bluesky post per pipeline run —
# most matched coverage (potentially dozens of articles/posts per race
# across hundreds of races) never becomes a post at all. Matches the same
# conservative per-run cap bluesky_engagement.py already uses for a
# different reason (repost/like volume) — here it keeps the feed from being
# flooded the moment a busy news day matches many races at once.
MAX_POSTS_PER_RUN = 5

_SYSTEM_PROMPT = (
    "You are a civic journalist writing brief, factual updates for the Civitas "
    "transparency platform. Civitas aggregates U.S. government and election data "
    "into public scorecards. Your posts are non-partisan, data-grounded, and "
    "written for citizens who want to understand what's happening in their races."
)


def _office_label(race: Race) -> str:
    if race.office == "S":
        return f"the {race.state} Senate race"
    district = race.district if race.district else "at-large"
    return f"the {race.state}-{district} House race"


def _generate_post_text(item: RaceCoverageItem, race: Race) -> str | None:
    """Ask the LLM for ONE grounded sentence describing this coverage item.

    Same retry/grounding shape as bluesky_poster._generate_new_post, sized
    down to a single source item instead of an aggregated ActionIssue.
    """
    user_prompt = f"""Write ONE brief Bluesky sentence introducing this piece of \
election coverage.

Race: {_office_label(race)}
Source: {item.source_name}
Title: {item.title}
Content: {item.summary or '(no summary available)'}

RULES — violating any rule means your response is unusable:
1. Use ONLY information from the Race, Title, and Content above. Do not add \
details, numbers, or claims not stated there.
2. STRICT MAXIMUM: {MAX_POST_CHARS} characters total.
3. Exactly ONE sentence, ending with proper punctuation.
4. No hashtags, no exclamation points, no editorializing, no "breaking news".
5. Neutral and non-partisan.
6. Report directly — never write "sources say," "reports indicate," or similar.
7. Do not predict or assert a winner, margin, or outcome unless the Content \
explicitly states one.

Return JSON: {{"post": "<your sentence>"}}"""

    source_material = f"{_office_label(race)}\n{item.title}\n{item.summary or ''}"

    retry_note = ""
    for attempt in range(2):
        result = call_llm(
            prompt_version="election_coverage_post_v1",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt + retry_note,
            model=settings.OLLAMA_STORY_MODEL or None,
            cache_key=None,  # time-sensitive, never cache
            db_session=None,
            max_tokens=200,
            num_ctx=2048,
        )
        if not result or not isinstance(result.get("post"), str):
            return None
        post = re.sub(r"#(\w+)", r"\1", result["post"]).strip()[:MAX_POST_CHARS]

        reasons = grounding_violations(post, source_material) + hedge_and_editorializing_violations(post)
        if not reasons:
            return post

        logger.warning(
            "Election coverage post failed grounding for item %s (attempt %d): %s | post: %s",
            item.id, attempt + 1, "; ".join(reasons), post[:160],
        )
        retry_note = (
            "\n\nYour previous attempt was rejected because it included "
            f"{'; '.join(reasons)}. Rewrite using only the Race, Title, and "
            "Content, report directly instead of hedging attribution, and do "
            "not assert any electoral outcome the Content doesn't state."
        )

    return None  # ungrounded twice — skip; a later run can retry


def _publish(text: str, race: Race) -> bool:
    text = re.sub(r"#(\w+)", r"\1", text).strip()
    url = f"https://civitas-research.org/elections/{race.id}"
    return publish_post(
        text, url,
        success_msg=f"Posted election coverage update: {race.id}",
        error_context=f"race {race.id}",
    )


def post_race_coverage_updates(db: Session) -> int:
    """Post a capped, prioritized batch of not-yet-considered coverage
    items to Bluesky. No-op if Bluesky credentials aren't configured.
    Every considered item (posted or not) is marked bsky_posted_at so the
    next run doesn't re-evaluate it.
    """
    if not getattr(settings, "BSKY_HANDLE", "") or not getattr(settings, "BSKY_APP_PASSWORD", ""):
        return 0

    candidates = (
        db.query(RaceCoverageItem)
        .filter(RaceCoverageItem.bsky_posted_at.is_(None))
        .order_by(RaceCoverageItem.fetched_at.desc())
        .limit(MAX_POSTS_PER_RUN)
        .all()
    )
    if not candidates:
        return 0

    races_by_id = {
        r.id: r for r in db.query(Race).filter(
            Race.id.in_({item.race_id for item in candidates}),
        ).all()
    }

    posted = 0
    now = utcnow()
    for item in candidates:
        race = races_by_id.get(item.race_id)
        if race is None:
            item.bsky_posted_at = now
            continue

        text = _generate_post_text(item, race)
        item.bsky_posted_at = now  # considered either way
        if not text:
            continue

        if _publish(text, race):
            posted += 1

    db.commit()
    return posted
