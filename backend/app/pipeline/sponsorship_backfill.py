"""Keep last run's sponsorship scores when this run withholds them.

compute_leadership_scores / compute_ideology_scores / compute_bipartisanship_
scores each withhold (return {}) under whole-cohort data-quality gates —
most recently the SVD-axis-not-partisan check — so every member of the
chamber is missing from the dicts at once. Scoring reads them with a bare
`.get(bio_id)`, so a withheld run scored every member's Legislative
Effectiveness without their leadership (neutral 50) or bipartisan-attraction
component, for that run's scores and snapshots.

The Senate pipeline backfilled from the stored values; the House pipeline
did not. Shared here so both chambers keep what they had.
"""

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def backfill_withheld_sponsorship_scores(
    db: Session,
    model,
    bio_ids: set[str],
    leadership_scores: dict, ideology_scores: dict,
    bipartisanship_scores: dict, attracted_bipartisanship_scores: dict,
) -> None:
    """Fill any bio_id missing from this run's freshly-computed sponsorship
    score dicts with that member's last-stored value, in place. `model` is
    Senator or Representative (both carry the same four columns)."""
    missing = {
        bio_id for bio_id in bio_ids
        if bio_id not in leadership_scores or bio_id not in ideology_scores
        or bio_id not in bipartisanship_scores or bio_id not in attracted_bipartisanship_scores
    }
    if not missing:
        return

    prior = {
        row.bioguide_id: row
        for row in db.query(
            model.bioguide_id, model.leadership_score, model.ideology_score,
            model.bipartisanship_score, model.attracted_bipartisanship_score,
        ).filter(model.bioguide_id.in_(missing)).all()
    }

    backfilled = 0
    for bio_id in missing:
        row = prior.get(bio_id)
        if row is None:
            continue
        for computed, value in (
            (leadership_scores, row.leadership_score),
            (ideology_scores, row.ideology_score),
            (bipartisanship_scores, row.bipartisanship_score),
            (attracted_bipartisanship_scores, row.attracted_bipartisanship_score),
        ):
            if bio_id not in computed and value is not None:
                computed[bio_id] = value
                backfilled += 1

    if backfilled:
        logger.warning(
            "Sponsorship analysis (%s): %d score(s) withheld this run, backfilled from last stored value",
            model.__tablename__, backfilled,
        )
