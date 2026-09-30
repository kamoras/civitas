"""Tests for the hourly action-center refresh's self-overlap guard.

_hourly_action_refresh fires a background thread on every cron tick with
no built-in protection against the previous refresh still running — a
slow/degraded local LLM can make one cycle run long enough to still be
active when the next tick fires (confirmed live 2026-07-13: this let
multiple refreshes pile up competing for the same LLM, worsening the
slowdown). These tests run the spawned thread synchronously (patching
threading.Thread) so the guard's decision can be asserted directly.
"""

from datetime import timedelta
from app.time_utils import utcnow
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import scheduler as scheduler_module
from app.pipeline.analyze.election_coverage import coverage_tracker
from app.pipeline.election_pipeline import ballot_tracker


@pytest.fixture(autouse=True)
def _job_leases_granted():
    """These tests stub the database, which a lease lives in; the leases
    themselves are tested in test_database_reset.TestLease."""
    @contextmanager
    def granted(_tier, **_kw):
        from app.pipeline.lease import Granted

        yield Granted(None)

    with patch("app.pipeline.lease.job", granted):
        yield


@contextmanager
def _tracker_running(tracker, running: bool, age):
    """Put a real PipelineRunTracker in the state a test describes: a run
    going for `age`."""
    import time

    token = tracker.start() if running else None
    if token is not None and age is not None:
        tracker._started_at = time.time() - age.total_seconds()
    try:
        yield
    finally:
        tracker.stop(token)


class _SyncThread:
    """Drop-in for threading.Thread that runs the target immediately."""

    def __init__(self, target, daemon=None, name=None):
        self._target = target

    def start(self):
        self._target()


def _run_hourly_refresh(
    refresh_state: dict, house_running: bool = False, stock_running: bool = False, stock_age=None,
    supplementary_running: bool = False, supplementary_age=None,
):
    from app import scheduler

    with patch("app.background.threading.Thread", _SyncThread), \
         patch("app.scheduler.get_action_refresh_state", return_value=refresh_state), \
         patch("app.database.SessionLocal") as mock_session_local, \
         patch("app.scheduler.refresh_action_issues") as mock_refresh:
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_session_local.return_value = mock_db

        with patch("app.scheduler.is_house_pipeline_running", return_value=house_running), \
             patch("app.scheduler.is_stock_pipeline_running", return_value=stock_running), \
             patch("app.scheduler.stock_pipeline_age", return_value=stock_age), \
             patch("app.scheduler.is_supplementary_pipeline_running", return_value=supplementary_running), \
             patch("app.scheduler.supplementary_pipeline_age", return_value=supplementary_age):
            scheduler._hourly_action_refresh()

    return mock_refresh


class TestIsStale:
    def test_none_age_is_never_stale(self):
        from app.scheduler import _is_stale
        assert _is_stale(None, timedelta(hours=1)) is False

    def test_age_under_threshold_is_not_stale(self):
        from app.scheduler import _is_stale
        assert _is_stale(timedelta(hours=1), timedelta(hours=2)) is False

    def test_age_over_threshold_is_stale(self):
        from app.scheduler import _is_stale
        assert _is_stale(timedelta(hours=3), timedelta(hours=2)) is True

    def test_age_exactly_at_threshold_is_not_stale(self):
        from app.scheduler import _is_stale
        assert _is_stale(timedelta(hours=2), timedelta(hours=2)) is False


class TestActionRefreshOverlapGuard:
    def test_skips_when_a_recent_refresh_is_still_running(self):
        state = {"is_running": True, "started_at": utcnow() - timedelta(minutes=10)}
        mock_refresh = _run_hourly_refresh(state)
        mock_refresh.assert_not_called()

    def test_proceeds_when_no_refresh_is_running(self):
        state = {"is_running": False, "started_at": None}
        mock_refresh = _run_hourly_refresh(state)
        mock_refresh.assert_called_once()

    def test_proceeds_when_running_flag_is_stale_beyond_4_hours(self):
        # A refresh "running" for 5h is wedged, not just slow (normal is
        # minutes; worst case with a degraded LLM is ~1-2h post-reorder) —
        # without this override a genuinely hung thread would block every
        # future hourly run until the container restarts.
        state = {"is_running": True, "started_at": utcnow() - timedelta(hours=5)}
        mock_refresh = _run_hourly_refresh(state)
        mock_refresh.assert_called_once()

    def test_does_not_skip_just_under_the_hung_edge(self):
        # Just under the edge — where the refresh's lease stops being
        # renewed (lease.max_hold), so the two agree — still running.
        from app.pipeline import lease

        edge = lease.max_hold(lease.ACTION_REFRESH)
        state = {"is_running": True, "started_at": utcnow() - edge + timedelta(minutes=1)}
        mock_refresh = _run_hourly_refresh(state)
        mock_refresh.assert_not_called()


