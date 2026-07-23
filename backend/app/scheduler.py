import asyncio
import logging
import threading
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.pipeline.senate_pipeline import run_senate_pipeline
from app.pipeline.house_pipeline import run_house_pipeline, is_house_pipeline_running, house_pipeline_age
from app.pipeline.supplementary_pipeline import (
    run_supplementary_pipeline, is_supplementary_pipeline_running, supplementary_pipeline_age,
)
from app.pipeline.stock_pipeline import (
    run_stock_trades_pipeline, is_stock_pipeline_running, stock_pipeline_age,
)
from app.pipeline.analyze.action_center import get_action_refresh_state, refresh_action_issues
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def _is_stale(age: timedelta | None, threshold: timedelta) -> bool:
    """True when an in-progress run is older than `threshold` — old enough
    that it's more likely hung/crashed than genuinely still active, so the
    caller proceeds instead of waiting on it indefinitely. Shared by every
    running-process guard in `_hourly_action_refresh` below."""
    return age is not None and age > threshold


def _nightly_pipeline() -> None:
    """Run the nightly sequence: Senate, then explore docs/SCOTUS/
    presidents, then House, then stock trades — four independent
    pipelines run one after another, not one combined pipeline.

    Runs in a background thread with its own event loop so the main
    uvicorn loop stays responsive during long-running pipeline phases.

    Safe to call from multiple containers: ``run_senate_pipeline`` acquires
    a database-level lock and skips if another instance is already running.
    """
    from app.ops_alerts import check_current_congress_staleness, send_ops_alert

    def _alert_if_skipped(label: str, result: dict) -> bool:
        """Returns True (and alerts) if `result` reports the step was
        skipped — every step in the chain can genuinely report this now
        (2026-07-23: House/Stock/Supplementary gained the same DB-row
        lock Senate already had). Previously only Senate's skip was
        checked at all, and even that alert didn't mention that a Senate
        skip silently takes the ENTIRE rest of the chain with it —
        confirmed live as the likely root cause of stock-trades data
        going stale for 4+ days and supplementary data for 1+ day, with
        no alert ever firing for either.
        """
        if result.get("status") != "skipped":
            return False
        logger.info("%s pipeline skipped — %s", label, result.get("reason", "unknown reason"))
        send_ops_alert(
            f"Nightly {label} run skipped",
            f"The scheduled {label} pipeline did not start because a "
            f"previous run of it was still active. {label} data will be a "
            "day stale unless triggered manually. If this was Senate, "
            "note that Supplementary/House/Stock never ran either tonight "
            "— the chain stops here, it does not skip just this one step.",
            dedupe_key=f"skipped-{label.lower()}-{utcnow():%Y-%m-%d}",
        )
        return True

    def _run():
        # Loud, deduped alert if CURRENT_CONGRESS has fallen behind the
        # calendar before we score another day against a possibly-dead one.
        check_current_congress_staleness()
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run_senate_pipeline())
            if _alert_if_skipped("Senate", result):
                return

            logger.info("Senate pipeline done — starting supplementary pipeline")
            supp_result = loop.run_until_complete(run_supplementary_pipeline())
            logger.info("Supplementary pipeline: %s", supp_result)
            if _alert_if_skipped("Supplementary", supp_result):
                return

            logger.info("Supplementary pipeline done — starting House pipeline")
            house_result = loop.run_until_complete(run_house_pipeline())
            logger.info("House pipeline: %s", house_result)
            if _alert_if_skipped("House", house_result):
                return

            # Both chambers' sponsored-bill rows were just rewritten —
            # swap fresh data into the /api/bills collection cache now
            # instead of waiting out its TTL.
            from app.services.bill_service import warm_bill_collection_cache
            warm_bill_collection_cache()
            logger.info("House pipeline done — starting stock trades pipeline")
            stock_result = loop.run_until_complete(run_stock_trades_pipeline())
            logger.info("Stock trades pipeline: %s", stock_result)
            _alert_if_skipped("Stock trades", stock_result)
        except BaseException as e:
            logger.exception("Nightly pipeline failed")
            send_ops_alert(
                "Nightly pipeline crashed",
                f"{type(e).__name__}: {e}",
                dedupe_key=f"crashed-{utcnow():%Y-%m-%d}",
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
                age = utcnow() - started if started else None
                if _is_stale(age, timedelta(hours=4)):
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
            from app.models import PipelineRun, PipelineStatus
            db = SessionLocal()
            try:
                running = db.query(PipelineRun).filter(PipelineRun.status == PipelineStatus.RUNNING).first()
            finally:
                db.close()
            if running:
                age = utcnow() - running.started_at
                if _is_stale(age, timedelta(hours=8)):
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
                if _is_stale(house_age, timedelta(hours=8)):
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
                        dedupe_key=f"house-overrun-{utcnow():%Y-%m-%d}",
                    )
                else:
                    logger.info("Action center refresh skipped — house pipeline is running")
                    return
            if is_supplementary_pipeline_running():
                supp_age = supplementary_pipeline_age()
                # 8h, not stock's 2h: on its weekly SCOTUS-refresh day this
                # pipeline includes the uncached per-case Oyez crawl, which
                # took 5h+ in run 69 — a tight threshold would misfire as
                # "hung" on a run that's just legitimately slow that day.
                if _is_stale(supp_age, timedelta(hours=8)):
                    from app.ops_alerts import send_ops_alert
                    logger.warning(
                        "Supplementary pipeline has been running for %s — "
                        "treating as hung and proceeding with action center refresh",
                        supp_age,
                    )
                    send_ops_alert(
                        "Supplementary pipeline overrun",
                        f"The supplementary (explore/SCOTUS/presidents) pipeline "
                        f"has been running for {supp_age} and is likely hung. "
                        "The action center is no longer waiting for it.",
                        dedupe_key=f"supplementary-overrun-{utcnow():%Y-%m-%d}",
                    )
                else:
                    logger.info("Action center refresh skipped — supplementary pipeline is running")
                    return
            if is_stock_pipeline_running():
                stock_age = stock_pipeline_age()
                # Shorter overrun threshold than House's 8h: stock trades is
                # PDF/OCR parsing over a bounded PTR filing set, not a
                # 431-member scoring pass — a confirmed live run took ~90min
                # (2026-07-15), so 2h already gives 20x headroom over that.
                if _is_stale(stock_age, timedelta(hours=2)):
                    from app.ops_alerts import send_ops_alert
                    logger.warning(
                        "Stock trades pipeline has been running for %s — "
                        "treating as hung and proceeding with action center refresh",
                        stock_age,
                    )
                    send_ops_alert(
                        "Stock trades pipeline overrun",
                        f"The stock trades pipeline has been running for {stock_age} "
                        "(normal is under 2h) and is likely hung. The action center "
                        "is no longer waiting for it.",
                        dedupe_key=f"stock-overrun-{utcnow():%Y-%m-%d}",
                    )
                else:
                    logger.info("Action center refresh skipped — stock trades pipeline is running")
                    return
            count = refresh_action_issues()
            logger.info("Action center hourly refresh: %d issues", count)
            # Issue mentions feed the bills view's "hot" ranking — rebuild
            # its collection cache in the background so the new ranking is
            # served immediately rather than after the cache TTL.
            from app.services.bill_service import warm_bill_collection_cache
            warm_bill_collection_cache()
        except Exception:
            logger.exception("Action center refresh failed")

    threading.Thread(target=_run, daemon=True, name="action-refresh").start()


