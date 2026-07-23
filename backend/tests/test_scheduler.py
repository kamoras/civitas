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
from unittest.mock import AsyncMock, MagicMock, patch


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

    with patch("app.scheduler.threading.Thread", _SyncThread), \
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

    def test_does_not_skip_at_exactly_the_4_hour_boundary_edge(self):
        # Just under 4h: still treated as a legitimately running refresh.
        state = {"is_running": True, "started_at": utcnow() - timedelta(hours=3, minutes=59)}
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
        self, senate_result, supplementary_result=None, house_result=None, stock_result=None,
    ):
        from app import scheduler

        with patch("app.scheduler.threading.Thread", _SyncThread), \
             patch("app.scheduler.run_senate_pipeline", new_callable=AsyncMock) as mock_senate, \
             patch("app.scheduler.run_supplementary_pipeline", new_callable=AsyncMock) as mock_supp, \
             patch("app.scheduler.run_house_pipeline", new_callable=AsyncMock) as mock_house, \
             patch("app.scheduler.run_stock_trades_pipeline", new_callable=AsyncMock) as mock_stock, \
             patch("app.ops_alerts.send_ops_alert") as mock_alert, \
             patch("app.ops_alerts.check_current_congress_staleness"), \
             patch("app.services.bill_service.warm_bill_collection_cache"):
            mock_senate.return_value = senate_result
            mock_supp.return_value = supplementary_result or {"status": "completed"}
            mock_house.return_value = house_result or {"status": "completed"}
            mock_stock.return_value = stock_result or {"status": "completed"}

            scheduler._nightly_pipeline()

            return mock_senate, mock_supp, mock_house, mock_stock, mock_alert

    def test_all_four_run_when_nothing_skips(self):
        senate, supp, house, stock, alert = self._run_chain({"status": "completed"})
        senate.assert_called_once()
        supp.assert_called_once()
        house.assert_called_once()
        stock.assert_called_once()
        alert.assert_not_called()

    def test_senate_skip_stops_the_whole_chain(self):
        senate, supp, house, stock, alert = self._run_chain(
            {"status": "skipped", "reason": "already_running"},
        )
        senate.assert_called_once()
        supp.assert_not_called()
        house.assert_not_called()
        stock.assert_not_called()
        alert.assert_called_once()
        subject, body = alert.call_args[0][0], alert.call_args[0][1]
        assert "Senate" in subject
        assert "Supplementary/House/Stock never ran either" in body

    def test_supplementary_skip_stops_house_and_stock_but_senate_already_ran(self):
        senate, supp, house, stock, alert = self._run_chain(
            {"status": "completed"},
            supplementary_result={"status": "skipped", "reason": "already_running"},
        )
        senate.assert_called_once()
        supp.assert_called_once()
        house.assert_not_called()
        stock.assert_not_called()
        alert.assert_called_once()
        assert "Supplementary" in alert.call_args[0][0]

    def test_house_skip_stops_stock_but_earlier_steps_already_ran(self):
        senate, supp, house, stock, alert = self._run_chain(
            {"status": "completed"},
            house_result={"status": "skipped", "reason": "already_running"},
        )
        supp.assert_called_once()
        house.assert_called_once()
        stock.assert_not_called()
        alert.assert_called_once()
        assert "House" in alert.call_args[0][0]

    def test_stock_skip_alerts_with_nothing_left_to_stop(self):
        senate, supp, house, stock, alert = self._run_chain(
            {"status": "completed"},
            stock_result={"status": "skipped", "reason": "already_running"},
        )
        stock.assert_called_once()
        alert.assert_called_once()
        assert "Stock trades" in alert.call_args[0][0]