class TestStockTradesOverlapGuard:
    """Stock trades runs sequentially after House within the same nightly
    thread, so by the time it starts, House's own running flag is already
    cleared — the pre-existing Senate/House guards can't see it. Without
    this guard the hourly refresh could run concurrently with stock trades,
    the same SQLite-write-conflict risk the House guard exists to prevent."""

    def test_skips_when_stock_pipeline_is_running_and_recent(self):
        state = {"is_running": False, "started_at": None}
        mock_refresh = _run_hourly_refresh(state, stock_running=True, stock_age=timedelta(minutes=30))
        mock_refresh.assert_not_called()

    def test_proceeds_when_stock_pipeline_running_flag_is_stale_beyond_2_hours(self):
        state = {"is_running": False, "started_at": None}
        mock_refresh = _run_hourly_refresh(state, stock_running=True, stock_age=timedelta(hours=3))
        mock_refresh.assert_called_once()

    def test_proceeds_when_stock_pipeline_is_not_running(self):
        state = {"is_running": False, "started_at": None}
        mock_refresh = _run_hourly_refresh(state, stock_running=False)
        mock_refresh.assert_called_once()


class TestSupplementaryOverlapGuard:
    """Explore docs/SCOTUS/presidents now run as their own pipeline
    (extracted from Senate's run_senate_pipeline — see
    supplementary_pipeline.py) between Senate and House in the nightly
    sequence, so the hourly refresh needs its own guard the same way
    House and stock trades already have one."""

    def test_skips_when_supplementary_pipeline_is_running_and_recent(self):
        state = {"is_running": False, "started_at": None}
        mock_refresh = _run_hourly_refresh(
            state, supplementary_running=True, supplementary_age=timedelta(hours=1),
        )
        mock_refresh.assert_not_called()

    def test_proceeds_when_supplementary_pipeline_running_flag_is_stale_beyond_8_hours(self):
        # 8h, not stock's 2h: a weekly SCOTUS refresh includes the uncached
        # per-case Oyez crawl, which took 5h+ in run 69 — a legitimately
        # slow run must not be misdiagnosed as hung.
        state = {"is_running": False, "started_at": None}
        mock_refresh = _run_hourly_refresh(
            state, supplementary_running=True, supplementary_age=timedelta(hours=9),
        )
        mock_refresh.assert_called_once()

    def test_proceeds_when_supplementary_pipeline_is_not_running(self):
        state = {"is_running": False, "started_at": None}
        mock_refresh = _run_hourly_refresh(state, supplementary_running=False)
        mock_refresh.assert_called_once()



async def _passthrough(run_house):
    """run_house_on_sitting_lines without its lease and lines check."""
    return await run_house()


