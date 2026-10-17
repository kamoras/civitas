import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.database import SessionLocal
from app.error_utils import exception_summary
from app.forecast.build import check_training_staleness as check_election_map_training_staleness
from app.forecast.build import run_refresh as election_map_refresh
from app.pipeline.senate_pipeline import run_senate_pipeline
from app.pipeline.house_pipeline import run_house_pipeline, is_house_pipeline_running, house_pipeline_hung
from app.pipeline.supplementary_pipeline import (
    run_supplementary_pipeline, is_supplementary_pipeline_running, supplementary_pipeline_hung,
)
from app.pipeline.stock_pipeline import (
    run_stock_trades_pipeline, is_stock_pipeline_running, stock_pipeline_hung,
)
from app.pipeline.election_pipeline import (
    run_election_pipeline, is_election_pipeline_running, election_pipeline_hung,
    run_ballot_sync, ballot_tracker,
)
from app.pipeline.analyze.action_center import get_action_refresh_state, refresh_action_issues
from app.pipeline.congress_activity import congress_sync_age, eastern_today, is_congress_sync_running, run_congress_sync
from app.pipeline.analyze.congress_bluesky import post_congress_lookahead, post_daily_congress, post_weekly_congress
from app.time_utils import utcnow
from app import calibration_drift
from app import data_refresh
from app.background import WritesHeld, start_writer
from app.broadcast import deliver_pending, refresh_engagement
from app.pipeline.analyze.trend_poster import run_trend_posts
from app.whats_new import post_whats_new
from app.pipeline import lease
from app.pipeline import bill_refresh
from app.pipeline.fetch import nominations
from app.pipeline.fetch import bill_display_titles
from app.pipeline.fetch.agencies import run_agency_sync
from app.pipeline.fetch.amendment_purpose import fill_amendment_purposes
from app.http_client import make_async_client
from app.services.bill_service import warm_bill_collection_cache
from app import site_search_index

if TYPE_CHECKING:
    from app.pipeline_chain import Link

logger = logging.getLogger(__name__)

# A job whose moment passes while the event loop is busy still runs (up to
# five minutes late, once however many moments it missed) instead of being
# skipped: APScheduler's default grace is one second, and a skipped
# heartbeat reads as a dead pipeline service, a skipped nightly as a lost
# day of data.
scheduler = AsyncIOScheduler(job_defaults={"misfire_grace_time": 300, "coalesce": True})


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
    # start_writer runs the job inside app.config.scoring_congress: one
    # Congress for the whole job — the nightly chain's Senate, Supplementary
    # and House runs included — so its scored windows and House members'
    # district lines move together, at the first job to start after noon ET
    # on Jan 3.
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
                condition="nightly-skipped-reset",
            )


def warm_bills() -> None:
    """After House ran (the nightly run's or a trigger's — not when it was
    skipped): both chambers' sponsored-bill rows may have been rewritten —
    swapped into the /api/bills collection cache now rather than after its
    TTL."""
    from app.services.bill_service import warm_bill_collection_cache

    warm_bill_collection_cache()


async def run_house_on_the_sitting_lines() -> dict:
    """The chain's House link: the House run settles the sitting Congress's
    district lines first, under a lease it holds until its scoring is done
    (fetch/district_pvi.run_house_on_sitting_lines), waiting out a District
    PVI refresh or the startup rescore holding them. The sitting Congress is
    the one this whole job holds (app.config.scoring_congress, via
    start_writer) — the same value every scored window reads — so the first
    job to start after noon ET on Jan 3 of an odd year (with the default
    03:00 UTC schedule, the chain that starts that evening) switches windows
    and lines together, to the new Congress's pinned table from disk — no
    fetch, no restart — and a pin advanced in district_pvi_sources.json is
    fetched the next run."""
    from app.pipeline.fetch.district_pvi import run_house_on_sitting_lines

    # Through the module's name at run time: tests patch it.
    return await run_house_on_sitting_lines(lambda: run_house_pipeline())


