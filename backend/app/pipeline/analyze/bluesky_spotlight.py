"""Daily Bluesky senator score spotlight.

Once per day, picks a senator who hasn't been spotlighted yet (cycling
through all senators before repeating) and posts a 1-2 sentence highlight
about their score and the most interesting driver behind it.
"""

import logging
import re
from datetime import datetime, UTC

from sqlalchemy.orm import Session

from app.config import settings
from app.models import BskySenatorSpotlight, Senator
from app.pipeline.analyze.ollama_client import call_llm

logger = logging.getLogger(__name__)

MAX_SPOTLIGHT_CHARS = 240
SITE = "https://civitas.paramain.com"

_SYSTEM_PROMPT = (
    "You are a nonpartisan civic journalist writing brief, factual posts for "
    "the Civitas transparency platform. Civitas scores U.S. senators on "
    "funding independence, promise persistence, independent voting, funding "
    "diversity, and legislative effectiveness. Your posts are data-driven, "
    "neutral, and written to help citizens understand how their representatives "
    "are performing."
)


def _pick_senator(db: Session) -> Senator | None:
    """Return the next senator to spotlight, cycling through all before repeating."""
    spotlighted_ids = {
        row.senator_id
        for row in db.query(BskySenatorSpotlight.senator_id).all()
    }

    senators = (
        db.query(Senator)
        .filter(Senator.score_funding_independence.isnot(None))
        .all()
    )
    if not senators:
        return None

    # Prefer senators not yet spotlighted
    unspotlighted = [s for s in senators if s.id not in spotlighted_ids]
    if not unspotlighted:
        # Full cycle complete — reset and start again
        logger.info("All %d senators spotlighted — resetting cycle", len(senators))
        db.query(BskySenatorSpotlight).delete()
        db.commit()
        unspotlighted = senators

    # Pick the one with the most extreme overall score (most newsworthy)
    def overall(s: Senator) -> float:
        scores = [
            s.score_funding_independence or 0,
            s.score_promise_persistence or 0,
            s.score_independent_voting or 0,
            s.score_funding_diversity or 0,
            (s.score_legislative_effectiveness or 0),
        ]
        return sum(scores) / len(scores)

    # Alternate between highest and lowest to vary the tone
    cycle_pos = len(spotlighted_ids) % 2
    ranked = sorted(unspotlighted, key=overall, reverse=(cycle_pos == 0))
    return ranked[0]


def _generate_spotlight_post(senator: Senator) -> str | None:
    """Ask the LLM to write a score highlight post for this senator."""
    scores = {
        "Funding independence": round(senator.score_funding_independence or 0, 1),
        "Promise persistence": round(senator.score_promise_persistence or 0, 1),
        "Independent voting": round(senator.score_independent_voting or 0, 1),
        "Funding diversity": round(senator.score_funding_diversity or 0, 1),
        "Legislative effectiveness": round(senator.score_legislative_effectiveness or 0, 1),
    }
    overall = round(sum(scores.values()) / len(scores), 1)
    score_lines = "\n".join(f"- {k}: {v}/100" for k, v in scores.items())

    user_prompt = f"""Write a Bluesky post spotlighting this senator's Civitas transparency score.

Senator: {senator.name} ({senator.party}-{senator.state})
Overall score: {overall}/100
Individual scores:
{score_lines}

RULES:
1. Mention the senator's name, state, and one or two of the most notable scores.
2. Focus on the most striking data point — the highest or lowest individual score.
3. Be factual and neutral. Do not say "good" or "bad" — just report the numbers.
4. STRICT MAXIMUM: {MAX_SPOTLIGHT_CHARS} characters total.
5. Write 1-2 complete sentences ending with proper punctuation.
6. No hashtags, no exclamation points, no editorializing.
7. Do not add any information not provided above.

Return JSON: {{"post": "<your post text>"}}"""

    result = call_llm(
        prompt_version="bsky_spotlight_v1",
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        cache_key=None,
        db_session=None,
        max_tokens=200,
        num_ctx=1024,
    )
    if not result or not isinstance(result.get("post"), str):
        return None

    text = re.sub(r"#(\w+)", r"\1", result["post"]).strip()
    if len(text) > MAX_SPOTLIGHT_CHARS:
        trimmed = text[:MAX_SPOTLIGHT_CHARS]
        cut = max(
            (trimmed.rfind(p) + 1 for p in (".", "!", "?") if trimmed.rfind(p) > 0),
            default=-1,
        )
        text = trimmed[:cut] if cut > 0 else trimmed[: trimmed.rfind(" ")]
    return text.strip()


def _publish_spotlight(text: str, senator: Senator) -> bool:
    """Post the spotlight to Bluesky. Returns True on success."""
    handle = getattr(settings, "BSKY_HANDLE", "")
    app_password = getattr(settings, "BSKY_APP_PASSWORD", "")
    if not handle or not app_password:
        return False

    url = f"{SITE}/senators/{senator.id}"
    full_text = f"{text}\n\n{url}"

    if len(full_text) > 300:
        budget = 300 - len(url) - 2
        trimmed = text[:budget]
        cut = max(
            (trimmed.rfind(p) + 1 for p in (".", "!", "?") if trimmed.rfind(p) > 0),
            default=-1,
        )
        trimmed = trimmed[:cut] if cut > 0 else trimmed[: trimmed.rfind(" ")]
        full_text = f"{trimmed}\n\n{url}"

    try:
        from atproto import Client, models

        client = Client()
        client.login(handle, app_password)

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
        logger.info("Posted senator spotlight: %s", senator.name)
        return True
    except ImportError:
        logger.error("atproto not installed — cannot post spotlight")
        return False
    except Exception:
        logger.exception("Spotlight post failed for senator %s", senator.id)
        return False


def post_daily_spotlight(db: Session) -> None:
    """Post a daily senator score spotlight. No-op if already posted today."""
    if not getattr(settings, "BSKY_HANDLE", "") or not getattr(settings, "BSKY_APP_PASSWORD", ""):
        return

    today = datetime.now(UTC).date().isoformat()
    already_posted = (
        db.query(BskySenatorSpotlight)
        .filter(BskySenatorSpotlight.posted_at >= today)
        .first()
    )
    if already_posted:
        logger.debug("Spotlight already posted today — skipping")
        return

    senator = _pick_senator(db)
    if not senator:
        logger.warning("No senators available for spotlight")
        return

    text = _generate_spotlight_post(senator)
    if not text:
        logger.warning("Failed to generate spotlight text for %s", senator.name)
        return

    if _publish_spotlight(text, senator):
        db.add(BskySenatorSpotlight(
            senator_id=senator.id,
            posted_at=datetime.now(UTC),
            post_text=text,
        ))
        db.commit()
        logger.info("Senator spotlight posted: %s", senator.name)