class TestNightlyPipelineCascadingSkip:
    """_nightly_pipeline's chain (Senate -> Supplementary -> House ->
    Stock) runs each step only if the previous one didn't report
    "skipped" — until 2026-07-23 only Senate's skip was even checked,
    and even that alert never mentioned the rest of the chain also
    silently not running that night. Confirmed live as the likely root
    cause of stock-trades data going stale 4+ days and supplementary
    data 1+ day, since a Senate (or, after this fix, any step's) skip
    took the whole rest of the chain with it with no visible signal.
    """

    def _run_chain(
        self, senate_result, supplementary_result=None, house_result=None,
        stock_result=None, election_result=None,
    ):
        from app import scheduler

        with patch("app.background.threading.Thread", _SyncThread), \
             patch("app.scheduler.run_senate_pipeline", new_callable=AsyncMock) as mock_senate, \
             patch("app.scheduler.run_supplementary_pipeline", new_callable=AsyncMock) as mock_supp, \
             patch("app.scheduler.run_house_pipeline", new_callable=AsyncMock) as mock_house, \
             patch("app.scheduler.run_stock_trades_pipeline", new_callable=AsyncMock) as mock_stock, \
             patch("app.scheduler.run_election_pipeline", new_callable=AsyncMock) as mock_election, \
             patch("app.ops_alerts.send_ops_alert") as mock_alert, \
             patch("app.ops_alerts.check_current_congress_staleness"), \
             patch("app.pipeline.fetch.district_pvi.run_house_on_sitting_lines", _passthrough), \
             patch("app.services.bill_service.warm_bill_collection_cache"):
            mock_senate.return_value = senate_result
            mock_supp.return_value = supplementary_result or {"status": "completed"}
            mock_house.return_value = house_result or {"status": "completed"}
            mock_stock.return_value = stock_result or {"status": "completed"}
            mock_election.return_value = election_result or {"status": "completed"}

            scheduler._nightly_pipeline()

            return mock_senate, mock_supp, mock_house, mock_stock, mock_election, mock_alert

    def test_the_house_run_goes_through_the_sitting_lines_check(self):
        """The nightly House run settles the district lines under the lease
        it holds for the run (fetch/district_pvi.run_house_on_sitting_lines)
        — after the Senate and Supplementary runs, which don't score on
        them — and a skip from it is alerted like any House skip."""
        from app import scheduler

        order = []

        async def wrapper(run_house):
            order.append("pvi")
            return await run_house()

        with patch("app.background.threading.Thread", _SyncThread), \
             patch("app.scheduler.run_senate_pipeline", new_callable=AsyncMock,
                   side_effect=lambda: order.append("senate") or {"status": "completed"}), \
             patch("app.scheduler.run_supplementary_pipeline", new_callable=AsyncMock,
                   side_effect=lambda: order.append("supplementary") or {"status": "completed"}), \
             patch("app.scheduler.run_house_pipeline", new_callable=AsyncMock,
                   side_effect=lambda: order.append("house") or {"status": "skipped", "reason": "held_elsewhere"}), \
             patch("app.ops_alerts.send_ops_alert") as alert, \
             patch("app.ops_alerts.check_current_congress_staleness"), \
             patch("app.ops_alerts.check_feedback_token_expiration"), \
             patch("app.ops_alerts.check_state_pvi_staleness"), \
             patch("app.pipeline.fetch.district_pvi.run_house_on_sitting_lines", wrapper):
            scheduler._nightly_pipeline()
        assert order == ["senate", "supplementary", "pvi", "house"]
        assert "Nightly House run skipped" in alert.call_args.args[0]

    def test_all_five_run_when_nothing_skips(self):
        senate, supp, house, stock, election, alert = self._run_chain({"status": "completed"})
        senate.assert_called_once()
        supp.assert_called_once()
        house.assert_called_once()
        stock.assert_called_once()
        election.assert_called_once()
        alert.assert_not_called()

    def test_senate_skip_stops_the_whole_chain(self):
        senate, supp, house, stock, election, alert = self._run_chain(
            {"status": "skipped", "reason": "already_running"},
        )
        senate.assert_called_once()
        supp.assert_not_called()
        house.assert_not_called()
        stock.assert_not_called()
        election.assert_not_called()
        alert.assert_called_once()
        subject, body = alert.call_args[0][0], alert.call_args[0][1]
        assert "Senate" in subject
        assert "Supplementary, House, Stock trades, Election did not run tonight either" in body

    def test_supplementary_skip_stops_house_and_stock_but_senate_already_ran(self):
        senate, supp, house, stock, election, alert = self._run_chain(
            {"status": "completed"},
            supplementary_result={"status": "skipped", "reason": "already_running"},
        )
        senate.assert_called_once()
        supp.assert_called_once()
        house.assert_not_called()
        stock.assert_not_called()
        election.assert_not_called()
        alert.assert_called_once()
        assert "Supplementary" in alert.call_args[0][0]

    def test_house_skip_stops_stock_but_earlier_steps_already_ran(self):
        senate, supp, house, stock, election, alert = self._run_chain(
            {"status": "completed"},
            house_result={"status": "skipped", "reason": "already_running"},
        )
        supp.assert_called_once()
        house.assert_called_once()
        stock.assert_not_called()
        election.assert_not_called()
        alert.assert_called_once()
        assert "House" in alert.call_args[0][0]

    def test_stock_skip_stops_election_but_earlier_steps_already_ran(self):
        senate, supp, house, stock, election, alert = self._run_chain(
            {"status": "completed"},
            stock_result={"status": "skipped", "reason": "already_running"},
        )
        stock.assert_called_once()
        election.assert_not_called()
        alert.assert_called_once()
        assert "Stock trades" in alert.call_args[0][0]

    def test_election_skip_alerts_with_nothing_left_to_stop(self):
        senate, supp, house, stock, election, alert = self._run_chain(
            {"status": "completed"},
            election_result={"status": "skipped", "reason": "already_running"},
        )
        election.assert_called_once()
        alert.assert_called_once()
        assert "Election" in alert.call_args[0][0]
        assert "did not run tonight either" not in alert.call_args[0][1]

    def test_a_house_skip_behind_a_stuck_district_pvi_refresh_does_not_end_the_chain(self):
        """run_house_on_sitting_lines waits for a refresh holding the lines;
        one that outlasts the wait costs the House scores, not Stock trades
        and Election, which don't read them. The alert names the refresh —
        not "another run of it" — and says the chain went on."""
        senate, supp, house, stock, election, alert = self._run_chain(
            {"status": "completed"},
            house_result={"status": "skipped", "reason": "held_elsewhere", "holder": "District PVI refresh"},
        )
        stock.assert_called_once()
        election.assert_called_once()
        alert.assert_called_once()
        subject, body = alert.call_args[0][0], alert.call_args[0][1]
        assert "House" in subject
        assert "District PVI refresh is already running" in body
        assert "another run of it" not in body
        assert "The rest of tonight's chain (Stock trades, Election) still runs" in body

    def test_a_house_skip_behind_another_house_run_names_it_and_ends_the_chain(self):
        senate, supp, house, stock, election, alert = self._run_chain(
            {"status": "completed"},
            house_result={"status": "skipped", "reason": "held_elsewhere", "holder": "House run"},
        )
        stock.assert_not_called()
        election.assert_not_called()
        body = alert.call_args[0][1]
        assert "House run is already running" in body and "another run of it" not in body


    def test_a_superseded_house_step_continues_the_chain_without_an_alert(self):
        """A chain that started before noon ET on Jan 3, whose House step
        finds a newer job already on the new Congress (district_pvi.
        _superseded): nothing is wrong — Stock trades and Election still
        run, no ops alert is sent, and a House-skip alert left from before
        is resolved."""
        from app.pipeline.run_tracker import SUPERSEDED

        with patch("app.ops_alerts.resolve_ops_alert") as resolved:
            senate, supp, house, stock, election, alert = self._run_chain(
                {"status": "completed"}, house_result={"status": "skipped", "reason": SUPERSEDED},
            )
        house.assert_called_once()
        stock.assert_called_once()
        election.assert_called_once()
        alert.assert_not_called()
        assert any(c.args == ("nightly-skipped-house",) for c in resolved.call_args_list)


