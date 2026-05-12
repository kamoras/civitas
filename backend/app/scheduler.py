import asyncio
import logging
import threading

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.pipeline.orchestrator import run_full_pipeline
from app.pipeline.house_pipeline import run_house_pipeline
from app.pipeline.analyze.action_center import refresh_action_issues
from app.pipeline.digest import send_weekly_digests

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def _nightly_pipeline() -> None:
    """Run the unified pipeline (senators, explore docs, SCOTUS justices).

    Runs in a background thread with its own event loop so the main
    uvicorn loop stays responsive during long-running pipeline phases.

    Safe to call from multiple containers: ``run_full_pipeline`` acquires
    a database-level lock and skips if another instance is already running.
    """
    def _run():
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_full_pipeline())
            if result.get("status") == "skipped":
                logger.info("Pipeline skipped — another instance is already running")
            else:
                logger.info("Senate pipeline done — starting House pipeline")
                loop.run_until_complete(run_house_pipeline())
        except BaseException:
            logger.exception("Nightly pipeline failed")
        finally:
            loop.close()

    threading.Thread(target=_run, daemon=True, name="nightly-pipeline").start()


def _hourly_action_refresh() -> None:
    """Refresh the action center in a background thread.

    Skipped when the nightly pipeline is running to avoid competing for
    memory during the most intensive part of the pipeline.
    """
    def _run():
        try:
            from app.database import SessionLocal
            from app.models import PipelineRun
            db = SessionLocal()
            try:
                running = db.query(PipelineRun).filter(PipelineRun.status == "running").first()
            finally:
                db.close()
            if running:
                logger.info(
                    "Action center refresh skipped — nightly pipeline is running (run #%d)",
                    running.id,
                )
                return
            count = refresh_action_issues()
            logger.info("Action center hourly refresh: %d issues", count)
        except Exception:
            logger.exception("Action center refresh failed")

    threading.Thread(target=_run, daemon=True, name="action-refresh").start()


def start_scheduler() -> None:
    """Parse the cron schedule from settings and start the scheduler.

    Multiple containers can safely run the scheduler because the pipeline
    orchestrator uses a database-level lock to prevent concurrent runs.
    """
    cron_parts = settings.PIPELINE_CRON_SCHEDULE.split()
    if len(cron_parts) != 5:
        logger.error("Invalid PIPELINE_CRON_SCHEDULE: %s", settings.PIPELINE_CRON_SCHEDULE)
        return

    minute, hour, day, month, day_of_week = cron_parts

    trigger = CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
    )

    scheduler.add_job(_nightly_pipeline, trigger, id="pipeline_run", replace_existing=True)

    scheduler.add_job(
        _hourly_action_refresh,
        CronTrigger(minute="15"),
        id="action_refresh",
        replace_existing=True,
    )

    # Weekly digest — every Monday at 8 AM UTC
    scheduler.add_job(
        lambda: threading.Thread(
            target=send_weekly_digests, daemon=True, name="weekly-digest"
        ).start(),
        CronTrigger(day_of_week="mon", hour=8, minute=0),
        id="weekly_digest",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started with cron: %s (+ hourly action refresh at :15)", settings.PIPELINE_CRON_SCHEDULE)


def stop_scheduler() -> None:
    """Shutdown the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


def get_next_run_time() -> str | None:
    """Return the next scheduled run time as an ISO string, or None."""
    job = scheduler.get_job("pipeline_run")
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None
