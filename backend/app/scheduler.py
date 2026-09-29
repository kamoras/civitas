import asyncio
import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.database import SessionLocal
from app.pipeline.senate_pipeline import run_senate_pipeline
from app.pipeline.house_pipeline import run_house_pipeline, is_house_pipeline_running, house_pipeline_age
from app.pipeline.supplementary_pipeline import (
    run_supplementary_pipeline, is_supplementary_pipeline_running, supplementary_pipeline_age,
)
from app.pipeline.stock_pipeline import (
    run_stock_trades_pipeline, is_stock_pipeline_running, stock_pipeline_age,
)
from app.pipeline.election_pipeline import (
    run_election_pipeline, is_election_pipeline_running, election_pipeline_age,
    run_ballot_sync, ballot_tracker,
)
from app.pipeline.analyze.action_center import get_action_refresh_state, refresh_action_issues
from app.pipeline.congress_activity import congress_sync_age, eastern_today, is_congress_sync_running, run_congress_sync
from app.pipeline.analyze.congress_bluesky import post_daily_congress, post_weekly_congress
from app.time_utils import utcnow
from app.background import WritesHeld, start_writer
from app.pipeline import lease

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def _is_stale(age: timedelta | None, threshold: timedelta) -> bool:
    """True when an in-progress run is older than `threshold` — old enough
    that it's more likely hung/crashed than genuinely still active, so the
    caller proceeds instead of waiting on it indefinitely. Shared by every
    running-process guard in `_hourly_action_refresh` below."""
    return age is not None and age > threshold


def _start_job(target, *, name: str, alert: bool = False) -> None:
    """Start a scheduled job's thread. While the admin data reset holds the
    database the job doesn't run this time: logged, and for the nightly
    chain — whose skip leaves the wiped database unbuilt for a day — an ops
    alert, as for any other skipped nightly run.

    A job that takes no run lock of its own holds its lease (lease.job or
    lease.tracked_job) while it runs, so a reset in another process sees it,
    and it sees the reset — taken inside the job, past its own checks, so a
    tick that bails holds nothing another entry point would skip over."""
    try:
        start_writer(target, name=name)
    except WritesHeld as held:
        logger.warning("%s", held)
        if alert:
            from app.ops_alerts import send_ops_alert

            send_ops_alert(
                "Nightly pipeline skipped: data reset in progress",
                f"{held}. Nothing ran tonight; trigger the pipeline once the reset has finished, or the "
                "database stays empty until tomorrow night's run.",
                dedupe_key=f"nightly-skipped-reset-{utcnow():%Y-%m-%d}",
            )


