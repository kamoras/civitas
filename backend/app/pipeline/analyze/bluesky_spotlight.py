"""The daily member spotlight: in the Atom feed, and on Bluesky when an
account is configured (app.broadcast.publish).

Once a day, picks a senator or representative who hasn't been spotlighted
yet (cycling through everyone before repeating) and posts their most
distinctive finding. No score or rank: member pages stopped showing both
(redesign 2026-10), so a post stating them would quote a number the reader
can't find on the page it links.

The finding is the scorecard's own sentence (services/score_findings.py,
served on the score breakdown) for the dimension where the member stands
furthest from their chamber: the largest absolute z-score of that
dimension's stored score among the chamber's scored members. Nothing is
hand-picked. A line of five numbers drew 0.07 likes a post, the weakest of
every post type (2026-10-10).

The text is a fixed template around that sentence. It used to be written
by the local model under a list of banned words, and the posts it wrote
still judged the numbers ("placing him in the average range", "All
individual metrics fall within typical expectations"). Every fact in the
post is one the site already shows, so there is nothing for a model to add.
"""

import logging
import random
import statistics
from datetime import datetime, UTC

from sqlalchemy.orm import Session

from app import broadcast
from app.models import BskySenatorSpotlight, Representative, Senator
from app.pipeline.analyze.bluesky_utils import BSKY_MAX_CHARS
from app.services.representative_service import get_representative_score_breakdown
from app.services.senator_service import get_senator_score_breakdown
from app.time_utils import utcnow

logger = logging.getLogger(__name__)


def _scored_chamber_pool(db: Session, model) -> list:
    return (
        db.query(model)
        .filter(model.score_funding_independence.isnot(None))
        .filter(model.is_current.is_(True))
        .all()
    )


def _pick_politician(db: Session) -> tuple["Senator | Representative | None", str]:
    """Return (entity, chamber), picked uniformly at random from senators
    AND representatives not yet spotlighted this cycle, combined into one
    pool (cycling through everyone before repeating).

    Deliberately NOT biased toward the highest or lowest scorer: always
    picking an extreme, framed as praise or criticism, produced a real
    incident — a "praise" post about a senator's score read as badly out of
    touch after negative news broke about him the same day. A random pick
    stated as a plain finding (compose_spotlight) can't have the same
    failure mode.
    """
    # (id, chamber), not id alone — chamber is stored precisely so a
    # senator and a representative that happened to share an id string
    # could never be mistaken for the same "already spotlighted" entity.
    spotlighted_keys = {
        (row.senator_id, row.chamber)
        for row in db.query(BskySenatorSpotlight.senator_id, BskySenatorSpotlight.chamber).all()
    }

    chambers = {
        "senate": sorted(_scored_chamber_pool(db, Senator), key=lambda m: m.id),
        "house": sorted(_scored_chamber_pool(db, Representative), key=lambda m: m.id),
    }
    if not chambers["senate"] and not chambers["house"]:
        return None, ""

    combined_unspotlighted = [
        (entity, chamber)
        for chamber, members in chambers.items()
        for entity in members
        if (entity.id, chamber) not in spotlighted_keys
    ]
    if not combined_unspotlighted:
        logger.info("All senators and representatives spotlighted — resetting cycle")
        db.query(BskySenatorSpotlight).delete()
        db.commit()
        combined_unspotlighted = [
            (entity, chamber) for chamber, members in chambers.items() for entity in members
        ]

    return random.choice(combined_unspotlighted)


def _identity(entity: "Senator | Representative", chamber: str) -> str:
    return (
        f"{entity.name} ({entity.party}-{entity.state})" if chamber == "senate"
        else f"{entity.name} ({entity.party}-{entity.state}-{entity.district})"
    )


# Breakdown key -> the stored score column it explains.
_DIMENSIONS = {
    "fundingIndependence": "score_funding_independence",
    "constituentAlignment": "score_constituent_alignment",
    "legislativeEffectiveness": "score_legislative_effectiveness",
}


def distinctiveness(entity: "Senator | Representative", pool: list) -> list[tuple[str, float]]:
    """(dimension, |z|) for each scored dimension, most distinctive first:
    how many standard deviations the member's stored score sits from the
    mean of the chamber's scored members. A dimension with no spread, or
    no score for the member, is left out. Ties keep _DIMENSIONS' order."""
    out = []
    for key, column in _DIMENSIONS.items():
        mine = getattr(entity, column)
        values = [v for v in (getattr(e, column) for e in pool) if v is not None]
        if mine is None or len(values) < 2:
            continue
        sd = statistics.pstdev(values)
        if sd > 0:
            out.append((key, abs(mine - statistics.fmean(values)) / sd))
    return sorted(out, key=lambda kz: -kz[1])


def distinctive_finding(db: Session, entity: "Senator | Representative", chamber: str) -> str | None:
    """The finding of the member's most distinctive dimension that has
    one (score_findings returns None where the figures can't support a
    sentence), or None."""
    model = Senator if chamber == "senate" else Representative
    get = get_senator_score_breakdown if chamber == "senate" else get_representative_score_breakdown
    breakdown = get(db, entity.id) or {}
    for key, _ in distinctiveness(entity, _scored_chamber_pool(db, model)):
        finding = (breakdown.get(key) or {}).get("finding")
        if finding:
            return finding
    return None


def profile_url(entity: "Senator | Representative") -> str:
    return f"{broadcast.SITE_URL}/politicians/{entity.id}"


def compose_spotlight(entity: "Senator | Representative", chamber: str, finding: str | None = None) -> str:
    """The post: who, then their most distinctive finding, or, with none,
    what the linked page holds. Bluesky posts the text with the profile link
    after it (bluesky_utils.publish_post) in 300 characters; the longest
    finding template fits (tested)."""
    identity = _identity(entity, chamber)
    text = f"{identity}: {finding}"
    if finding and len(text) <= BSKY_MAX_CHARS - 1 - len(profile_url(entity)):
        return text
    return f"{identity}: money, votes and bills beside colleagues in the same party."


def _publish_spotlight(db: Session, text: str, entity: "Senator | Representative", chamber: str) -> None:
    """Publish the spotlight: to the feed, then Bluesky if configured."""
    broadcast.publish(
        db, kind="spotlight", subject=f"member:{chamber}:{entity.id}", title=f"Member spotlight: {_identity(entity, chamber)}", text=text,
        url=profile_url(entity), state=entity.state,
    )


def post_daily_spotlight(db: Session) -> None:
    """Publish a daily senator/representative spotlight. No-op if
    already published today."""
    today = utcnow().date().isoformat()
    already_posted = (
        db.query(BskySenatorSpotlight)
        .filter(BskySenatorSpotlight.posted_at >= today)
        .first()
    )
    if already_posted:
        logger.debug("Spotlight already posted today — skipping")
        return

    entity, chamber = _pick_politician(db)
    if not entity:
        logger.warning("No senators or representatives available for spotlight")
        return

    text = compose_spotlight(entity, chamber, distinctive_finding(db, entity, chamber))
    # Recorded in the same commit as the published post (broadcast.publish
    # commits), so a crash can't publish a second spotlight today.
    db.add(BskySenatorSpotlight(
        senator_id=entity.id,
        chamber=chamber,
        posted_at=datetime.now(UTC),
        post_text=text,
    ))
    _publish_spotlight(db, text, entity, chamber)
    logger.info("Spotlight published (%s): %s", chamber, entity.name)
