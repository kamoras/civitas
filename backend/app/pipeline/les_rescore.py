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

from app.pipeline.analyze.score_calculator import _LES_MAX_STAGE

logger = logging.getLogger(__name__)


def _needs_rescore(reference: dict | None) -> bool:
    """Missing stage totals (before v6.14), or a different number of stages
    than the scorer now uses (v6.17 added V&W's fifth)."""
    totals = (reference or {}).get("stage_totals")
    return not totals or len(totals) != _LES_MAX_STAGE


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
    from app.models import HousePipelineRun, PipelineRun, Representative, Senator
    from app.pipeline.analyze.population_reference import LES_REFERENCE
    from app.pipeline.analyze.score_calculator import _calc_legislative_effectiveness, derive_chamber_majority
    from app.pipeline.live_references import measure_les_reference, sitting_president_party
    from app.pipeline.run_tracker import run_in_progress

    stale = [c for c in ("senate", "house") if _needs_rescore(LES_REFERENCE.load().get(c))]
    done: list[str] = []
    for chamber in stale:
        model, run = (Senator, PipelineRun) if chamber == "senate" else (Representative, HousePipelineRun)
        db = session_factory()
        try:
            if run_in_progress(db, run):
                # A run in progress writes a fresh reference and scores itself.
                logger.info("LES rescore (%s) skipped — a pipeline run is in progress", chamber)
                continue
            rows = (
                db.query(model)
                .options(selectinload(model.sponsored_bills))
                .filter(model.is_current.is_(True))
                .all()
            )
            members = [(_bills(r), r.caucus_party or r.party) for r in rows]
            majority = derive_chamber_majority([p for _, p in members], chamber, sitting_president_party(db))
            ref = measure_les_reference(chamber, members, majority)
            if ref is None:
                logger.warning("LES rescore (%s): too few members with bills to measure a reference", chamber)
                continue
            reference = {**LES_REFERENCE.load(), chamber: ref}
            for row, (bills, party) in zip(rows, members):
                row.score_legislative_effectiveness = _calc_legislative_effectiveness(
                    bills,
                    row.leadership_score,
                    party=party,
                    years_in_office=row.years_in_office,
                    attracted_bipartisanship=row.attracted_bipartisanship_score,
                    les_reference=reference,
                    chamber=chamber,
                    sworn_date=getattr(row, "sworn_date", None),
                )
            db.commit()
            # Persisted only once the scores it describes are committed: a
            # reference on the current scale is what marks the chamber done,
            # so writing it first would strand the scores on a failure.
            LES_REFERENCE.write(chamber, ref)
            done.append(chamber)
            logger.info("LES rescore (%s): %d members moved to the current reference", chamber, len(rows))
        except Exception:
            db.rollback()
            logger.exception("LES rescore (%s) failed (non-fatal) — retried next startup", chamber)
        finally:
            db.close()
    return done