class TestElectionCoverageRefresh:
    """The tighter-cadence election-season coverage refresh: a no-op
    outside is_election_season's window, and otherwise runs only the
    coverage-ingestion + posting phases (not the full nightly pipeline)."""

    def _run(
        self, in_season: bool, pipeline_running: bool = False, pipeline_age=None,
        coverage_running: bool = False, coverage_age=None,
    ):
        from app import scheduler

        with patch("app.background.threading.Thread", _SyncThread), \
             patch("app.election_calendar.is_election_season", return_value=in_season), \
             patch("app.scheduler.is_election_pipeline_running", return_value=pipeline_running), \
             patch("app.scheduler.election_pipeline_age", return_value=pipeline_age), \
             _tracker_running(coverage_tracker(), coverage_running, coverage_age), \
             patch("app.database.SessionLocal") as mock_session_local, \
             patch(
                 "app.pipeline.analyze.election_coverage.ingest_race_coverage",
                 new_callable=AsyncMock,
             ) as mock_ingest, \
             patch(
                 "app.pipeline.analyze.election_bluesky.post_race_coverage_updates",
             ) as mock_post:
            mock_ingest.return_value = 3
            mock_post.return_value = 1
            mock_session_local.return_value = MagicMock()

            scheduler._election_coverage_refresh()

        return mock_ingest, mock_post

    def test_noop_outside_election_season(self):
        ingest, post = self._run(in_season=False)
        ingest.assert_not_called()
        post.assert_not_called()

    def test_runs_coverage_and_posting_in_season(self):
        ingest, post = self._run(in_season=True)
        ingest.assert_called_once()
        post.assert_called_once()

    def test_skips_when_election_pipeline_is_running_and_recent(self):
        ingest, post = self._run(
            in_season=True, pipeline_running=True, pipeline_age=timedelta(minutes=20),
        )
        ingest.assert_not_called()
        post.assert_not_called()

    def test_proceeds_when_election_pipeline_running_flag_is_stale_beyond_2_hours(self):
        ingest, post = self._run(
            in_season=True, pipeline_running=True, pipeline_age=timedelta(hours=3),
        )
        ingest.assert_called_once()
        post.assert_called_once()

    def test_skips_when_previous_coverage_refresh_still_running(self):
        """Self-overlap guard (2026-07 review B3): the PREVIOUS 15-minute
        refresh still mid-flight means overlapping passes would
        double-ingest and double-post — skip while its tracker is fresh."""
        ingest, post = self._run(
            in_season=True, coverage_running=True, coverage_age=timedelta(minutes=20),
        )
        ingest.assert_not_called()
        post.assert_not_called()

    def test_proceeds_when_coverage_refresh_flag_is_stale_beyond_2_hours(self):
        # A crashed refresh can leave the in-process flag set forever —
        # the 2h stale override keeps one wedge from stopping all coverage.
        ingest, post = self._run(
            in_season=True, coverage_running=True, coverage_age=timedelta(hours=3),
        )
        ingest.assert_called_once()
        post.assert_called_once()


