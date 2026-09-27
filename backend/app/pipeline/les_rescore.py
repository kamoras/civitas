"""Bring stored Legislative Effectiveness onto the current reference at startup.

A release that changes Legislative Effectiveness's scale (v6.14's stage
normalization) leaves two things on the old scale until the next nightly
run: the persisted /data/les_reference.json and every member's stored
score. The API's "show the math" breakdown reads the first and the pages
read the second, so for up to a day a profile's number and its breakdown
disagreed, and the breakdown could only say "neutral until the next run".

Everything the component needs is stored — sponsored bills with their
action-code stage, party, tenure, leadership and bipartisan-attraction
scores — so on startup, when a chamber's persisted reference predates the
current scale, this re-measures it from the database and rescores that
chamber's stored Legislative Effectiveness, exactly as the pipeline would.
Once the reference is on the current scale it does nothing.
"""

import logging

from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)


def _needs_rescore(reference: dict | None) -> bool:
    return not (reference or {}).get("stage_totals")


def _bills(entity) -> list[dict]:
    return [
        {
            "billType": b.bill_type,
            "congress": b.congress,
            "isLaw": b.is_law,
            "latestAction": b.latest_action,
            "stage": b.stage,
            "commemorative": b.commemorative,
        }
        for b in entity.sponsored_bills
    ]


def rescore_stale_legislative_effectiveness(session_factory) -> list[str]:
    """Rescore each chamber whose persisted LES reference predates the
    current scale. Returns the chambers rescored. Never raises."""
    from app.models import HousePipelineRun, PipelineRun, PipelineStatus, Representative, Senator
    from app.pipeline.analyze.population_reference import LES_REFERENCE
    from app.pipeline.analyze.score_calculator import _calc_legislative_effectiveness
    from app.pipeline.live_references import live_les_reference

    stale = [c for c in ("senate", "house") if _needs_rescore(LES_REFERENCE.load().get(c))]
    if not stale:
        return []

    done: list[str] = []
    db = session_factory()
    try:
        if any(
            db.query(run).filter(run.status == PipelineStatus.RUNNING).first()
            for run in (PipelineRun, HousePipelineRun)
        ):
            # A run in progress writes a fresh reference and scores itself.
            logger.info("LES rescore skipped — a pipeline run is in progress")
            return []
        for chamber in stale:
            model = Senator if chamber == "senate" else Representative
            rows = (
                db.query(model)
                .options(selectinload(model.sponsored_bills))
                .filter(model.is_current.is_(True))
                .all()
            )
            members = [(_bills(r), r.caucus_party or r.party) for r in rows]
            reference = live_les_reference(chamber, members, db)
            if reference is None:
                logger.warning("LES rescore (%s): too few members with bills to measure a reference", chamber)
                continue
            for row, (bills, party) in zip(rows, members):
                row.score_legislative_effectiveness = _calc_legislative_effectiveness(
                    bills,
                    row.leadership_score,
                    party=party,
                    years_in_office=row.years_in_office,
                    attracted_bipartisanship=row.attracted_bipartisanship_score,
                    les_reference=reference,
                    chamber=chamber,
                )
            db.commit()
            done.append(chamber)
            logger.info("LES rescore (%s): %d members moved to the current reference", chamber, len(rows))
    except Exception:
        db.rollback()
        logger.exception("LES rescore failed (non-fatal) — the next pipeline run rescores")
    finally:
        db.close()
    return done
