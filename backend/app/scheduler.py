import asyncio
import logging
import threading

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.pipeline.orchestrator import run_full_pipeline

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
        except BaseException:
            logger.exception("Nightly pipeline failed")
        finally:
            loop.close()

    threading.Thread(target=_run, daemon=True, name="nightly-pipeline").start()


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
    scheduler.start()
    logger.info("Scheduler started with cron: %s", settings.PIPELINE_CRON_SCHEDULE)


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