class TestElectionCoverageRefreshExceptionHandling:
    """The refresh's inner _run() must catch and log any failure, not let
    it escape and take the daemon thread down silently."""

    def test_ingestion_failure_is_caught_and_logged(self):
        from app import scheduler

        with patch("app.background.threading.Thread", _SyncThread), \
             patch("app.election_calendar.is_election_season", return_value=True), \
             patch("app.scheduler.is_election_pipeline_running", return_value=False), \
             patch("app.database.SessionLocal", return_value=MagicMock()), \
             patch(
                 "app.pipeline.analyze.election_coverage.ingest_race_coverage",
                 new_callable=AsyncMock,
                 side_effect=RuntimeError("boom"),
             ), \
             patch("app.scheduler.logger") as mock_logger:
            scheduler._election_coverage_refresh()  # must not raise

        mock_logger.exception.assert_called_once()


class TestElectionBallotSync:
    """The election-season ballot sync: every state's ballot list on its own
    6-hour clock, so a failure earlier in the nightly chain can't hold
    ballots back. A no-op outside the season; never overlaps the nightly
    election run or itself."""

    def _run(self, in_season=True, pipeline_running=False, pipeline_age=None,
             sync_running=False, sync_age=None, result=None, error=None):
        sync = AsyncMock(return_value=result or {
            "status": "ok", "confirmed": 5, "statesOk": ["AK"], "statesFailed": [], "filings": {},
        }, side_effect=error)
        with patch("app.background.threading.Thread", _SyncThread), \
             patch("app.election_calendar.is_election_season", return_value=in_season), \
             patch("app.scheduler.is_election_pipeline_running", return_value=pipeline_running), \
             patch("app.scheduler.election_pipeline_age", return_value=pipeline_age), \
             _tracker_running(ballot_tracker(), sync_running, sync_age), \
             patch("app.scheduler.run_ballot_sync", sync), \
             patch("app.scheduler.logger") as mock_logger:
            scheduler_module._election_ballot_sync()
        return sync, mock_logger

    def test_noop_outside_election_season(self):
        sync, _ = self._run(in_season=False)
        sync.assert_not_called()

    def test_runs_in_season(self):
        sync, _ = self._run()
        sync.assert_called_once()

    def test_steps_aside_for_the_nightly_election_run(self):
        sync, _ = self._run(pipeline_running=True, pipeline_age=timedelta(minutes=30))
        sync.assert_not_called()

    def test_proceeds_past_a_hung_nightly_run(self):
        sync, _ = self._run(pipeline_running=True, pipeline_age=timedelta(hours=7))
        sync.assert_called_once()

    def test_never_overlaps_itself(self):
        sync, _ = self._run(sync_running=True, sync_age=timedelta(minutes=10))
        sync.assert_not_called()

    def test_a_failure_is_logged_not_raised(self):
        _, mock_logger = self._run(error=RuntimeError("boom"))
        mock_logger.exception.assert_called_once()

    def test_the_tracker_is_released_after_a_run(self):
        self._run(error=RuntimeError("boom"))
        assert ballot_tracker().is_running is False