def nightly_links() -> "list[Link]":
    """The nightly run's pipelines, in order (app.pipeline_chain). A full
    trigger runs the same list (triggered_chain)."""
    from app.pipeline_chain import Link

    # Called through the module's names at run time: tests patch them.
    return [
        Link("Senate", lambda: run_senate_pipeline()),
        Link("Supplementary", lambda: run_supplementary_pipeline()),
        Link("House", run_house_on_the_sitting_lines, after=warm_bills),
        Link("Stock trades", lambda: run_stock_trades_pipeline()),
        Link("Election", lambda: run_election_pipeline()),
    ]


def pipelines_running() -> bool:
    """Whether any of the chain's pipelines is running — what a link held
    off by another run waits on (app.pipeline_chain). Only a run that is
    live: every pipeline runs in the one pipeline process, so House,
    Supplementary, Stock trades and Election count by their in-process flag
    (a RUNNING row with the flag down is a dead run's leftover — the admin
    status calls it stuck), and the Senate by a row its live lease speaks
    for (run_tracker.senate_run_state). Each only until its run is stale
    (no progress for run_tracker.QUIET_PIPELINE_TIMEOUT, or past the age
    ceiling): hung, its lock taken over from then — a wait on it must end."""
    from app.pipeline.run_tracker import senate_run_state

    for running, hung in (
        (is_house_pipeline_running, house_pipeline_hung),
        (is_supplementary_pipeline_running, supplementary_pipeline_hung),
        (is_stock_pipeline_running, stock_pipeline_hung),
        (is_election_pipeline_running, election_pipeline_hung),
    ):
        if running() and not hung():
            return True
    db = SessionLocal()
    try:
        _row, senate_running, senate_clearable = senate_run_state(db)
        return senate_running and not senate_clearable
    finally:
        db.close()


def reporter(run_name: str):
    """Each link's outcome in a chain of runs `run_name` ("Nightly",
    "Triggered"): alerted when it didn't run, failed or crashed — never
    silent, since its data otherwise goes a day stale with no signal — and
    its alerts resolved when it ran. One set of conditions per pipeline,
    whichever run reports: a trigger that runs it cleanly clears the
    nightly one's alert."""
    from app.ops_alerts import resolve_ops_alert, send_ops_alert
    from app.pipeline.run_tracker import SUPERSEDED, skip_reason_text
    from app.pipeline_chain import CRASHED, FAILED, ends_chain

    def report(link, outcome) -> None:
        slug = link.label.lower().replace(" ", "-")
        skipped, crashed = f"nightly-skipped-{slug}", f"nightly-crashed-{slug}"
        tag = f"{run_name.lower()}-{slug}-{utcnow():%Y-%m-%d}"
        if outcome.status == "skipped":
            reason = (outcome.result or {}).get("reason")
            if reason == SUPERSEDED:
                # A House step whose job started before noon ET on Jan 3,
                # after a newer job had moved to the new Congress: nothing is
                # wrong, the next job scores the House on it
                # (district_pvi._superseded). Not a skip to alert on — and it
                # clears one left from before.
                logger.info("%s step left to the next job: %s", link.label, skip_reason_text(SUPERSEDED))
                resolve_ops_alert(skipped)
                return
            logger.info("%s pipeline skipped — %s", link.label, reason or "unknown reason")
            rest = ("The rest of its chain is not run: every later pipeline would be refused the same way."
                    if ends_chain(outcome) else "The rest of its chain still runs.")
            send_ops_alert(
                f"{run_name} {link.label} run skipped",
                # Naming the lease's holder when the skip recorded it (a
                # House run held off by a stuck District PVI refresh).
                f"The {link.label} pipeline did not start because "
                f"{skip_reason_text(reason, who=(outcome.result or {}).get('holder'))}. "
                f"{link.label} data will be a day stale unless triggered again. {rest}",
                dedupe_key=f"skipped-{tag}",
                condition=skipped,
            )
            return
        resolve_ops_alert(skipped)
        # A link reached: a reset no longer holds the nightly run off.
        resolve_ops_alert("nightly-skipped-reset")
        if outcome.status in FAILED:
            # Raised, or caught its own error and said so (House,
            # Supplementary and Election return "failed"; House "no_data"
            # for an empty roster).
            # The cause's head line (where, type, message, the frame it
            # raised in); the traceback is on the run row, which outlives
            # the container's log.
            if outcome.status == CRASHED:
                why = exception_summary(outcome.error)
            else:
                detail = (outcome.result or {}).get("error") or (outcome.result or {}).get("reason")
                why = f"it ended {outcome.status} ({detail or 'see its run row'})"
            send_ops_alert(
                f"{run_name} {link.label} run {'crashed' if outcome.status == CRASHED else 'failed'}",
                f"{why}. The traceback is on the run row (admin, Pipelines). {link.label} data will be "
                "a day stale unless triggered again. The rest of its chain still runs.",
                dedupe_key=f"crashed-{tag}",
                condition=crashed,
            )
        else:
            resolve_ops_alert(crashed)

    return report


