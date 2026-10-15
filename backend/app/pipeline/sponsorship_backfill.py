"""Keep last run's sponsorship scores and sponsored bills when this run
can't produce them.

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

import json
import logging

from sqlalchemy.orm import Session, selectinload

from app.pipeline.analyze.score_calculator import carried_date, stored_bills_status

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


def stored_sponsored_bill(row) -> dict:
    """A stored SponsoredBill / RepSponsoredBill row in the shape the
    pipeline scores and saves (the fields its writer reads back)."""
    try:
        areas = json.loads(row.policy_areas or "[]")
    except ValueError:
        areas = []
    return {
        "billId": row.bill_id,
        "title": row.title,
        "introducedDate": row.introduced_date,
        "latestAction": row.latest_action,
        "latestActionDate": row.latest_action_date,
        "policyArea": row.policy_area,
        "policyAreas": areas,
        "partyLeaning": row.party_leaning,
        "congress": row.congress,
        "billType": row.bill_type,
        "isLaw": row.is_law,
        "stage": row.stage,
        "commemorative": row.commemorative,
    }


def carry_forward_sponsored_bills(db: Session, model, members: list[dict]) -> list[str]:
    """For each member dict whose sponsored legislation this run couldn't
    read (`sponsoredBillsUnavailable`, or no `sponsoredBills` at all because
    the step that reads them stopped before reaching the member), score the
    bills an earlier run stored: an outage is not a record of no bills, and
    a neutral score reads it as an average one. Sets `sponsoredBills` to
    them and `sponsoredBillsCarriedFrom` to the date those bills were read
    (from the stored score's LES_BILLS_STATUS; "" when no run recorded one),
    and clears `sponsoredBillsUnavailable`, so the save writes the same
    bills back. A member the database has never stored stays unavailable
    (Legislative Effectiveness then leaves the bill part unmeasured).
    Members are matched by `bioguideId`. Returns the bioguide ids carried,
    in place. `model` is Senator or Representative."""
    pending = {
        m["bioguideId"]: m for m in members
        if m.get("bioguideId") and (m.get("sponsoredBillsUnavailable") or "sponsoredBills" not in m)
    }
    if not pending:
        return []
    rows = (
        db.query(model)
        .options(selectinload(model.sponsored_bills))
        .filter(model.bioguide_id.in_(pending))
        .all()
    )
    carried = []
    for row in rows:
        member = pending.pop(row.bioguide_id)
        status = stored_bills_status(row)
        member["sponsoredBills"] = [stored_sponsored_bill(b) for b in row.sponsored_bills]
        member["sponsoredBillsCarriedFrom"] = carried_date(status)
        member["sponsoredBillsUnavailable"] = False
        carried.append(row.bioguide_id)
    for member in pending.values():
        member.setdefault("sponsoredBills", [])
        member["sponsoredBillsUnavailable"] = True
    if carried or pending:
        logger.warning(
            "Sponsored legislation (%s): %d member(s) unreadable this run scored on their stored bills, "
            "%d with none stored left unmeasured",
            model.__tablename__, len(carried), len(pending),
        )
    return carried
