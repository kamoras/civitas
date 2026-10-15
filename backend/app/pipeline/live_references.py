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
from app.pipeline.analyze.population_reference import reference_coverage_floor
from app.ops_alerts import resolve_ops_alert, send_ops_alert
from app.time_utils import utcnow

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
    chamber: str, members: list[tuple[list[dict], str | None, str]], db: Session,
    roster: int | None = None, quiet: bool = False,
) -> dict | None:
    """This run's Legislative Effectiveness population reference for
    `chamber`, persisted for the API and returned merged with the other
    chamber's last reference for calculate_scores. None (score against the
    last persisted reference) when this run has too few members to measure
    one — the first days of a congress.

    `members` is (sponsored bills, caucus party, where the bills came from:
    bills_source) per member scored; `roster` the chamber's serving members
    (default: len(members)). Only bills this run read are measured: a member
    whose read failed is left out of the stage totals and the member count
    alike (counting them as members with no bills put 2026-10-14's House
    mean credit at 3.16 on a scale defined as 1.0).

    And only when enough of the chamber was read (reference_coverage_floor,
    calibrated): a read that stops part-way stops at a point in the roster,
    so its members are not a random sample — on 2026-10-15, 105 of 433 House
    members read, measured alone, raised 61 members' scores by 5 to 11
    points, 133 of the 149 who rose Republicans. Below the floor the last
    reference measured on full coverage is carried (not rewritten, so the
    breakdown reads the same one); with none on file, the reference is
    measured on every member as scored — the bills read and the stored bills
    carried for the rest — and persisted. Either way an ops alert says so
    (`quiet` for a deliberately partial run: a single-member filter). The
    majority is always derived from the whole roster."""
    from app.pipeline.analyze.population_reference import LES_REFERENCE
    from app.pipeline.analyze.score_calculator import _LES_MAX_STAGE, derive_chamber_majority

    roster = roster or len(members)
    majority = derive_chamber_majority(
        [party for _, party, _ in members], chamber, sitting_president_party(db),
    )
    if majority is None:
        logger.warning(
            "%s majority party undeterminable from the roster — majority/minority "
            "adjustment falls back to the historical table for this congress",
            chamber,
        )
    read = [(bills, party) for bills, party, source in members if source == "read"]
    if len(read) < len(members):
        logger.warning(
            "LES reference (%s): %d of %d members' bills weren't read this run",
            chamber, len(members) - len(read), len(members),
        )
    floor = reference_coverage_floor(LES_REFERENCE.name, chamber)
    if roster and len(read) / roster < floor:
        carried = LES_REFERENCE.last_good(
            chamber, roster, floor,
            usable=lambda entry: len(entry.get("stage_totals") or []) == _LES_MAX_STAGE,
        )
        if carried is not None:
            _coverage_alert(LES_REFERENCE.name, chamber, len(read), roster, floor, carried, quiet)
            return {**LES_REFERENCE.load(), chamber: carried}
        known = [(bills, party) for bills, party, source in members if source != "unavailable"]
        _coverage_alert(LES_REFERENCE.name, chamber, len(read), roster, floor, None, quiet, basis=len(known))
        ref = measure_les_reference(chamber, known, majority)
        if ref is not None:
            ref = {**ref, **_coverage(len(known), roster), "basis": f"stored bills for {len(known) - len(read)} members"}
    else:
        ref = measure_les_reference(chamber, read, majority)
        if ref is not None:
            ref = {**ref, **_coverage(len(read), roster)}
            _coverage_resolved(LES_REFERENCE.name, chamber, quiet)
    if ref is None:
        logger.warning(
            "Too few %s members with substantive bills to measure an LES reference "
            "this run — scoring against the last persisted one", chamber,
        )
        return None
    logger.info("LES reference (%s): %s", chamber, ref)
    return LES_REFERENCE.with_live(chamber, ref)


def bills_source(member: dict) -> str:
    """Where a member's sponsored bills came from this run, as
    live_les_reference reads it: "read", "carried" (an earlier run's,
    sponsorship_backfill.carry_forward_sponsored_bills) or "unavailable"."""
    if member.get("sponsoredBillsUnavailable"):
        return "unavailable"
    return "read" if member.get("sponsoredBillsCarriedFrom") is None else "carried"


def _coverage(measured: int, roster: int) -> dict:
    """The coverage fields a written reference records, which a later run
    reads to tell a full measurement from a partial one (last_good)."""
    return {"coverage": round(measured / roster, 4), "roster": roster}


def _coverage_condition(name: str, chamber: str) -> str:
    return f"reference-coverage-{name}-{chamber}"