def triggered_chain(chain_id: int):
    """What a full pipeline trigger runs, under the chain slot it claimed
    (pipeline_chain.claim): the nightly chain's five pipelines, each
    whatever the one before it did (so a trigger recovers any of them, not
    only the first), reported like the nightly run."""
    from app.pipeline_chain import run_chain

    async def chain() -> None:
        await run_chain(nightly_links(), reporter("Triggered"), busy=pipelines_running, chain_id=chain_id,
                        name="Triggered")

    return chain


# How old an interrupted chain may be and still be resumed: within a day,
# every link before the one it was on ran within the day too, so if the
# resumed chain is still going at the next nightly run (which then leaves
# the night to it, as to a trigger), no pipeline's data is a day stale.
RESUME_WITHIN = timedelta(days=1)


def resume_interrupted_chain() -> None:
    """At the pipeline process's start: resume a chain the last process died
    in (app.pipeline_chain.take_cut), from the link it was on, which runs
    again from its start (a killed run's row was swept stale). Once: a
    resumed chain cut short again is alerted, not resumed — the link may be
    what kills the process. Not one older than RESUME_WITHIN (the nightly
    run has the data by then), nor one whose link this release no longer
    has. A deploy never lands here mid-chain (check-and-deploy.sh waits for
    pipelineChainIsRunning) unless the chain was wedged — and resuming the
    wedged link once on the release that fixes it is what's wanted."""
    from app.ops_alerts import send_ops_alert
    from app.pipeline_chain import claim, release, run_chain, take_cut

    cut = take_cut()
    if cut is None:
        return
    links = nightly_links()
    labels = [link.label for link in links]
    where = f"The {cut.name.lower()} pipeline chain started {cut.started_at:%Y-%m-%d %H:%M} UTC"
    if cut.link not in labels:
        logger.warning("%s was cut short in a %s link this release doesn't have — not resumed", where, cut.link)
        return
    if cut.resumed:
        send_ops_alert(
            "Resumed pipeline chain cut short again",
            f"{where} was cut short by a restart, resumed, and cut short again in its {cut.link} link: "
            f"it is not resumed a second time, since the {cut.link} run may be what stops the process "
            "(memory, a health-check kill). Pipelines from that link on did not run; the next nightly run "
            "tries them.",
            dedupe_key=f"chain-cut-again-{cut.started_at:%Y-%m-%d}",
            condition="chain-cut-again",
        )
        return
    if utcnow() - cut.started_at > RESUME_WITHIN:
        logger.info("%s was cut short in its %s link over a day ago — left to the nightly run", where, cut.link)
        return
    chain_id = claim()
    if chain_id is None:  # Nothing else runs this early in the process: defensive.
        logger.warning("%s was cut short in its %s link; another chain holds the slot — not resumed",
                       where, cut.link)
        return
    logger.warning("%s was cut short by a restart in its %s link — resuming it from there", where, cut.link)
    rest = links[labels.index(cut.link):]

    def _run() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run_chain(
                rest, reporter("Resumed"), busy=pipelines_running, chain_id=chain_id, name=cut.name,
                started_at=cut.started_at, resumed=True,
            ))
        except BaseException:
            logger.exception("Resumed pipeline chain stopped")
        finally:
            loop.close()

    try:
        start_writer(_run, name="resumed-pipeline-chain")
    except WritesHeld as held:
        release(chain_id)
        logger.warning("%s was cut short in its %s link; not resumed: %s", where, cut.link, held)