def _nightly_pipeline() -> None:
    """Run the nightly sequence: Senate, then explore docs/SCOTUS/
    presidents, then House, then stock trades, then elections — five
    independent pipelines run one after another, not one combined
    pipeline. A skip or a crash anywhere ends the chain there.

    Runs in a background thread with its own event loop so the main
    uvicorn loop stays responsive during long-running pipeline phases.

    Safe to call from multiple containers: ``run_senate_pipeline`` acquires
    a database-level lock and skips if another instance is already running.
    """
    from app.ops_alerts import (
        check_current_congress_staleness,
        check_feedback_token_expiration,
        check_state_pvi_staleness,
        send_ops_alert,
    )
    from app.pipeline.fetch.district_pvi import REFRESH_WHO as DISTRICT_PVI_REFRESH
    from app.pipeline.fetch.district_pvi import run_house_on_sitting_lines

    _CHAIN = ["Senate", "Supplementary", "House", "Stock trades", "Election"]

    def _alert_if_skipped(label: str, result: dict, *, chain_continues: bool = False) -> bool:
        """Returns True (and alerts) if `result` reports the step was
        skipped. Every step in the nightly chain shares the same DB-row
        lock, and a skip anywhere silently takes the rest of the chain
        down with it — this alert exists so a skip is never silent,
        since downstream data can otherwise go stale for days with no
        signal that anything is wrong. `chain_continues`: this skip does
        not end the chain (the alert says so).
        """
        if result.get("status") != "skipped":
            return False
        logger.info("%s pipeline skipped — %s", label, result.get("reason", "unknown reason"))
        rest = _CHAIN[_CHAIN.index(label) + 1:] if label in _CHAIN else []
        after = (
            "" if not rest else
            f"The rest of tonight's chain ({', '.join(rest)}) still runs." if chain_continues else
            f"The chain stops here — {', '.join(rest)} did not run tonight either."
        )
        send_ops_alert(
            f"Nightly {label} run skipped",
            f"The scheduled {label} pipeline did not start because {_skip_cause(result)}. {label} data will be a "
            f"day stale unless triggered manually. {after}".rstrip(),
            dedupe_key=f"skipped-{label.lower()}-{utcnow():%Y-%m-%d}",
        )
        return True

    def _skip_cause(result: dict) -> str:
        """What held the run off: the skip's own reason (every pipeline's
        lock refusal carries one — run_tracker.acquire_pipeline_lock_why),
        naming the lease's holder when the skip recorded it."""
        from app.pipeline.run_tracker import skip_reason_text

        return skip_reason_text(result.get("reason"), who=result.get("holder"))

    def _run():
        # Loud, deduped alerts before another night's scoring. Each is a
        # warning about the chain, never a reason to skip it: one that
        # raises is logged and the pipelines still run.
        pre_checks = (
            # CURRENT_CONGRESS fallen behind the calendar, before we score
            # another day against a possibly-dead one.
            check_current_congress_staleness,
            # FEEDBACK_TOKEN's mandatory PAT expiration — a real GitHub API
            # call, so this one is self-gated to run at most weekly.
            check_feedback_token_expiration,
            # state_pvi.json's election-year window — this one can't
            # self-advance (see the check's own docstring for why), so the
            # alert is the only signal that a manual refresh is due.
            check_state_pvi_staleness,
        )
        for check in pre_checks:
            try:
                check()
            except Exception:
                logger.exception("Pre-pipeline check %s failed", check.__name__)
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
            # The House run settles the sitting Congress's district lines
            # first, under a lease it holds until its scoring is done
            # (fetch/district_pvi.run_house_on_sitting_lines): the sitting
            # Congress is read from the clock, not CURRENT_CONGRESS, so the
            # first House run after noon ET on Jan 3 of an odd year (with
            # the default 03:00 UTC schedule, the Jan 4 nightly) switches
            # to the new Congress's pinned table from disk — no fetch, no
            # restart — and a pin advanced in district_pvi_sources.json is
            # fetched the next run. Triggered House runs go through it too.
            house_result = loop.run_until_complete(run_house_on_sitting_lines(run_house_pipeline))
            logger.info("House pipeline: %s", house_result)
            # A District PVI refresh is waited for; one that outlasts the
            # wait (stuck) costs tonight's House scores but not Stock
            # trades or Election, which don't read them.
            held_by_refresh = house_result.get("holder") == DISTRICT_PVI_REFRESH
            if _alert_if_skipped("House", house_result, chain_continues=held_by_refresh) and not held_by_refresh:
                return

            # Both chambers' sponsored-bill rows were just rewritten —
            # swap fresh data into the /api/bills collection cache now
            # instead of waiting out its TTL.
            from app.services.bill_service import warm_bill_collection_cache
            warm_bill_collection_cache()
            logger.info("House pipeline done — starting stock trades pipeline")
            stock_result = loop.run_until_complete(run_stock_trades_pipeline())
            logger.info("Stock trades pipeline: %s", stock_result)
            if _alert_if_skipped("Stock trades", stock_result):
                return

            logger.info("Stock trades pipeline done — starting election pipeline")
            election_result = loop.run_until_complete(run_election_pipeline())
            logger.info("Election pipeline: %s", election_result)
            _alert_if_skipped("Election", election_result)
        except BaseException as e:
            logger.exception("Nightly pipeline failed")
            send_ops_alert(
                "Nightly pipeline crashed",
                f"{type(e).__name__}: {e}",
                dedupe_key=f"crashed-{utcnow():%Y-%m-%d}",
            )
        finally:
            loop.close()

    _start_job(_run, name="nightly-pipeline", alert=True)