def _coverage_alert(
    name: str, chamber: str, measured: int, roster: int, floor: float, carried: dict | None,
    quiet: bool, basis: int | None = None,
) -> None:
    """One ops alert per run and reference that wasn't measured on the run's
    own reads: how many were read, the floor, and what stood in. Never
    raises."""
    if carried is not None:
        when = carried.get("computed_at") or carried.get("measured_on") or "(undated)"
        outcome = f"carried the reference measured {when}"
    elif basis is not None:
        outcome = (
            f"no full-coverage reference on file, so measured on the {basis} members as scored "
            "(the rest on their stored inputs)"
        )
    else:
        outcome = "no full-coverage reference on file, so measured on the members read"
    logger.warning(
        "%s (%s): %d of %d members read, under the %.0f%% floor — %s",
        name, chamber, measured, roster, floor * 100, outcome,
    )
    if quiet:
        return
    try:
        send_ops_alert(
            f"{chamber.capitalize()} {name} not re-measured: {measured} of {roster} members read",
            f"Under the {floor:.0%} coverage floor (app/data/reference_coverage.json), a reference measured "
            f"on the members read would lean toward where the read stopped; {outcome}.",
            dedupe_key=f"{_coverage_condition(name, chamber)}-{utcnow().date().isoformat()}",
            condition=_coverage_condition(name, chamber),
        )
    except Exception:
        logger.exception("Reference-coverage alert failed (non-fatal)")


def _coverage_resolved(name: str, chamber: str, quiet: bool) -> None:
    """Close a reference's open coverage alert once a run measures it whole."""
    if quiet:
        return
    try:
        resolve_ops_alert(_coverage_condition(name, chamber))
    except Exception:
        logger.exception("Reference-coverage alert resolve failed (non-fatal)")


def live_funding_reference(
    chamber: str, fundings: list[dict], states: list[str] | None = None, parties: list[str] | None = None,
    roster: int | None = None, quiet: bool = False,
) -> dict:
    """This run's Funding Independence reference for `chamber` (median PAC
    share of contributions — see score_calculator.compute_funding_reference),
    persisted and merged the same way as live_les_reference. Falls back to
    the last persisted reference when too few members have funding.

    `fundings` are the members this run prepared; `roster` the chamber's
    serving members. A member whose FEC read failed is not among them, so
    an outage leaves a subset — below reference_coverage_floor the last
    full-coverage reference is carried, as for Legislative Effectiveness."""
    from app.pipeline.analyze.population_reference import FUNDING_REFERENCE
    from app.pipeline.analyze.score_calculator import compute_funding_reference

    roster = roster or len(fundings)
    floor = reference_coverage_floor(FUNDING_REFERENCE.name, chamber)
    if roster and len(fundings) / roster < floor:
        carried = FUNDING_REFERENCE.last_good(chamber, roster, floor)
        _coverage_alert(FUNDING_REFERENCE.name, chamber, len(fundings), roster, floor, carried, quiet)
        if carried is not None:
            return {**FUNDING_REFERENCE.load(), chamber: carried}
    else:
        _coverage_resolved(FUNDING_REFERENCE.name, chamber, quiet)

    ref = compute_funding_reference(fundings, states, parties)
    if ref is None:
        logger.warning(
            "Too few %s members with funding to measure a PAC-share reference "
            "this run — scoring against the last persisted one", chamber,
        )
        return FUNDING_REFERENCE.load()
    # A stat this run couldn't measure (e.g. too few measurable donor pools
    # for concentration) keeps its last persisted value.
    ref = {**(FUNDING_REFERENCE.load().get(chamber) or {}), **ref, **_coverage(len(fundings), roster)}
    logger.info("Funding reference (%s): %s", chamber, ref)
    return FUNDING_REFERENCE.with_live(chamber, ref)


def live_constituent_reference_measured(
    chamber: str, members: list[dict], roster: int | None = None, quiet: bool = False,
) -> tuple[dict, bool]:
    """This run's Constituent Alignment expectation for `chamber` (per-party
    break rate by seat lean — see score_calculator.compute_constituent_
    reference), persisted and merged the same way as live_funding_reference,
    and whether it was measured from `members` this run. `members` are
    calculate_scores-shaped dicts. Falls back to the last persisted
    reference (measured False) when either party has too few measurable
    members (e.g. a single-member filtered run) — the ground-truth gate's
    saturation-share probe needs to know which it got.

    `roster` is the chamber's serving members: below reference_coverage_floor
    of them prepared this run, the last full-coverage reference is carried
    (measured False), as for Legislative Effectiveness."""
    from app.pipeline.analyze.population_reference import CONSTITUENT_REFERENCE
    from app.pipeline.analyze.score_calculator import (
        compute_constituent_reference,
        constituent_reference_inputs,
    )

    roster = roster or len(members)
    floor = reference_coverage_floor(CONSTITUENT_REFERENCE.name, chamber)
    if roster and len(members) / roster < floor:
        carried = CONSTITUENT_REFERENCE.last_good(chamber, roster, floor)
        _coverage_alert(CONSTITUENT_REFERENCE.name, chamber, len(members), roster, floor, carried, quiet)
        if carried is not None:
            return {**CONSTITUENT_REFERENCE.load(), chamber: carried}, False
    else:
        _coverage_resolved(CONSTITUENT_REFERENCE.name, chamber, quiet)

    ref = compute_constituent_reference(constituent_reference_inputs(members))
    if ref is None:
        logger.warning(
            "Too few full-confidence %s members (party-labeled votes >= "
            "CONSTITUENT_FULL_CONFIDENCE_VOTES, per party) to measure the "
            "Constituent Alignment expectation this run — expected early in a "
            "Congress; scoring against the last persisted one", chamber,
        )
        return CONSTITUENT_REFERENCE.load(), False
    ref = {**ref, **_coverage(len(members), roster)}
    logger.info("Constituent Alignment reference (%s): %s", chamber, ref)
    return CONSTITUENT_REFERENCE.with_live(chamber, ref), True
