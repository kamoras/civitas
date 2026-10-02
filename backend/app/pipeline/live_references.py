"""This run's measured population references, shared by the Senate and
House pipelines.

Each chamber pipeline measures its references (Legislative Effectiveness,
Funding Independence, Constituent Alignment) from the members it is about
to score, persists them for the API's breakdowns, and scores against the
result (AGENTS.md §3a). These used to live in senate_pipeline as private
helpers the House pipeline imported; they belong to neither chamber.
"""

import logging

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)


def sitting_president_party(db: Session) -> str | None:
    """Party of the sitting president — the Vice President's party, which
    breaks a Senate tie (see score_calculator.derive_chamber_majority)."""
    from app.models import President

    row = db.query(President.party).filter(President.is_current == True).first()  # noqa: E712
    return row[0] if row else None


def measure_les_reference(
    chamber: str, members: list[tuple[list[dict], str | None]], majority: str | None,
) -> dict | None:
    """`chamber`'s Legislative Effectiveness reference measured from
    `members`, not persisted (None when too few members to measure).

    Labelled with the Congress of the bills measured — the newest among
    them (every member's bills are windowed to one Congress) — not the
    Congress the job holds: the startup rescore (les_rescore) measures the
    STORED bills, which after a restart between noon ET on Jan 3 and the
    next House/Senate run are still the outgoing Congress's while the job
    already holds the new one. Only with no bills at all does it fall back
    to the held Congress."""
    from app.pipeline.analyze.population_reference import LES_REFERENCE
    from app.pipeline.analyze.score_calculator import compute_les_reference

    previous = LES_REFERENCE.load().get(chamber) or {}
    measured = max((b.get("congress") or 0 for bills, _ in members for b in bills), default=0)
    return compute_les_reference(
        members, measured or settings.CURRENT_CONGRESS, majority, previous.get("advancement_rates"),
    )


def live_les_reference(
    chamber: str, members: list[tuple[list[dict], str | None]], db: Session,
) -> dict | None:
    """This run's Legislative Effectiveness population reference for
    `chamber`, persisted for the API and returned merged with the other
    chamber's last reference for calculate_scores. None (score against the
    last persisted reference) when this run has too few members to measure
    one — a single-member filter run, or the first days of a congress.
    """
    from app.pipeline.analyze.population_reference import LES_REFERENCE
    from app.pipeline.analyze.score_calculator import derive_chamber_majority

    majority = derive_chamber_majority(
        [party for _, party in members], chamber, sitting_president_party(db),
    )
    if majority is None:
        logger.warning(
            "%s majority party undeterminable from the roster — majority/minority "
            "adjustment falls back to the historical table for this congress",
            chamber,
        )
    ref = measure_les_reference(chamber, members, majority)
    if ref is None:
        logger.warning(
            "Too few %s members with substantive bills to measure an LES reference "
            "this run — scoring against the last persisted one", chamber,
        )
        return None
    logger.info("LES reference (%s): %s", chamber, ref)
    return LES_REFERENCE.with_live(chamber, ref)


def live_funding_reference(
    chamber: str, fundings: list[dict], states: list[str] | None = None, parties: list[str] | None = None,
) -> dict:
    """This run's Funding Independence reference for `chamber` (median PAC
    share of contributions — see score_calculator.compute_funding_reference),
    persisted and merged the same way as live_les_reference. Falls back to
    the last persisted reference when too few members have funding."""
    from app.pipeline.analyze.population_reference import FUNDING_REFERENCE
    from app.pipeline.analyze.score_calculator import compute_funding_reference

    ref = compute_funding_reference(fundings, states, parties)
    if ref is None:
        logger.warning(
            "Too few %s members with funding to measure a PAC-share reference "
            "this run — scoring against the last persisted one", chamber,
        )
        return FUNDING_REFERENCE.load()
    # A stat this run couldn't measure (e.g. too few measurable donor pools
    # for concentration) keeps its last persisted value.
    ref = {**(FUNDING_REFERENCE.load().get(chamber) or {}), **ref}
    logger.info("Funding reference (%s): %s", chamber, ref)
    return FUNDING_REFERENCE.with_live(chamber, ref)


def live_constituent_reference_measured(chamber: str, members: list[dict]) -> tuple[dict, bool]:
    """This run's Constituent Alignment expectation for `chamber` (per-party
    break rate by seat lean — see score_calculator.compute_constituent_
    reference), persisted and merged the same way as live_funding_reference,
    and whether it was measured from `members` this run. `members` are
    calculate_scores-shaped dicts. Falls back to the last persisted
    reference (measured False) when either party has too few measurable
    members (e.g. a single-member filtered run) — the ground-truth gate's
    saturation-share probe needs to know which it got."""
    from app.pipeline.analyze.population_reference import CONSTITUENT_REFERENCE
    from app.pipeline.analyze.score_calculator import (
        compute_constituent_reference,
        constituent_reference_inputs,
    )

    ref = compute_constituent_reference(constituent_reference_inputs(members))
    if ref is None:
        logger.warning(
            "Too few full-confidence %s members (party-labeled votes >= "
            "CONSTITUENT_FULL_CONFIDENCE_VOTES, per party) to measure the "
            "Constituent Alignment expectation this run — expected early in a "
            "Congress; scoring against the last persisted one", chamber,
        )
        return CONSTITUENT_REFERENCE.load(), False
    logger.info("Constituent Alignment reference (%s): %s", chamber, ref)
    return CONSTITUENT_REFERENCE.with_live(chamber, ref), True
