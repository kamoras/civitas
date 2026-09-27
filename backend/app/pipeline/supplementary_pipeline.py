"""Explore-document ingestion + SCOTUS justice scoring + president scoring.

Extracted from senate_pipeline.py (2026-07): these three had no data
dependency on Senate's own fetch/analyze work, they were just sequenced
as extra phases inside run_senate_pipeline() because that was the
pipeline that already existed. Genuinely independent domains — own
top-level function, own tracking row — matching how House and Stock
Trades are already orchestrated, rather than living inside "the Senate
pipeline" where they don't belong.
"""

import logging
import time
from datetime import timedelta

from app.database import SessionLocal
from app.models import Justice, PipelineStatus, SupplementaryPipelineRun
from app.pipeline.progress_tracker import ProgressTracker
from app.pipeline.run_tracker import PipelineRunTracker, STALE_PIPELINE_TIMEOUT, acquire_pipeline_lock
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

SUPPLEMENTARY_PIPELINE_STEPS = [
    ("explore_documents",   "explore",   "Ingest explore documents"),
    ("justice_scorecards",  "justices",  "Score SCOTUS justices"),
    ("committee_leadership", "committee_leadership", "Refresh committee/leadership data"),
    ("district_pvi",        "district_pvi", "Refresh district PVI"),
    ("president_scorecards", "presidents", "Score presidents"),
]

_tracker = PipelineRunTracker()


def is_supplementary_pipeline_running() -> bool:
    return _tracker.is_running


def supplementary_pipeline_age() -> "timedelta | None":
    """Wall-clock age of the in-process supplementary run, or None when idle."""
    return _tracker.age


