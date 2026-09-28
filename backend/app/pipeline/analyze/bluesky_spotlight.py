"""The daily member spotlight on Bluesky.

Once a day, picks a senator or representative who hasn't been spotlighted
yet (cycling through everyone before repeating) and posts their scores.

The text is a fixed template filled with the stored scores and the
member's rank in their chamber. It used to be written by the local model
under a list of banned words, and the posts it wrote still judged the
numbers ("placing him in the average range", "All individual metrics fall
within typical expectations"). Every fact in the post is a number the site
already shows, so there is nothing for a model to add.
"""

import logging
import random
from datetime import datetime, UTC

from sqlalchemy.orm import Session

from app.config import settings
from app.models import BskySenatorSpotlight, Representative, Senator
from app.pipeline.analyze.bluesky_utils import publish_post
from app.pipeline.analyze.score_calculator import compute_overall_score

logger = logging.getLogger(__name__)

SITE = "https://civitas-research.org"


def _scored_chamber_pool(db: Session, model) -> list:
    return (
        db.query(model)
        .filter(model.score_funding_independence.isnot(None))
        .filter(model.is_current.is_(True))
        .all()
    )


def _pick_politician(
    db: Session,
) -> tuple["Senator | Representative | None", int, int, str]:
    """Return (entity, rank, total, chamber), picked uniformly at random
    from senators AND representatives not yet spotlighted this cycle,
    combined into one pool (cycling through everyone before repeating).

    Deliberately NOT biased toward the highest or lowest scorer: always
    picking an extreme, framed as praise or criticism, produced a real
    incident — a "praise" post about a senator's score read as badly out of
    touch after negative news broke about him the same day. A random pick
    stated as plain numbers (compose_spotlight) can't have the same failure
    mode.

    rank/total are computed WITHIN the picked entity's own chamber, not
    across the combined pool — the site's own leaderboard ranks Senate and
    House in separate tabs, so a combined "#210 of 535" would state a
    number the reader can't find anywhere else on the site.
    """
    # (id, chamber), not id alone — chamber is stored precisely so a
    # senator and a representative that happened to share an id string
    # could never be mistaken for the same "already spotlighted" entity.
    spotlighted_keys = {
        (row.senator_id, row.chamber)
        for row in db.query(BskySenatorSpotlight.senator_id, BskySenatorSpotlight.chamber).all()
    }

    chambers = {
        "senate": sorted(_scored_chamber_pool(db, Senator), key=compute_overall_score, reverse=True),
        "house": sorted(_scored_chamber_pool(db, Representative), key=compute_overall_score, reverse=True),
    }
    if not chambers["senate"] and not chambers["house"]:
        return None, 0, 0, ""

    combined_unspotlighted = [
        (entity, chamber)
        for chamber, ranked in chambers.items()
        for entity in ranked
        if (entity.id, chamber) not in spotlighted_keys
    ]
    if not combined_unspotlighted:
        logger.info("All senators and representatives spotlighted — resetting cycle")
        db.query(BskySenatorSpotlight).delete()
        db.commit()
        combined_unspotlighted = [
            (entity, chamber) for chamber, ranked in chambers.items() for entity in ranked
        ]

    pick, chamber = random.choice(combined_unspotlighted)

    own_chamber = chambers[chamber]
    rank = next(i + 1 for i, e in enumerate(own_chamber) if e.id == pick.id)
    return pick, rank, len(own_chamber), chamber


def compose_spotlight(entity: "Senator | Representative", rank: int, total: int, chamber: str) -> str:
    """The post: who, their Representation Score and rank within their own
    chamber, and each dimension's score."""
    identity = (
        f"{entity.name} ({entity.party}-{entity.state})" if chamber == "senate"
        else f"{entity.name} ({entity.party}-{entity.state}-{entity.district})"
    )
    noun = "senators" if chamber == "senate" else "representatives"
    # The same weighted composite the leaderboard shows.
    overall = compute_overall_score(entity)
    return (
        f"{identity}: Representation Score {overall:.1f}, #{rank} of {total} {noun}. "
        f"Funding Independence {entity.score_funding_independence:.1f}, "
        f"Constituent Alignment {entity.score_constituent_alignment:.1f}, "
        f"Legislative Effectiveness {entity.score_legislative_effectiveness:.1f}."
    )


def _publish_spotlight(text: str, entity: "Senator | Representative") -> bool:
    """Post the spotlight to Bluesky. Returns True on success."""
    url = f"{SITE}/politicians/{entity.id}"
    return publish_post(
        text, url,
        success_msg=f"Posted spotlight: {entity.name}",
        error_context=f"politician {entity.id}",
    )


def post_daily_spotlight(db: Session) -> None:
    """Post a daily senator/representative score spotlight. No-op if
    already posted today."""
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

    entity, rank, total, chamber = _pick_politician(db)
    if not entity:
        logger.warning("No senators or representatives available for spotlight")
        return

    text = compose_spotlight(entity, rank, total, chamber)
    if _publish_spotlight(text, entity):
        db.add(BskySenatorSpotlight(
            senator_id=entity.id,
            chamber=chamber,
            posted_at=datetime.now(UTC),
            post_text=text,
        ))
        db.commit()
        logger.info("Spotlight posted (%s): %s", chamber, entity.name)