def test_a_nightly_chain_refused_by_a_data_reset_alerts():
    """A reset holding the database refuses the chain; the skip leaves the
    wiped database unbuilt for a day, so it is never silent."""
    from app import scheduler
    from app.background import exclusive

    ran = []
    with exclusive("test-reset"), patch("app.ops_alerts.send_ops_alert") as alert:
        scheduler._start_job(lambda: ran.append(1), name="nightly-pipeline", alert=True)
        scheduler._start_job(lambda: ran.append(1), name="action-refresh")
    assert ran == []
    assert alert.call_count == 1 and "data reset" in alert.call_args.args[0]


def _refused_leases(taken):
    @contextmanager
    def refused(tier, **_kw):
        from app.pipeline import lease

        taken.append(tier)
        yield lease.Granted("a data reset is running")

    return refused


class TestLeasedJobs:
    """A lockless job holds a lease so a reset in another process sees it;
    refused one (a reset running, or the job running elsewhere), it skips.
    It takes the lease only past its own checks: a tick that bails must hold
    nothing, or the nightly election pipeline's step reaching the same lease
    at that moment would skip (lease.tracked_job)."""

    def _patches(self, taken, *, pipeline_running=False):
        from contextlib import ExitStack

        stack = ExitStack()
        for target, kw in [
            ("app.background.threading.Thread", {"new": _SyncThread}),
            ("app.pipeline.lease.job", {"new": _refused_leases(taken)}),
            ("app.election_calendar.is_election_season", {"return_value": True}),
            ("app.scheduler.is_election_pipeline_running", {"return_value": pipeline_running}),
            ("app.scheduler.election_pipeline_age", {"return_value": timedelta(minutes=5)}),
            ("app.scheduler.is_house_pipeline_running", {"return_value": False}),
            ("app.database.SessionLocal", {"return_value": MagicMock(
                **{"query.return_value.filter.return_value.first.return_value": None},
            )}),
        ]:
            stack.enter_context(patch(target, **kw))
        return stack

    def test_bill_refresh_skips_without_its_lease(self):
        from app import scheduler
        from app.pipeline import lease

        taken = []
        refresh = AsyncMock()
        with self._patches(taken), patch("app.pipeline.bill_refresh.refresh_bill_statuses", refresh):
            scheduler._hourly_bill_status_refresh()
        assert taken == [lease.BILL_REFRESH]
        refresh.assert_not_called()

    def test_coverage_refresh_skips_without_its_lease(self):
        from app import scheduler
        from app.pipeline import lease

        taken = []
        ingest = AsyncMock()
        with self._patches(taken), \
             patch("app.pipeline.analyze.election_coverage.ingest_race_coverage", ingest):
            scheduler._election_coverage_refresh()
        assert taken == [lease.COVERAGE_REFRESH]
        ingest.assert_not_called()
        assert not coverage_tracker().is_running

    def test_ballot_sync_skips_without_its_lease(self):
        from app import scheduler
        from app.pipeline import lease

        taken = []
        sync = AsyncMock()
        with self._patches(taken), patch("app.scheduler.run_ballot_sync", sync):
            scheduler._election_ballot_sync()
        assert taken == [lease.BALLOT_SYNC]
        sync.assert_not_called()
        assert not ballot_tracker().is_running

    def test_a_tick_that_steps_aside_takes_no_lease(self):
        from app import scheduler

        taken = []
        with self._patches(taken, pipeline_running=True), \
             patch("app.scheduler.run_ballot_sync", AsyncMock()), \
             patch("app.pipeline.analyze.election_coverage.ingest_race_coverage", AsyncMock()):
            scheduler._election_coverage_refresh()
            scheduler._election_ballot_sync()
        assert taken == []


