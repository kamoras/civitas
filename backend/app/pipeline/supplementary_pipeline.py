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
from datetime import datetime, timedelta

from app.database import SessionLocal
from app.models import Justice, PipelineStatus, SupplementaryPipelineRun
from app.pipeline.run_tracker import PipelineRunTracker

logger = logging.getLogger(__name__)

_tracker = PipelineRunTracker()


def is_supplementary_pipeline_running() -> bool:
    return _tracker.is_running


def supplementary_pipeline_age() -> "timedelta | None":
    """Wall-clock age of the in-process supplementary run, or None when idle."""
    return _tracker.age


async def run_supplementary_pipeline() -> dict:
    """Ingest explore documents, refresh SCOTUS justice scorecards
    (weekly cadence), and update president scorecards."""
    _tracker.start()
    db = SessionLocal()
    start_time = time.time()

    run = SupplementaryPipelineRun(started_at=datetime.utcnow(), status=PipelineStatus.RUNNING)
    db.add(run)
    db.commit()

    try:
        logger.info("=== SUPPLEMENTARY PIPELINE START ===")

        # ── EXPLORE DOCUMENTS ──
        run.current_phase = "explore"
        db.commit()
        logger.info("--- Supplementary: EXPLORE DOCUMENTS ---")
        try:
            from app.pipeline.explore_pipeline import run_explore_pipeline
            explore_result = await run_explore_pipeline(days_back=60)
            total_docs = sum(v for v in explore_result.values() if isinstance(v, int))
            run.explore_docs_ingested = total_docs
            logger.info("Explore pipeline ingested %d documents", total_docs)
        except Exception:
            logger.exception("Explore pipeline failed — continuing")

        # ── SCOTUS JUSTICES ──
        run.current_phase = "justices"
        db.commit()
        logger.info("--- Supplementary: SCOTUS JUSTICES ---")
        # SCOTUS data changes a few times per term, but the Oyez fetch is
        # uncached per-case crawling (5h+ in run 69). Refresh weekly
        # (Sunday UTC), or whenever the justices table is empty.
        justices_missing = db.query(Justice.id).first() is None
        run_justices = justices_missing or datetime.utcnow().weekday() == 6
        if not run_justices:
            logger.info("Justice refresh skipped (weekly cadence; next on Sunday UTC)")
            run.justices_skipped = True
        else:
            try:
                from app.pipeline.justice_pipeline import run_justice_pipeline
                justice_result = await run_justice_pipeline(db)
                run.justices_scored = justice_result.get("justices", 0)
                logger.info("Justice pipeline scored %d justices", run.justices_scored)
            except Exception:
                logger.exception("Justice pipeline failed — continuing")

        # ── PRESIDENTS ──
        run.current_phase = "presidents"
        db.commit()
        logger.info("--- Supplementary: PRESIDENTS ---")
        try:
            from app.pipeline.president_pipeline import run_president_pipeline
            president_result = await run_president_pipeline(db)
            run.presidents_updated = president_result.get("updated", 0)
            logger.info("President pipeline updated %d presidents", run.presidents_updated)
        except Exception:
            logger.exception("President pipeline failed — continuing")

        run.current_phase = "finalize"
        run.status = PipelineStatus.COMPLETED
        run.completed_at = datetime.utcnow()
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
        logger.exception("Supplementary pipeline failed: %s", e)
        try:
            run.status = PipelineStatus.FAILED
            run.completed_at = datetime.utcnow()
            run.elapsed_seconds = round(time.time() - start_time, 1)
            run.error_message = str(e)[:500]
            db.commit()
        except Exception:
            logger.exception("Failed to record supplementary pipeline failure")
        return {"status": PipelineStatus.FAILED, "error": str(e)[:500]}
    finally:
        _tracker.stop()
        db.close()