async def run_supplementary_pipeline() -> dict:
    """Ingest explore documents, refresh SCOTUS justice scorecards
    (weekly cadence), and update president scorecards."""
    db = SessionLocal()

    # Same reasoning as senate_pipeline.py's own lock: until 2026-07-23
    # this was an unconditional insert with no lock at all, so a row
    # orphaned by a killed process (a deploy restarting the container
    # mid-run) stayed "running" forever, blocking every future
    # supplementary run. Confirmed live: this left supplementary data
    # (explore docs, SCOTUS, presidents) stale for 1+ day after a
    # since-fixed deploy-race incident.
    run = acquire_pipeline_lock(db, SupplementaryPipelineRun, STALE_PIPELINE_TIMEOUT)
    if run is None:
        logger.warning("Supplementary pipeline already running in another process — skipping")
        db.close()
        return {"status": "skipped", "reason": "already_running"}

    _tracker.start()
    start_time = time.time()
    progress = ProgressTracker(run, SUPPLEMENTARY_PIPELINE_STEPS, db, start_time)

    try:
        logger.info("=== SUPPLEMENTARY PIPELINE START ===")

        # ── EXPLORE DOCUMENTS ──
        run.current_phase = "explore"
        db.commit()
        logger.info("--- Supplementary: EXPLORE DOCUMENTS ---")
        progress.begin("explore_documents")
        try:
            from app.pipeline.explore_pipeline import run_explore_pipeline
            explore_result = await run_explore_pipeline(days_back=60)
            # Count NEW documents per source — the old sum over all int
            # values picked up total_embedded (historically the whole
            # corpus), reporting "N ingested" when nothing new arrived.
            total_docs = sum(
                (explore_result.get("new_documents") or {}).values()
            )
            run.explore_docs_ingested = total_docs
            logger.info("Explore pipeline ingested %d documents", total_docs)
            progress.complete("explore_documents", detail=f"{total_docs} ingested")
        except Exception:
            # Discard any partial writes this phase staged on the shared
            # session before the next phase's commit persists them (the
            # justice/president sub-pipelines run on this same `db` and only
            # commit at their end, so a mid-phase failure otherwise leaves
            # half-written rows that the phase-boundary commit below flushes).
            db.rollback()
            logger.exception("Explore pipeline failed — continuing")
            progress.fail("explore_documents")

        # ── SCOTUS JUSTICES ──
        run.current_phase = "justices"
        db.commit()
        logger.info("--- Supplementary: SCOTUS JUSTICES ---")
        progress.begin("justice_scorecards")
        # SCOTUS data changes a few times per term, but the Oyez fetch is
        # uncached per-case crawling (5h+ in run 69). Refresh weekly
        # (Sunday UTC), or whenever the justices table is empty.
        justices_missing = db.query(Justice.id).first() is None
        run_justices = justices_missing or utcnow().weekday() == 6
        if not run_justices:
            logger.info("Justice refresh skipped (weekly cadence; next on Sunday UTC)")
            run.justices_skipped = True
            progress.skip("justice_scorecards", detail="weekly cadence")
        else:
            try:
                from app.pipeline.justice_pipeline import run_justice_pipeline
                justice_result = await run_justice_pipeline(db)
                run.justices_scored = justice_result.get("justices", 0)
                logger.info("Justice pipeline scored %d justices", run.justices_scored)
                progress.complete("justice_scorecards", detail=f"{run.justices_scored} scored")
            except Exception:
                db.rollback()  # drop partial justice upserts before the next commit
                logger.exception("Justice pipeline failed — continuing")
                progress.fail("justice_scorecards")

        # ── COMMITTEE MEMBERSHIP / CHAMBER LEADERSHIP ──
        run.current_phase = "committee_leadership"
        db.commit()
        logger.info("--- Supplementary: COMMITTEE/LEADERSHIP ---")
        progress.begin("committee_leadership")
        # Same weekly-or-empty cadence as justices above: leadership titles
        # and committee rosters change slowly, and the source (three raw
        # GitHub-hosted YAML files) needs no more frequent a pull than that.
        # "Missing" here means the persistent volume has never had a
        # successful ingest — the bundled app/data/*.json fallback loads
        # fine in the meantime, but should be refreshed immediately rather
        # than waiting up to a week for the first real data.
        from app.pipeline.transform.committee_data import load_leadership_roles
        leadership_missing = not load_leadership_roles()
        run_committee_leadership = leadership_missing or utcnow().weekday() == 6
        if not run_committee_leadership:
            logger.info("Committee/leadership refresh skipped (weekly cadence; next on Sunday UTC)")
            run.committee_leadership_skipped = True
            progress.skip("committee_leadership", detail="weekly cadence")
        else:
            try:
                from app.pipeline.fetch.committee_leadership import (
                    refresh_committee_leadership_data,
                )
                run.committee_leadership_refreshed = await refresh_committee_leadership_data()
                progress.complete(
                    "committee_leadership",
                    detail="refreshed" if run.committee_leadership_refreshed else "kept previous data",
                )
            except Exception:
                logger.exception("Committee/leadership refresh failed — continuing")
                progress.fail("committee_leadership")

        # ── DISTRICT PVI ──
        run.current_phase = "district_pvi"
        db.commit()
        logger.info("--- Supplementary: DISTRICT PVI ---")
        progress.begin("district_pvi")
        # Same weekly-or-missing cadence as the phases above. Unlike
        # state_pvi.json (see ops_alerts.check_state_pvi_staleness), this
        # scrapes whatever Cook PVI Wikipedia's infoboxes currently show —
        # no election-year window is hardcoded here, so a weekly re-pull
        # naturally tracks Cook's next publication with no code change ever
        # required.
        from app.pipeline.analyze.score_calculator import _district_pvi
        district_pvi_missing = not _district_pvi()
        run_district_pvi = district_pvi_missing or utcnow().weekday() == 6
        if not run_district_pvi:
            logger.info("District PVI refresh skipped (weekly cadence; next on Sunday UTC)")
            run.district_pvi_skipped = True
            progress.skip("district_pvi", detail="weekly cadence")
        else:
            try:
                from app.pipeline.fetch.district_pvi import refresh_district_pvi
                run.district_pvi_refreshed = await refresh_district_pvi()
                progress.complete(
                    "district_pvi",
                    detail="refreshed" if run.district_pvi_refreshed else "kept previous data",
                )
            except Exception:
                logger.exception("District PVI refresh failed — continuing")
                progress.fail("district_pvi")

        # ── PRESIDENTS ──
        run.current_phase = "presidents"
        db.commit()
        logger.info("--- Supplementary: PRESIDENTS ---")
        progress.begin("president_scorecards")
        try:
            from app.pipeline.president_pipeline import run_president_pipeline
            president_result = await run_president_pipeline(db)
            run.presidents_updated = president_result.get("updated", 0)
            logger.info("President pipeline updated %d presidents", run.presidents_updated)
            progress.complete("president_scorecards", detail=f"{run.presidents_updated} updated")
        except Exception:
            db.rollback()  # drop partial president updates before the next commit
            logger.exception("President pipeline failed — continuing")
            progress.fail("president_scorecards")

        run.current_phase = "finalize"
        run.status = PipelineStatus.COMPLETED
        run.completed_at = utcnow()
        run.elapsed_seconds = round(time.time() - start_time, 1)
        db.commit()
        logger.info("=== SUPPLEMENTARY PIPELINE COMPLETE ===")

        return {
            "status": PipelineStatus.COMPLETED,
            "explore_docs_ingested": run.explore_docs_ingested,
            "justices_scored": run.justices_scored,
            "presidents_updated": run.presidents_updated,
            "elapsed_seconds": run.elapsed_seconds,
        }
    except Exception as e:
        # Full detail goes to the server log; the admin-facing summary is a
        # static string with zero reference to the exception object — see
        # database.py's reset_all_data for why.
        logger.exception("Supplementary pipeline failed: %s", e)
        summary = "supplementary pipeline failed — see server logs"
        try:
            db.rollback()  # clear a poisoned session so the FAILED write commits
            run.status = PipelineStatus.FAILED
            run.completed_at = utcnow()
            run.elapsed_seconds = round(time.time() - start_time, 1)
            run.error_message = summary
            db.commit()
        except Exception:
            logger.exception("Failed to record supplementary pipeline failure")
        return {"status": PipelineStatus.FAILED, "error": summary}
    finally:
        _tracker.stop()
        db.close()