def _hourly_action_refresh() -> None:
    """Refresh the action center in a background thread.

    Skipped when the nightly pipeline is running to avoid competing for
    memory during the most intensive part of the pipeline, or when the
    previous hourly refresh is still running — a slow/degraded local LLM
    can make one cycle run long enough to still be active when the next
    cron tick fires, and without this guard that spawns a second thread
    competing for the same LLM, compounding the slowdown rather than
    just running a bit late.
    """
    def _run():
        try:
            state = get_action_refresh_state()
            if state.get("is_running"):
                started = state.get("started_at")
                age = utcnow() - started if started else None
                if _is_stale(age, lease.max_hold(lease.ACTION_REFRESH)):
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
            # A run "running" for >8h almost certainly crashed without
            # updating its status, and one whose lease no live run holds
            # did (run_tracker.live_run): proceed past either rather than
            # blocking the action center indefinitely.
            from app.database import SessionLocal
            from app.models import PipelineRun
            from app.pipeline.run_tracker import live_run
            db = SessionLocal()
            try:
                running = live_run(db, PipelineRun, timedelta(hours=8))
            finally:
                db.close()
            if running is not None:
                logger.info(
                    "Action center refresh skipped — nightly pipeline is running (run #%d, age %s)",
                    running.id, utcnow() - running.started_at,
                )
                return
            if is_house_pipeline_running():
                house_age = house_pipeline_age()
                if _is_stale(house_age, timedelta(hours=8)):
                    # Same reasoning as the stale PipelineRun check above: a
                    # House run this old is wedged, not just slow (normal
                    # runs are 1-2h) — left unchecked, a hung run would
                    # silently starve the action center of fresh data all day.
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
                # 8h, not stock's shorter stock_trades_overrun_budget(): on
                # its weekly SCOTUS-refresh day this pipeline includes the
                # uncached per-case Oyez crawl, which can run 5h+ — a tight threshold would misfire as "hung"
                # on a run that's just legitimately slow that day.
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
                # 431-member scoring pass. The budget (2h for the trade
                # phases, which normally finish in under 90 minutes, plus
                # the annual-holdings phases' own ceiling) is shared with
                # ops_alerts.check_pipeline_overrun.
                from app.ops_alerts import stock_trades_overrun_budget

                if _is_stale(stock_age, stock_trades_overrun_budget()):
                    from app.ops_alerts import send_ops_alert
                    logger.warning(
                        "Stock trades pipeline has been running for %s — "
                        "treating as hung and proceeding with action center refresh",
                        stock_age,
                    )
                    send_ops_alert(
                        "Stock trades pipeline overrun",
                        f"The stock trades pipeline has been running for {stock_age}, past its "
                        f"{stock_trades_overrun_budget()} budget, and is likely hung. The action center "
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

    _start_job(_run, name="action-refresh")


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
            from app.pipeline.bill_refresh import bill_tracker, refresh_bill_statuses

            from app.database import SessionLocal
            from app.models import PipelineRun
            from app.pipeline.run_tracker import run_in_progress
            db = SessionLocal()
            try:
                running = run_in_progress(db, PipelineRun, timedelta(hours=8))  # live_run's liveness
            finally:
                db.close()
            if running:
                logger.info("Bill status refresh skipped — nightly pipeline is running")
                return
            if is_house_pipeline_running() and not _is_stale(house_pipeline_age(), timedelta(hours=8)):
                logger.info("Bill status refresh skipped — house pipeline is running")
                return

            # Under its tracker and lease (lease.run_tracked), so a pass in
            # this process, a reset or a refresh in another process sees it,
            # taken only past the checks above: a tick that bails holds
            # nothing. Cut off where they stop holding, never left running
            # beside the next: two passes at once would let the older one's
            # snapshot overwrite the newer one's rows.
            summary = lease.run_tracked(
                lease.BILL_REFRESH, bill_tracker(), refresh_bill_statuses, who="Bill status refresh",
            )
            if summary is not None:
                logger.info("Bill status refresh: %s", summary)
        except Exception:
            logger.exception("Bill status refresh failed")

    _start_job(_run, name="bill-status-refresh")


def _election_coverage_refresh() -> None:
    """Tighter-cadence race-coverage ingestion during election season.

    election_pipeline.py's full run (roster sync + financial refresh +
    coverage + posting) is nightly-only, same as every other pipeline —
    roster/financials don't change minute to minute. Coverage is the one
    phase that genuinely benefits from a tighter cadence as an election
    approaches, so this runs ONLY that phase (plus posting), and only
    within is_election_season's window — a no-op the rest of the year,
    same shape as _hourly_action_refresh's existing running-pipeline guards.
    """
    from app.api.action import is_election_season

    if not is_election_season():
        return

    def _run():
        from app.pipeline.analyze.election_coverage import (
            coverage_tracker,
        )

        if is_election_pipeline_running():
            age = election_pipeline_age()
            if not _is_stale(age, timedelta(hours=2)):
                logger.info("Election coverage refresh skipped — election pipeline is running")
                return
            logger.warning(
                "Election pipeline has been running for %s — treating as hung "
                "and proceeding with coverage refresh anyway", age,
            )
        # Self-overlap guard: the PREVIOUS 15-minute refresh may still be
        # mid-flight (degraded LLM, slow network) — overlapping passes
        # double-ingest and double-post; so may the nightly election
        # pipeline's coverage phase. Under its tracker and lease
        # (lease.run_tracked), taken only now, past the check above: a tick
        # that bails must not hold them, or the nightly phase reaching them
        # at that moment would skip. Cut off where they stop holding; the
        # posting loop, which doesn't await, stops at the same deadline.
        from app.database import SessionLocal
        from app.pipeline.analyze.election_bluesky import post_race_coverage_updates
        from app.pipeline.analyze.election_coverage import ingest_race_coverage

        async def _refresh():
            deadline = lease.deadline(lease.COVERAGE_REFRESH)
            db = SessionLocal()
            try:
                ingested = await ingest_race_coverage(db)
                posted = post_race_coverage_updates(db, deadline=deadline)
                logger.info(
                    "Election-season coverage refresh: %d ingested, %d posted",
                    ingested, posted,
                )
            finally:
                db.close()

        try:
            lease.run_tracked(
                lease.COVERAGE_REFRESH, coverage_tracker(), _refresh, who="Election coverage refresh",
            )
        except Exception:
            logger.exception("Election coverage refresh failed")

    _start_job(_run, name="election-coverage-refresh")


def _election_ballot_sync() -> None:
    """Every state's ballot list, between nightly runs, in election season.

    The nightly election pipeline runs LAST in the chain (Senate ->
    Supplementary -> House -> Stock -> Election), and a skip or crash
    anywhere upstream ends the chain for the night — ballots would go a
    day stale for reasons unrelated to elections, in exactly the weeks
    voters are reading them. This runs only the ballot step
    (run_ballot_sync) on its own clock, so a withdrawal or replacement
    reaches the page within hours. It reads state election offices'
    published lists only — a few requests per state at one per second — so
    the cadence is cheap; the roster and financial refresh stay nightly.
    A no-op outside is_election_season's window, like the coverage refresh.
    """
    from app.api.action import is_election_season

    if not is_election_season():
        return

    def _run():
        if is_election_pipeline_running():
            age = election_pipeline_age()
            if not _is_stale(age, timedelta(hours=6)):
                logger.info("Ballot sync skipped — the nightly election pipeline is running")
                return
            logger.warning(
                "Election pipeline has been running for %s — treating as hung "
                "and proceeding with the ballot sync anyway", age,
            )
        # Under its tracker and lease (lease.run_tracked), shared with the
        # nightly pipeline's ballot step, taken only now, past the check
        # above: a tick that bails must not hold them, or the nightly step
        # reaching them at that moment would skip. Cut off where they stop
        # holding.
        try:
            result = lease.run_tracked(lease.BALLOT_SYNC, ballot_tracker(), run_ballot_sync, who="Ballot sync")
            if result is None:
                return
            logger.info(
                "Election-season ballot sync: %d confirmed, %d states ok, failed: %s",
                result["confirmed"], len(result["statesOk"]), result["statesFailed"] or "none",
            )
        except Exception:
            logger.exception("Election-season ballot sync failed")

    _start_job(_run, name="election-ballot-sync")


def _congress_activity_sync() -> None:
    """The /congress record: new roll calls, today's floor logs and the
    Daily Digest (pipeline/congress_activity.py). Half-hourly all year:
    the chambers' logs are live during a session day, and each run is a
    few requests when nothing is new."""
    def _run():
        if is_congress_sync_running():
            age = congress_sync_age()
            if not _is_stale(age, lease.max_hold(lease.CONGRESS_SYNC)):
                logger.info("Congress sync skipped — the previous one is still running")
                return
            logger.warning("Previous Congress sync has been running for %s — proceeding anyway", age)
        # Its lease, taken past the check above, so a tick that bails holds
        # nothing, and a data reset in any process sees the sync (and the
        # sync sees the reset).
        with lease.job(lease.CONGRESS_SYNC, who="Congress sync") as held:
            if not held:
                return
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(run_congress_sync())
                logger.info(
                    "Congress sync: roll calls %s; floor logs %s; digests %s",
                    {k: v["stored"] for k, v in result["rollCalls"].items()},
                    result["floorLogs"], result["digests"],
                )
            except Exception:
                logger.exception("Congress sync failed")
            finally:
                loop.close()
            # The day's Bluesky post, once its Digest has made it final.
            # After the sync, so a day finalized this run posts this run.
            db = SessionLocal()
            try:
                posted = post_daily_congress(db, eastern_today())
                if posted:
                    logger.info("Posted the Congress day %s to Bluesky", posted)
                week = post_weekly_congress(db, eastern_today())
                if week:
                    logger.info("Posted the Congress week of %s to Bluesky", week)
            except Exception:
                logger.exception("Congress Bluesky post failed")
            finally:
                db.close()

    _start_job(_run, name="congress-activity-sync")


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

    # Election-season coverage refresh — every 15 min, but a no-op outside
    # is_election_season's window (checked inside the job itself, not the
    # trigger, so this doesn't need its own enable/disable toggle).
    scheduler.add_job(
        _election_coverage_refresh,
        CronTrigger(minute="*/15"),
        id="election_coverage_refresh",
        replace_existing=True,
    )

    # Election-season ballot sync — every 6 hours at :50 (clear of the :45
    # bill refresh and the 03:00 nightly start), a no-op outside the season
    # window like the coverage refresh above. Six hours because certified
    # lists change by withdrawals and replacements, days apart, not minutes.
    scheduler.add_job(
        _election_ballot_sync,
        CronTrigger(hour="*/6", minute="50", timezone="UTC"),
        id="election_ballot_sync",
        replace_existing=True,
    )

    # The /congress record — half-hourly at :10/:40, clear of the :15
    # action refresh and the :45 bill refresh.
    scheduler.add_job(
        _congress_activity_sync,
        CronTrigger(minute="10,40", timezone="UTC"),
        id="congress_activity_sync",
        replace_existing=True,
    )

    # Pipeline overrun watchdog — alerts once per run past the budget
    from app.ops_alerts import check_pipeline_overrun, check_pipeline_staleness
    scheduler.add_job(
        # Writers too (the alert history lives in api_cache).
        lambda: _start_job(check_pipeline_overrun, name="pipeline-watchdog"),
        CronTrigger(minute="5,35"),
        id="pipeline_watchdog",
        replace_existing=True,
    )

    # Pipeline staleness watchdog — the complement to the overrun check
    # above: that one watches a run that EXISTS and is taking too long,
    # this one watches for a run that never happened at all. Hourly
    # rather than half-hourly since it is measured in days, and it
    # dedupes per pipeline per day regardless.
    scheduler.add_job(
        lambda: _start_job(check_pipeline_staleness, name="pipeline-staleness-watchdog"),
        CronTrigger(minute="20"),
        id="pipeline_staleness_watchdog",
        replace_existing=True,
    )

    # A run a crash or a rollout left RUNNING, once it is proven dead
    # (run_tracker.tidy_dead_runs): run history stops showing it as running.
    # Readers already see through it (run_tracker.live_run).
    from app.pipeline.run_tracker import tidy_dead_runs

    scheduler.add_job(
        lambda: _start_job(tidy_dead_runs, name="dead-run-tidy"),
        CronTrigger(minute="25"),
        id="dead_run_tidy",
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