def _nightly_pipeline() -> None:
    """Run the nightly sequence: Senate, then explore docs/SCOTUS/
    presidents, then House, then stock trades, then elections — five
    independent pipelines run one after another, not one combined
    pipeline. One at a time for the Pi's memory, but none waits on the
    one before it succeeding: a skip, failure or crash is alerted and the
    next still runs (app.pipeline_chain).

    Runs in a background thread with its own event loop so the main
    uvicorn loop stays responsive during long-running pipeline phases.
    """
    from app.ops_alerts import (
        check_current_congress_staleness,
        check_election_readiness,
        check_feedback_token_expiration,
        check_sitting_president,
        check_state_pvi_staleness,
        resolve_ops_alert,
        send_ops_alert,
    )
    from app.pipeline_chain import run_chain

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
            # Each hand-labelled calibration against the stream it gates
            # (pass rates since it was fitted).
            calibration_drift.check_all,
            # The coming election's cycle-specific data (maps, district
            # lean, voter calendar, poll closing times, town sample
            # ballots), each alerted once its lead time begins.
            check_election_readiness,
            # The presidents table's sitting president against the Federal
            # Register's newest presidential document (an inauguration the
            # roster hasn't caught up with).
            check_sitting_president,
            # The election map's fits stop at their newest trained cycle;
            # like state_pvi.json, nothing refits them on its own.
            check_election_map_training_staleness,
        )
        for check in pre_checks:
            try:
                check()
            except Exception:
                logger.exception("Pre-pipeline check %s failed", check.__name__)
        loop = asyncio.new_event_loop()
        try:
            # None: a triggered chain is running the same five pipelines,
            # and reports them — tonight is left to it.
            if loop.run_until_complete(run_chain(
                nightly_links(), reporter("Nightly"), busy=pipelines_running, name="Nightly",
            )) is not None:
                # The whole chain's alert, from before each link had its own.
                resolve_ops_alert("nightly-crashed")
        except BaseException as e:
            # Only what the chain itself doesn't absorb — cancellation, an
            # exit — ends it early.
            logger.exception("Nightly pipeline chain stopped")
            send_ops_alert(
                "Nightly pipeline chain stopped",
                f"{exception_summary(e)}. The pipelines after the one running then did not run tonight.",
                dedupe_key=f"crashed-{utcnow():%Y-%m-%d}",
                condition="nightly-crashed",
            )
        finally:
            loop.close()

    _start_job(_run, name="nightly-pipeline", alert=True)


def _hourly_action_refresh() -> None:
    """Refresh the action center in a background thread.

    Skipped only when the previous hourly refresh is still running — a
    slow/degraded local LLM can make one cycle run long enough to still be
    active when the next cron tick fires, and without this guard that spawns
    a second thread competing for the same LLM, compounding the slowdown
    rather than just running a bit late.

    It runs alongside the nightly chain. Until 2026-10 it stood down for the
    Senate, Supplementary, House and Stock trades links, a rule from 2026-07
    ("competing for memory"), and the Action Center went as stale as the
    chain was long: 13 hours on 2026-10-14. Measured then, it doesn't
    compete: a refresh takes 2-6 minutes; the pipeline process peaked at
    3.5 GB over a day that held both refreshes and the chain, so the two
    together stay under 7 GB, with the llama-server, API and frontend
    processes 5.7 GB more, on a 16 GB Pi; most of the chain waits on
    upstream rate limits; and it already ran beside the Election link,
    the Congress sync and the election syncs, all writers, with no
    "database is locked" in the logs. Overruns are the pipeline watchdog's
    to report (ops_alerts.check_pipeline_overrun covers all four links).
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
            count = refresh_action_issues()
            logger.info("Action center hourly refresh: %d issues", count)
            # Issue mentions feed the bills view's "hot" ranking — rebuild
            # its collection cache in the background so the new ranking is
            # served immediately rather than after the cache TTL.
            from app.services.bill_service import warm_bill_collection_cache
            warm_bill_collection_cache()
        except Exception:
            logger.exception("Action center refresh failed")
        _post_trends()
        # The calibrated gates' decisions this hour (calibration_drift).
        calibration_drift.flush()

    _start_job(_run, name="action-refresh")


def _post_trends() -> None:
    """The hourly trend posts (analyze/trend_poster.py), after the refresh
    so an issue posted this hour already counts as covering its record, and
    the engagement read their image-vs-card trial is measured by; first,
    the hour's "New on Civitas" check (app/whats_new.py). Under the Action
    Center's lease, which yields to a data reset."""
    db = SessionLocal()
    try:
        with lease.holding(db, lease.ACTION_REFRESH, yield_to=lease.DATA_RESET) as token:
            if token is None:
                logger.info("Trend posts skipped: %s", lease.code_and_refusal(db, lease.ACTION_REFRESH)[1])
                return
            try:
                logger.info("What's new: %s", post_whats_new(db))
            except Exception:
                logger.exception("What's new post failed (non-fatal)")
            logger.info("Trend posts: %s", run_trend_posts(db))
            refresh_engagement(db)
    except Exception:
        logger.exception("Trend posts failed (non-fatal)")
    finally:
        db.close()