@pytest.mark.parametrize("reason, cause", [
    ("data_reset", "data reset"),
    ("busy", "locked by another writer"),
])
def test_a_skipped_nightly_run_alert_names_what_held_it_off(reason, cause):
    from app import scheduler

    with patch("app.scheduler.run_senate_pipeline", new_callable=AsyncMock,
               return_value={"status": "skipped", "reason": reason}), \
         patch("app.background.threading.Thread", _SyncThread), \
         patch("app.ops_alerts.send_ops_alert") as alert, \
         patch("app.ops_alerts.check_current_congress_staleness"), \
         patch("app.ops_alerts.check_feedback_token_expiration"), \
         patch("app.ops_alerts.check_state_pvi_staleness"):
        scheduler._nightly_pipeline()
    assert cause in alert.call_args.args[1]


def test_a_hung_bill_refresh_is_cut_off_inside_its_lease(monkeypatch):
    """Never run beside the next pass (its older snapshot would overwrite the
    newer one's rows): cut off while its lease still holds."""
    import asyncio
    from datetime import timedelta

    from app import scheduler
    from app.pipeline import lease

    monkeypatch.setitem(lease.HUNG_AFTER, lease.BILL_REFRESH, lease.stale_after(lease.BILL_REFRESH) + timedelta(seconds=0.05))
    cancelled = []

    async def hangs():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    with patch("app.background.threading.Thread", _SyncThread), \
         patch("app.pipeline.bill_refresh.refresh_bill_statuses", hangs), \
         patch("app.database.SessionLocal", return_value=MagicMock(**{"query.return_value.filter.return_value.first.return_value": None})), \
         patch("app.scheduler.is_house_pipeline_running", return_value=False):
        scheduler._hourly_bill_status_refresh()
    assert cancelled == [True]


@pytest.mark.parametrize("job, tier, target", [
    ("_election_ballot_sync", "BALLOT_SYNC", "app.scheduler.run_ballot_sync"),
    ("_election_coverage_refresh", "COVERAGE_REFRESH", "app.pipeline.analyze.election_coverage.ingest_race_coverage"),
])
def test_an_election_season_job_is_cut_off_where_its_guards_stop_holding(monkeypatch, job, tier, target):
    """Past max_hold its tracker and lease give way to the next pass; the
    job must not still be running beside it."""
    import asyncio

    from app import scheduler
    from app.pipeline import lease

    tier = getattr(lease, tier)
    monkeypatch.setitem(lease.HUNG_AFTER, tier, lease.stale_after(tier) + timedelta(seconds=0.05))
    cancelled = []

    async def hangs(*_args, **_kw):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    with patch("app.background.threading.Thread", _SyncThread), \
         patch("app.election_calendar.is_election_season", return_value=True), \
         patch("app.scheduler.is_election_pipeline_running", return_value=False), \
         patch("app.database.SessionLocal", return_value=MagicMock()), \
         patch(target, hangs):
        getattr(scheduler, job)()
    assert cancelled == [True]


@pytest.mark.parametrize("beat_ago, waits", [(None, True), (timedelta(hours=2), False)])
def test_the_bill_refresh_waits_for_a_senate_run_only_while_it_may_be_live(db_session, beat_ago, waits):
    """Through run_tracker.live_run: a young RUNNING row gets the benefit of
    the doubt; one its lease proves dead is proceeded past."""
    from app import scheduler
    from app.pipeline import lease

    class _Session:
        def __getattr__(self, name):
            return getattr(db_session, name)

        def close(self):
            pass

    from tests.conftest import start_senate_run_then_stop_beating

    start_senate_run_then_stop_beating(db_session, beat_ago=beat_ago)
    refresh = AsyncMock(return_value={})

    @contextmanager
    def granted(_tier, **_kw):
        yield lease.Granted(None)

    with patch("app.background.threading.Thread", _SyncThread), \
         patch("app.database.SessionLocal", lambda: _Session()), \
         patch("app.scheduler.is_house_pipeline_running", return_value=False), \
         patch("app.pipeline.lease.job", granted), \
         patch("app.pipeline.bill_refresh.refresh_bill_statuses", refresh):
        scheduler._hourly_bill_status_refresh()
    assert refresh.called is not waits
