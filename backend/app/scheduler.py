import asyncio
import logging
import threading
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.pipeline.senate_pipeline import run_senate_pipeline
from app.pipeline.house_pipeline import run_house_pipeline, is_house_pipeline_running, house_pipeline_age
from app.pipeline.stock_pipeline import run_stock_trades_pipeline
from app.pipeline.analyze.action_center import get_action_refresh_state, refresh_action_issues

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def _nightly_pipeline() -> None:
    """Run the unified pipeline (senators, explore docs, SCOTUS justices).

    Runs in a background thread with its own event loop so the main
    uvicorn loop stays responsive during long-running pipeline phases.

    Safe to call from multiple containers: ``run_senate_pipeline`` acquires
    a database-level lock and skips if another instance is already running.
    """
    def _run():
        from app.ops_alerts import send_ops_alert
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_senate_pipeline())
            if result.get("status") == "skipped":
                logger.info("Pipeline skipped — another instance is already running")
                send_ops_alert(
                    "Nightly Senate run skipped",
                    "The scheduled Senate pipeline did not start because a "
                    "previous run is still active. Senate data will be a day "
                    "stale unless triggered manually.",
                    dedupe_key=f"skipped-{datetime.utcnow():%Y-%m-%d}",
                )
            else:
                logger.info("Senate pipeline done — starting House pipeline")
                loop.run_until_complete(run_house_pipeline())
                logger.info("House pipeline done — starting stock trades pipeline")
                stock_result = loop.run_until_complete(run_stock_trades_pipeline())
                logger.info("Stock trades pipeline: %s", stock_result)
        except BaseException as e:
            logger.exception("Nightly pipeline failed")
            send_ops_alert(
                "Nightly pipeline crashed",
                f"{type(e).__name__}: {e}",
                dedupe_key=f"crashed-{datetime.utcnow():%Y-%m-%d}",
            )
        finally:
            loop.close()

    threading.Thread(target=_run, daemon=True, name="nightly-pipeline").start()


def _hourly_action_refresh() -> None:
    """Refresh the action center in a background thread.

    Skipped when the nightly pipeline is running to avoid competing for
    memory during the most intensive part of the pipeline, or when the
    previous hourly refresh is still running — a slow/degraded local LLM
    can make one cycle run long enough to still be active when the next
    cron tick fires (confirmed live 2026-07-13), and without this guard
    that spawns a second thread competing for the same LLM, compounding
    the slowdown rather than just running a bit late.
    """
    def _run():
        try:
            state = get_action_refresh_state()
            if state.get("is_running"):
                started = state.get("started_at")
                age = datetime.utcnow() - started if started else None
                if age is not None and age > timedelta(hours=4):
                    # Same reasoning as the stale-PipelineRun checks below: a
                    # refresh this old (normal is minutes, worst case with a
                    # degraded LLM is ~1-2h) is wedged, not just slow. This
                    # in-memory flag only clears on completion or container
                    # restart, so without this override a genuinely hung
                    # thread would block every future hourly run indefinitely.
                    logger.warning(
                        "Stale action-center refresh detected (age %s) — "
                        "treating as hung and proceeding with a new refresh",
                        age,
                    )
                else:
                    logger.info(
                        "Action center refresh skipped — previous refresh still running (age %s)",
                        age,
                    )
                    return
            from app.database import SessionLocal
            from app.models import PipelineRun
            db = SessionLocal()
            try:
                running = db.query(PipelineRun).filter(PipelineRun.status == "running").first()
            finally:
                db.close()
            if running:
                age = datetime.utcnow() - running.started_at
                if age > timedelta(hours=8):
                    # Pipeline run has been "running" for >8h — it almost certainly
                    # crashed without updating its status. Proceed rather than blocking
                    # the action center indefinitely.
                    logger.warning(
                        "Stale PipelineRun detected (run #%d started %s, age %s) "
                        "— treating as stale and proceeding with action center refresh",
                        running.id, running.started_at.isoformat(), age,
                    )
                else:
                    logger.info(
                        "Action center refresh skipped — nightly pipeline is running "
                        "(run #%d, age %s)",
                        running.id, age,
                    )
                    return
            if is_house_pipeline_running():
                house_age = house_pipeline_age()
                if house_age is not None and house_age > timedelta(hours=8):
                    # Same reasoning as the stale PipelineRun check above: a
                    # House run this old is wedged (normal runs are 1-2h), and
                    # on 2026-07-04 one hung in Phase 1 for 17h, silently
                    # starving the action center all day.
                    from app.ops_alerts import send_ops_alert
                    logger.warning(
                        "House pipeline has been running for %s — treating as "
                        "hung and proceeding with action center refresh",
                        house_age,
                    )
                    send_ops_alert(
                        "House pipeline overrun",
                        f"The House pipeline has been running for {house_age} "
                        "(normal is 1-2h) and is likely hung. The action center "
                        "is no longer waiting for it; the run may need "
                        "clear-stuck-house and a container restart.",
                        dedupe_key=f"house-overrun-{datetime.utcnow():%Y-%m-%d}",
                    )
                else:
                    logger.info("Action center refresh skipped — house pipeline is running")
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

    # Pipeline overrun watchdog — alerts once per run past the budget
    from app.ops_alerts import check_pipeline_overrun
    scheduler.add_job(
        lambda: threading.Thread(
            target=check_pipeline_overrun, daemon=True, name="pipeline-watchdog"
        ).start(),
        CronTrigger(minute="5,35"),
        id="pipeline_watchdog",
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