async def _bill_refresh_pass() -> dict:
    """One hourly bill pass: the statuses (pipeline/bill_refresh.py), then
    the titles lists of bills moved past committee whose cached copy is
    missing or went stale as they moved (fetch/bill_display_titles.py), for
    the short title the bill list and page show. Here rather than in
    bill_refresh so the display titles stay outside the analysis-code
    fingerprint."""
    summary = await bill_refresh.refresh_bill_statuses()
    titles = await bill_display_titles.refresh_display_titles()
    summary["display_titles"] = titles
    if titles["fetched"]:
        warm_bill_collection_cache()
    return summary


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
            from app.pipeline.bill_refresh import bill_tracker

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
            # Quiet for eight hours, not merely running that long: a House
            # run on a cache warm-up night runs well past that while it
            # works, and rewrites the rows this refresh updates.
            if is_house_pipeline_running() and not house_pipeline_hung(timedelta(hours=8)):
                logger.info("Bill status refresh skipped — house pipeline is running")
                return

            # Under its tracker and lease (lease.run_tracked), so a pass in
            # this process, a reset or a refresh in another process sees it,
            # taken only past the checks above: a tick that bails holds
            # nothing. Cut off where they stop holding, never left running
            # beside the next: two passes at once would let the older one's
            # snapshot overwrite the newer one's rows.
            summary = lease.run_tracked(
                lease.BILL_REFRESH, bill_tracker(), _bill_refresh_pass, who="Bill status refresh",
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
    from app.election_calendar import is_election_season

    if not is_election_season():
        return

    def _run():
        from app.pipeline.analyze.election_coverage import (
            coverage_tracker,
        )

        if is_election_pipeline_running():
            if not election_pipeline_hung(timedelta(hours=2)):
                logger.info("Election coverage refresh skipped — election pipeline is running")
                return
            logger.warning(
                "Election pipeline has written no progress for %dh — treating as hung "
                "and proceeding with coverage refresh anyway", 2,
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
                # Election night: the live count's own posts
                # (live_results/bluesky.py) have the account while any
                # race's totals are still moving.
                from app.live_results.bluesky import counting_is_live

                posted = 0 if counting_is_live(db) else post_race_coverage_updates(db, deadline=deadline)
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
    Supplementary -> House -> Stock -> Election): it starts only once the
    four before it are done, hours into the night, and ballots would go
    most of a day between refreshes in exactly the weeks voters are
    reading them. This runs only the ballot step
    (run_ballot_sync) on its own clock, so a withdrawal or replacement
    reaches the page within hours. It reads state election offices'
    published lists only — a few requests per state at one per second — so
    the cadence is cheap; the roster and financial refresh stay nightly.
    A no-op outside is_election_season's window, like the coverage refresh.
    """
    from app.election_calendar import is_election_season

    if not is_election_season():
        return

    def _run():
        if is_election_pipeline_running():
            if not election_pipeline_hung(timedelta(hours=6)):
                logger.info("Ballot sync skipped — the nightly election pipeline is running")
                return
            logger.warning(
                "Election pipeline has written no progress for %dh — treating as hung "
                "and proceeding with the ballot sync anyway", 6,
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
            if result.get("status") == "skipped":
                logger.info("Election-season ballot sync skipped: %s", result["reason"])
                return
            logger.info(
                "Election-season ballot sync: %d confirmed, %d states ok, failed: %s",
                result["confirmed"], len(result["statesOk"]), result["statesFailed"] or "none",
            )
        except Exception:
            logger.exception("Election-season ballot sync failed")

    _start_job(_run, name="election-ballot-sync")


def _election_results_sync() -> None:
    """Every covered state's live count, from election day until the
    results window closes (election_phase), and each special election's,
    from its own day until its window closes (live_results/specials.py) --
    a no-op the rest of the year.

    Every five minutes while a count is moving; once no race's totals
    have changed for a day, hourly, since the page then only waits out its
    grace period and a late amendment is all there is left to catch.
    """
    from app.election_phase import active_election
    from app.live_results.specials import due_specials

    election = active_election()
    general = election.shows_results
    last = election.last_result_change
    if general and last is not None and utcnow() - last > timedelta(days=1) and utcnow().minute >= 5:
        general = False
    db = SessionLocal()
    try:
        specials = [row.id for row in due_specials(db)]
    finally:
        db.close()
    if not general and not specials:
        return

    def _run():
        from app.election_archive import freeze
        from app.http_client import make_async_client
        from app.live_results.specials import sync_specials
        from app.live_results.sync import results_tracker, sync_live_results
        from app.models import FederalElectionDate

        async def _sync():
            db = SessionLocal()
            try:
                summary = {}
                async with make_async_client() as client:
                    if general:
                        summary = await sync_live_results(db, client, election.election_day)
                    if specials:
                        rows = db.query(FederalElectionDate).filter(FederalElectionDate.id.in_(specials)).all()
                        summary["_specials"] = await sync_specials(db, client, rows)
                if general:
                    # The election's archive (app/election_archive.py), copied
                    # on every pass through the window and frozen by its close.
                    try:
                        freeze(db, election.election_day)
                    except Exception:
                        db.rollback()
                        logger.exception("Election archive copy failed")
                return summary
            finally:
                db.close()

        try:
            summary = lease.run_tracked(lease.RESULTS_SYNC, results_tracker(), _sync, who="Live results sync")
            if summary:
                logger.info("Live results sync: %s", summary)
        except Exception:
            logger.exception("Live results sync failed")

    _start_job(_run, name="election-results-sync")


def _election_results_readiness() -> None:
    """Once a day in the two weeks before election day, whether every
    covered state's live-results feed can still be read
    (live_results/readiness.py) — a no-op the rest of the year."""
    from app.election_phase import active_election, election_today
    from app.live_results.readiness import readiness_due

    election = active_election()
    today = election_today()
    if election.shows_results or not readiness_due(election.election_day, today):
        return

    def _run():
        from app.http_client import make_async_client
        from app.live_results.readiness import check_feed_readiness
        from app.live_results.sync import results_tracker

        async def _check():
            db = SessionLocal()
            try:
                async with make_async_client() as client:
                    return await check_feed_readiness(db, client, election.election_day, today)
            finally:
                db.close()

        # Under the live sync's tracker and lease: the sync is a no-op until
        # election day, and a data reset sees this read as it would the sync.
        try:
            problems = lease.run_tracked(lease.RESULTS_SYNC, results_tracker(), _check, who="Live results readiness")
            if problems is not None:  # None: refused or cut off, logged by run_tracked
                logger.info("Live results readiness: %s", problems or "every covered state ready")
        except Exception:
            logger.exception("Live results readiness check failed")

    _start_job(_run, name="election-results-readiness")


def _data_refresh() -> None:
    """The generated data files (app/data_refresh.py): each regenerated by
    its own script on its own cadence, gated, the last good copy kept on a
    failure. Daily; the files that load the embedding model or are
    CPU-bound wait while a pipeline runs."""
    def _run():
        with lease.job(lease.DATA_REFRESH, who="Data file refresh") as held:
            if not held:
                return
            outcomes = data_refresh.run_refreshes(pipeline_busy=pipelines_running())
            logger.info("Data file refresh: %s", outcomes)

    _start_job(_run, name="data-refresh")


async def _fill_amendment_purposes() -> dict:
    db = SessionLocal()
    try:
        async with make_async_client() as client:
            return await fill_amendment_purposes(client, db)
    finally:
        db.close()


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
            # What each amendment voted on would do, from the record
            # (fetch/amendment_purpose.py): after the sync, so a vote stored
            # this run is read this run.
            try:
                logger.info("Amendment purposes: %s", loop.run_until_complete(_fill_amendment_purposes()))
            except Exception:
                logger.exception("Reading amendment purposes failed")
            finally:
                loop.close()
            # The day's Bluesky post, once its Digest has made it final.
            # After the sync, so a day finalized this run posts this run.
            db = SessionLocal()
            try:
                posted = post_daily_congress(db, eastern_today())
                if posted:
                    logger.info("Published the Congress day %s", posted)
                week = post_weekly_congress(db, eastern_today())
                if week:
                    logger.info("Published the Congress week of %s", week)
                ahead = post_congress_lookahead(db, eastern_today())
                if ahead:
                    logger.info("Published the Congress look-ahead for the week of %s", ahead)
            except Exception:
                logger.exception("Congress Bluesky post failed")
                db.rollback()
            # Posts held for the posting window go out when it opens
            # (broadcast.deliver_pending): from here every half hour as well
            # as from the hourly Action Center run, so the window's opening
            # waits on neither.
            try:
                sent = deliver_pending(db)
                if sent:
                    logger.info("Sent %d held or retried Bluesky post(s)", sent)
            except Exception:
                logger.exception("Sending held Bluesky posts failed")
            finally:
                db.close()

    _start_job(_run, name="congress-activity-sync")


def _agency_sync() -> None:
    """The agency list and budgets (fetch/agencies.py): tried hourly, each
    source read only once a week has passed since it last stored, so a
    source that failed is retried within the hour. Outside the nightly
    chain: nothing the chain scores reads it."""
    _start_job(run_agency_sync, name="agency-sync")


def _nominations_sync() -> None:
    """Congress.gov's nominations (pipeline/fetch/nominations.py): daily,
    outside the nightly chain, and skipped while any of its pipelines runs —
    both read Congress.gov through one rate limiter, and the chain's need is
    the greater."""
    def _run():
        try:
            if pipelines_running():
                logger.info("Nominations sync skipped — a nightly pipeline is running")
                return

            async def work():
                db = SessionLocal()
                try:
                    summary = await nominations.sync_nominations(db)
                    # The Congress before, until its last actions are in
                    # (nominations.sync_ended_congress).
                    ended = await nominations.sync_ended_congress(db)
                    return {**summary, "endedCongress": ended} if ended else summary
                finally:
                    db.close()

            summary = lease.run_tracked(
                lease.NOMINATIONS_SYNC, nominations.nominations_tracker(), work, who="Nominations sync",
            )
            if summary is not None:
                logger.info("Nominations sync: %s", summary)
        except Exception:
            logger.exception("Nominations sync failed")

    _start_job(_run, name="nominations-sync")


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

    # Live election results — every five minutes, a no-op outside the
    # results window (checked inside the job, like the season jobs above).
    scheduler.add_job(
        _election_results_sync,
        CronTrigger(minute="*/5", timezone="UTC"),
        id="election_results_sync",
        replace_existing=True,
    )

    # Whether every covered state's results feed still reads — daily in the
    # two weeks before election day, a no-op otherwise (checked inside).
    scheduler.add_job(
        _election_results_readiness,
        CronTrigger(hour="14", minute="20", timezone="UTC"),
        id="election_results_readiness",
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

    # The election map's data (app/forecast/build.py) — every three
    # hours at :55, and once at start, a no-op outside the campaign. Not
    # through _start_job: it reads the database and writes only its own
    # file on the data volume, so it is no database writer for a data
    # reset to wait on, and a reset under it only shortens one build.
    scheduler.add_job(
        election_map_refresh,
        CronTrigger(hour="*/3", minute="55", timezone="UTC"),
        id="election_map_refresh",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),
    )

    # Agency list and budgets — hourly at :30, clear of the :10/:40 Congress
    # sync, the :15 action refresh and the :25 dead-run tidy; a no-op unless
    # a source is a week old or was never read (checked inside). And once at
    # start, so a deploy that brings the agency pages, or a fresh database,
    # fills them as the pipeline process comes up rather than at the next :30.
    scheduler.add_job(
        _agency_sync,
        CronTrigger(minute="30", timezone="UTC"),
        id="agency_sync",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),
    )

    # Generated data files — daily at 15:40, clear of the :45 bill refresh
    # and after the nightly chain has usually finished (the heavy ones wait
    # for it anyway).
    scheduler.add_job(
        _data_refresh,
        CronTrigger(hour="15", minute="40", timezone="UTC"),
        id="data_refresh",
        replace_existing=True,
    )

    # Nominations — daily at 14:50, after the nightly chain's usual end and
    # clear of the :10/:40 congress sync, :15 action refresh, :30 agency
    # sync and :45 bills.
    scheduler.add_job(
        _nominations_sync,
        CronTrigger(hour="14", minute="50", timezone="UTC"),
        id="nominations_sync",
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

    # Not through _start_job: a timestamp write isn't a data writer, and
    # registering one would make an admin data reset refuse while it runs
    # (and skip it for the length of a reset). APScheduler runs a plain
    # function on a worker thread; the first beat goes out at once.

    scheduler.add_job(
        _site_search_refresh,
        CronTrigger(minute="*/10"),
        id="site_search_refresh",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),
    )

    scheduler.add_job(
        _heartbeat,
        CronTrigger(minute=f"*/{_HEARTBEAT_MINUTES}"),
        id="scheduler_heartbeat",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),
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


# Where the scheduler's process records its next nightly run, for a process
# that doesn't run the scheduler (the read-only API, PROCESS_ROLE=api) to
# report: rewritten every _HEARTBEAT_MINUTES, and reported only while fresh,
# so a pipeline service that is down stops advertising a run that won't
# happen. A file on the data volume both services mount, not a database
# row: a beat must not depend on the database's write lock, which a long
# pipeline transaction can hold past the busy timeout — a heartbeat that
# can't be written reads as a service that is gone (the liveness alert,
# ops_alerts.check_pipeline_service_alive).
_HEARTBEAT_MINUTES = 5
_HEARTBEAT_STALE = timedelta(minutes=3 * _HEARTBEAT_MINUTES)


def heartbeat_path() -> str:
    from app.shared_state import record_path

    return record_path("scheduler_heartbeat.json")


def read_heartbeat():
    """(when the scheduler last beat, what it recorded), None when it never
    has, or shared_state.UNREADABLE (shared_state.read_record)."""
    from app.shared_state import read_record

    return read_record(heartbeat_path())


def _live_next_run() -> str | None:
    job = scheduler.get_job("pipeline_run")
    if job is None or job.next_run_time is None:
        return None  # not scheduled, or paused
    # The trigger's next fire from now, not next_run_time alone: the
    # scheduler advances that only after submitting the run, so a beat
    # landing in the same moment as the run (both fire on the hour) would
    # record the time that has just passed — and the API process would
    # report it until the next beat.
    upcoming = job.trigger.get_next_fire_time(None, datetime.now(job.next_run_time.tzinfo))
    return max(job.next_run_time, upcoming).isoformat() if upcoming else job.next_run_time.isoformat()


def _record_next_run() -> None:
    from app.shared_state import write_record

    write_record(heartbeat_path(), {"nextRun": _live_next_run()})


def _site_search_refresh() -> None:
    """Site search's entity index (app/site_search_index.py), rebuilt when
    its sources moved: checked every ten minutes and at start. Not a data
    writer (it reads the database and writes its own file, renamed into
    place), so not registered through _start_job: a data reset neither
    waits on it nor skips it, and the next tick after one rebuilds."""
    db = SessionLocal()
    try:
        site_search_index.refresh_index(db)
    except Exception:
        logger.exception("Site search index refresh failed")
    finally:
        db.close()


def _heartbeat() -> None:
    try:
        _record_next_run()
    except Exception:
        logger.warning("Scheduler heartbeat not recorded", exc_info=True)


def get_next_run_time() -> str | None:
    """Return the next scheduled run time as an ISO string, or None.

    Read off the live job where this process runs the scheduler; elsewhere
    (the read-only API process) from the heartbeat the scheduler's process
    keeps, and None once that goes stale — the pipeline service is down.
    """
    if scheduler.running:
        return _live_next_run()
    row = read_heartbeat()
    if not isinstance(row, tuple) or row[0] < utcnow() - _HEARTBEAT_STALE:
        return None
    value = row[1]
    return value.get("nextRun") if isinstance(value, dict) else None
