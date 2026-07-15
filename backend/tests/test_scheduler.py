"""Tests for the hourly action-center refresh's self-overlap guard.

_hourly_action_refresh fires a background thread on every cron tick with
no built-in protection against the previous refresh still running — a
slow/degraded local LLM can make one cycle run long enough to still be
active when the next tick fires (confirmed live 2026-07-13: this let
multiple refreshes pile up competing for the same LLM, worsening the
slowdown). These tests run the spawned thread synchronously (patching
threading.Thread) so the guard's decision can be asserted directly.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


class _SyncThread:
    """Drop-in for threading.Thread that runs the target immediately."""

    def __init__(self, target, daemon=None, name=None):
        self._target = target

    def start(self):
        self._target()


def _run_hourly_refresh(refresh_state: dict, house_running: bool = False, stock_running: bool = False, stock_age=None):
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
             patch("app.scheduler.stock_pipeline_age", return_value=stock_age):
            scheduler._hourly_action_refresh()

    return mock_refresh


class TestActionRefreshOverlapGuard:
    def test_skips_when_a_recent_refresh_is_still_running(self):
        state = {"is_running": True, "started_at": datetime.utcnow() - timedelta(minutes=10)}
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
        state = {"is_running": True, "started_at": datetime.utcnow() - timedelta(hours=5)}
        mock_refresh = _run_hourly_refresh(state)
        mock_refresh.assert_called_once()

    def test_does_not_skip_at_exactly_the_4_hour_boundary_edge(self):
        # Just under 4h: still treated as a legitimately running refresh.
        state = {"is_running": True, "started_at": datetime.utcnow() - timedelta(hours=3, minutes=59)}
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
