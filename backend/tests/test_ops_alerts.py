"""Tests for check_pipeline_overrun's coverage of all four nightly
pipelines (2026-07-23) — until this it only checked Senate and House, so
a wedged Supplementary or Stock run generated zero automatic alert.
Confirmed live as contributing to stock-trades data going stale 4+ days
and supplementary data 1+ day with nothing telling an operator to look.
"""

from datetime import date, timedelta
from unittest.mock import patch

from app.models import (
    HousePipelineRun, PipelineRun, PipelineStatus,
    StockTradesPipelineRun, SupplementaryPipelineRun,
)
from app.ops_alerts import check_pipeline_overrun
from app.time_utils import utcnow


def _check(db_session):
    with patch("app.database.SessionLocal", return_value=db_session), \
         patch("app.ops_alerts.send_ops_alert") as mock_alert:
        check_pipeline_overrun()
    return mock_alert


class TestCheckPipelineOverrunAllFourPipelines:
    def test_no_running_rows_sends_no_alert(self, db_session):
        mock_alert = _check(db_session)
        mock_alert.assert_not_called()

    def test_senate_overrunning_its_8h_budget_alerts(self, db_session):
        db_session.add(PipelineRun(started_at=utcnow() - timedelta(hours=9), status=PipelineStatus.RUNNING))
        db_session.commit()
        mock_alert = _check(db_session)
        mock_alert.assert_called_once()
        assert "Senate" in mock_alert.call_args[0][0]

    def test_house_overrunning_its_8h_budget_alerts(self, db_session):
        db_session.add(HousePipelineRun(started_at=utcnow() - timedelta(hours=9), status=PipelineStatus.RUNNING))
        db_session.commit()
        mock_alert = _check(db_session)
        mock_alert.assert_called_once()
        assert "House" in mock_alert.call_args[0][0]

    def test_supplementary_overrunning_its_8h_budget_alerts(self, db_session):
        # 8h, not stock's 2h: the weekly SCOTUS refresh includes an
        # uncached Oyez crawl that took 5h+ in run 69 — a tighter budget
        # would misfire on a run that's just legitimately slow that day.
        db_session.add(SupplementaryPipelineRun(started_at=utcnow() - timedelta(hours=9), status=PipelineStatus.RUNNING))
        db_session.commit()
        mock_alert = _check(db_session)
        mock_alert.assert_called_once()
        assert "Supplementary" in mock_alert.call_args[0][0]

    def test_supplementary_within_its_8h_budget_does_not_alert(self, db_session):
        db_session.add(SupplementaryPipelineRun(started_at=utcnow() - timedelta(hours=5), status=PipelineStatus.RUNNING))
        db_session.commit()
        mock_alert = _check(db_session)
        mock_alert.assert_not_called()

    def test_stock_overrunning_its_tighter_2h_budget_alerts(self, db_session):
        # Confirmed live run took ~90min (2026-07-15) — 2h budget, not
        # House/Supplementary's 8h.
        db_session.add(StockTradesPipelineRun(started_at=utcnow() - timedelta(hours=3), status=PipelineStatus.RUNNING))
        db_session.commit()
        mock_alert = _check(db_session)
        mock_alert.assert_called_once()
        assert "Stock trades" in mock_alert.call_args[0][0]

    def test_stock_within_its_2h_budget_does_not_alert(self, db_session):
        db_session.add(StockTradesPipelineRun(started_at=utcnow() - timedelta(hours=1), status=PipelineStatus.RUNNING))
        db_session.commit()
        mock_alert = _check(db_session)
        mock_alert.assert_not_called()

    def test_multiple_overrunning_pipelines_each_alert_independently(self, db_session):
        db_session.add(PipelineRun(started_at=utcnow() - timedelta(hours=9), status=PipelineStatus.RUNNING))
        db_session.add(StockTradesPipelineRun(started_at=utcnow() - timedelta(hours=3), status=PipelineStatus.RUNNING))
        db_session.commit()
        mock_alert = _check(db_session)
        assert mock_alert.call_count == 2


class _FrozenDate(date):
    """datetime.date is immutable/built-in — can't patch .today() on it
    directly, so freeze it via a real subclass instead (comparisons with
    plain `date` instances still work via inheritance)."""
    _frozen = date(2026, 1, 1)

    @classmethod
    def today(cls):
        return cls._frozen


class TestCheckStatePviStaleness:
    """state_pvi.json's sources are deliberately pinned to immutable
    historical snapshots (see the check's own docstring) — a scheduled
    refetch can't advance its 2-cycle window, so this alert is the only
    signal an operator gets that a manual refresh is due."""

    def _check(self, window: str, today: date):
        from app.ops_alerts import check_state_pvi_staleness

        frozen = type("FrozenDate", (_FrozenDate,), {"_frozen": today})
        with patch("app.pipeline.analyze.score_calculator._read_pvi_json",
                   return_value={"_window": window}), \
             patch("app.ops_alerts.date", frozen), \
             patch("app.ops_alerts.send_ops_alert") as mock_alert:
            check_state_pvi_staleness()
        return mock_alert

    def test_silent_well_before_next_cycle_is_due(self):
        mock_alert = self._check("2020+2024", today=date(2027, 1, 1))
        mock_alert.assert_not_called()

    def test_alerts_once_next_cycle_data_should_be_available(self):
        mock_alert = self._check("2020+2024", today=date(2029, 1, 1))
        mock_alert.assert_called_once()
        assert "2028" in mock_alert.call_args.args[1]

    def test_silent_right_before_the_due_date(self):
        mock_alert = self._check("2020+2024", today=date(2028, 12, 14))
        mock_alert.assert_not_called()

    def test_alerts_on_the_due_date(self):
        mock_alert = self._check("2020+2024", today=date(2028, 12, 15))
        mock_alert.assert_called_once()

    def test_silent_when_window_metadata_missing(self):
        mock_alert = self._check("", today=date(2030, 1, 1))
        mock_alert.assert_not_called()