def _hourly_bill_status_refresh() -> None:
    """Incremental Congress.gov bill-status sync (pipeline/bill_refresh.py)
    in a background thread.

    Skipped while the nightly Senate/House pipelines are running: those
    rebuild the same rows wholesale (delete-then-insert per member), so a
    concurrent incremental pass would contend for SQLite's single writer
    lock only to update rows about to be replaced anyway. Uses the same
    stale-run overrides as _hourly_action_refresh so a wedged pipeline row
    can't silently starve bill freshness forever.
    """
    def _run():
        try:
            from app.pipeline.bill_refresh import is_bill_refresh_running, refresh_bill_statuses

            if is_bill_refresh_running():
                logger.info("Bill status refresh skipped — previous refresh still running")
                return
            from app.database import SessionLocal
            from app.models import PipelineRun, PipelineStatus
            db = SessionLocal()
            try:
                running = db.query(PipelineRun).filter(PipelineRun.status == PipelineStatus.RUNNING).first()
            finally:
                db.close()
            if running and not _is_stale(utcnow() - running.started_at, timedelta(hours=8)):
                logger.info("Bill status refresh skipped — nightly pipeline is running")
                return
            if is_house_pipeline_running() and not _is_stale(house_pipeline_age(), timedelta(hours=8)):
                logger.info("Bill status refresh skipped — house pipeline is running")
                return

            loop = asyncio.new_event_loop()
            try:
                summary = loop.run_until_complete(refresh_bill_statuses())
            finally:
                loop.close()
            logger.info("Bill status refresh: %s", summary)
        except Exception:
            logger.exception("Bill status refresh failed")

    threading.Thread(target=_run, daemon=True, name="bill-status-refresh").start()


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

    # Explicit UTC: without a timezone, APScheduler fires in whatever tz
    # the container happens to have — the docs promise "3 AM UTC" and the
    # same-day dedupe keys/date labels elsewhere assume the run date
    # doesn't float with container configuration.
    trigger = CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
        timezone="UTC",
    )

    scheduler.add_job(_nightly_pipeline, trigger, id="pipeline_run", replace_existing=True)

    scheduler.add_job(
        _hourly_action_refresh,
        CronTrigger(minute="15"),
        id="action_refresh",
        replace_existing=True,
    )

    # Hourly incremental bill-status sync — :45 so it doesn't stack on the
    # :15 action refresh or the :5/:35 watchdog on a 4-core Pi.
    scheduler.add_job(
        _hourly_bill_status_refresh,
        CronTrigger(minute="45"),
        id="bill_status_refresh",
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
    logger.info(
        "Scheduler started with cron: %s (+ hourly action refresh at :15, bill status refresh at :45)",
        settings.PIPELINE_CRON_SCHEDULE,
    )


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
