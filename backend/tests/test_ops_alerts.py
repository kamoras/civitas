"""Tests for check_pipeline_overrun's coverage of all four nightly
pipelines (2026-07-23) — until this it only checked Senate and House, so
a wedged Supplementary or Stock run generated zero automatic alert.
Confirmed live as contributing to stock-trades data going stale 4+ days
and supplementary data 1+ day with nothing telling an operator to look.
"""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

from app.models import (
    ElectionPipelineRun, HousePipelineRun, PipelineRun, PipelineStatus,
    StockTradesPipelineRun, SupplementaryPipelineRun,
)
from app.ops_alerts import (
    check_pipeline_overrun,
    check_pipeline_staleness,
    recent_alerts,
    resolve_ops_alert,
    send_ops_alert,
)
from app.time_utils import utcnow


def _check(db_session):
    with patch("app.ops_alerts.SessionLocal", return_value=db_session), \
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

    def test_a_senate_run_its_lease_proves_dead_is_not_an_overrun(self, db_session):
        from tests.conftest import start_senate_run_then_stop_beating

        run = start_senate_run_then_stop_beating(db_session, beat_ago=timedelta(hours=2))
        run.started_at = utcnow() - timedelta(hours=9)
        db_session.commit()
        _check(db_session).assert_not_called()  # check_pipeline_staleness reports a run that never finished

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


class TestCheckFeedbackTokenExpiration:
    """FEEDBACK_TOKEN must be a fine-grained GitHub PAT (config.py's own
    comment), and those always expire. Runs only on Sunday to avoid an
    unnecessary GitHub API call every night."""

    SUNDAY = datetime(2026, 8, 2)  # a Sunday
    WEDNESDAY = datetime(2026, 7, 15)

    def _check(self, *, now, expiration_header, token="a-fine-grained-pat"):
        from app.ops_alerts import check_feedback_token_expiration

        mock_resp = MagicMock()
        mock_resp.headers = {"github-authentication-token-expiration": expiration_header} if expiration_header else {}
        with patch("app.ops_alerts.settings.FEEDBACK_TOKEN", token), \
             patch("app.ops_alerts.utcnow", return_value=now), \
             patch("app.ops_alerts.httpx.get", return_value=mock_resp) as mock_get, \
             patch("app.ops_alerts.send_ops_alert") as mock_alert:
            check_feedback_token_expiration()
        return mock_alert, mock_get

    def test_no_token_configured_skips_the_api_call_entirely(self):
        mock_alert, mock_get = self._check(now=self.SUNDAY, expiration_header="2026-09-01 00:00:00 UTC", token="")
        mock_get.assert_not_called()
        mock_alert.assert_not_called()

    def test_skipped_on_a_non_sunday(self):
        mock_alert, mock_get = self._check(now=self.WEDNESDAY, expiration_header="2026-08-05 00:00:00 UTC")
        mock_get.assert_not_called()
        mock_alert.assert_not_called()

    def test_silent_when_expiration_is_far_out(self):
        mock_alert, _ = self._check(now=self.SUNDAY, expiration_header="2027-06-01 00:00:00 UTC")
        mock_alert.assert_not_called()

    def test_alerts_within_30_days_of_expiration(self):
        mock_alert, _ = self._check(now=self.SUNDAY, expiration_header="2026-08-15 00:00:00 UTC")
        mock_alert.assert_called_once()
        assert "2026-08-15" in mock_alert.call_args.args[1]

    def test_silent_when_header_missing(self):
        """Classic (non-expiring) PATs don't carry this header at all."""
        mock_alert, _ = self._check(now=self.SUNDAY, expiration_header=None)
        mock_alert.assert_not_called()

    def test_network_failure_does_not_raise(self):
        from app.ops_alerts import check_feedback_token_expiration

        with patch("app.ops_alerts.settings.FEEDBACK_TOKEN", "a-token"), \
             patch("app.ops_alerts.utcnow", return_value=self.SUNDAY), \
             patch("app.ops_alerts.httpx.get", side_effect=Exception("network down")), \
             patch("app.ops_alerts.send_ops_alert") as mock_alert:
            check_feedback_token_expiration()  # must not raise
        mock_alert.assert_not_called()


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


def _check_stale(db_session):
    with patch("app.ops_alerts.SessionLocal", return_value=db_session), \
         patch("app.ops_alerts.send_ops_alert") as mock_alert:
        check_pipeline_staleness()
    return mock_alert


def _labels(mock_alert):
    return {call[0][0] for call in mock_alert.call_args_list}


class TestCheckPipelineStaleness:
    """The watchdog for a pipeline that never RAN, as opposed to one
    running too long.

    The 2026-09-01 outage went 19 nights unnoticed because every other
    alert here watches a run that exists: nothing was skipped, an absent
    pipeline has no RUNNING row for check_pipeline_overrun to find, and
    the scheduler's `except BaseException` cannot catch the SIGKILL that
    actually killed it. Absence was the only signal being emitted, and
    nothing was watching for it.
    """

    def test_a_fresh_install_with_no_runs_at_all_is_silent(self, db_session):
        """No rows anywhere is a new deployment, not a stall — alerting
        would fire on day one of every install."""
        _check_stale(db_session).assert_not_called()

    def test_a_recent_successful_run_is_silent(self, db_session):
        db_session.add(PipelineRun(
            started_at=utcnow() - timedelta(hours=9),
            completed_at=utcnow() - timedelta(hours=8),
            status=PipelineStatus.COMPLETED,
        ))
        db_session.commit()
        _check_stale(db_session).assert_not_called()

    def test_one_missed_night_does_not_cry_wolf(self, db_session):
        """Threshold is 2 days precisely so a single skipped night, which
        self-heals the next evening, stays quiet."""
        db_session.add(PipelineRun(
            started_at=utcnow() - timedelta(days=1, hours=1),
            completed_at=utcnow() - timedelta(days=1),
            status=PipelineStatus.COMPLETED,
        ))
        db_session.commit()
        _check_stale(db_session).assert_not_called()

    def test_a_pipeline_that_stopped_completing_alerts(self, db_session):
        db_session.add(PipelineRun(
            started_at=utcnow() - timedelta(days=19),
            completed_at=utcnow() - timedelta(days=19),
            status=PipelineStatus.COMPLETED,
        ))
        db_session.commit()
        mock_alert = _check_stale(db_session)
        mock_alert.assert_called_once()
        assert "Senate" in mock_alert.call_args[0][0]
        assert "19." in mock_alert.call_args[0][1]

    def test_the_real_outage_shape_alerts_on_every_downstream_pipeline(self, db_session):
        """Reproduces 2026-09-01: Senate kept completing nightly while
        House, Stock trades and Election silently never ran again. The
        healthy pipeline must stay quiet and all three dead ones must
        report — that combination is what makes the alert actionable,
        since the first quiet one points at where the chain broke."""
        db_session.add(PipelineRun(
            started_at=utcnow() - timedelta(hours=9),
            completed_at=utcnow() - timedelta(hours=8),
            status=PipelineStatus.COMPLETED,
        ))
        for model in (HousePipelineRun, StockTradesPipelineRun, ElectionPipelineRun):
            db_session.add(model(
                started_at=utcnow() - timedelta(days=19),
                completed_at=utcnow() - timedelta(days=19),
                status=PipelineStatus.COMPLETED,
            ))
        db_session.commit()
        labels = _labels(_check_stale(db_session))
        assert labels == {
            "House pipeline is stale",
            "Stock trades pipeline is stale",
            "Election pipeline is stale",
        }

    def test_election_is_covered_even_though_the_overrun_check_omits_it(self, db_session):
        db_session.add(ElectionPipelineRun(
            started_at=utcnow() - timedelta(days=19),
            completed_at=utcnow() - timedelta(days=19),
            status=PipelineStatus.COMPLETED,
        ))
        db_session.commit()
        assert _labels(_check_stale(db_session)) == {"Election pipeline is stale"}

    def test_a_pipeline_that_has_tried_but_never_succeeded_alerts(self, db_session):
        """Distinct from a fresh install: rows exist, none ever completed."""
        db_session.add(HousePipelineRun(
            started_at=utcnow() - timedelta(days=3), status=PipelineStatus.FAILED,
        ))
        db_session.commit()
        mock_alert = _check_stale(db_session)
        mock_alert.assert_called_once()
        assert "never completed successfully" in mock_alert.call_args[0][1]

    def test_a_still_running_row_does_not_count_as_a_completion(self, db_session):
        """A wedged run is exactly the 2026-09 shape — it must not mask
        the staleness of the data it has failed to refresh."""
        db_session.add(SupplementaryPipelineRun(
            started_at=utcnow() - timedelta(days=5),
            completed_at=utcnow() - timedelta(days=5),
            status=PipelineStatus.COMPLETED,
        ))
        db_session.add(SupplementaryPipelineRun(
            started_at=utcnow() - timedelta(hours=2), status=PipelineStatus.RUNNING,
        ))
        db_session.commit()
        assert _labels(_check_stale(db_session)) == {"Supplementary pipeline is stale"}


class TestOpenAndResolved:
    """An alert about a condition stays open until the code that detects
    it sees it gone (2026-09-29: the dashboard listed ten newest alerts
    with no way to tell a fixed problem from a live one)."""

    def _alerts(self, db_session):
        with patch("app.ops_alerts.SessionLocal", return_value=db_session):
            return recent_alerts()

    def _send(self, db_session, subject, **kw):
        with patch("app.ops_alerts.SessionLocal", return_value=db_session):
            return send_ops_alert(subject, "body", **kw)

    def _resolve(self, db_session, condition):
        with patch("app.ops_alerts.SessionLocal", return_value=db_session):
            return resolve_ops_alert(condition)

    def test_a_condition_alert_is_open_until_resolved(self, db_session):
        self._send(db_session, "Justice loyalty not measured", dedupe_key="j-1", condition="justice")
        [alert] = self._alerts(db_session)
        assert (alert["condition"], alert["resolvedAt"], alert["open"]) == ("justice", None, True)
        assert self._resolve(db_session, "justice") == 1
        [alert] = self._alerts(db_session)
        assert alert["resolvedAt"] is not None and alert["open"] is False
        assert self._resolve(db_session, "justice") == 0  # nothing left open

    def test_resolving_frees_the_dedupe_key_so_a_recurrence_alerts_again(self, db_session):
        assert self._send(db_session, "Down", dedupe_key="day-1", condition="c")
        assert not self._send(db_session, "Down", dedupe_key="day-1", condition="c")
        self._resolve(db_session, "c")
        assert self._send(db_session, "Down again", dedupe_key="day-1", condition="c")

    def test_a_newer_alert_for_the_condition_supersedes_the_older(self, db_session):
        self._send(db_session, "3 states failed", dedupe_key="a", condition="ingest")
        self._send(db_session, "1 state failed", dedupe_key="b", condition="ingest")
        open_ = [a["subject"] for a in self._alerts(db_session) if a["open"]]
        assert open_ == ["1 state failed"]

    def test_an_event_alert_has_no_open_state(self, db_session):
        self._send(db_session, "Something happened once")
        [alert] = self._alerts(db_session)
        assert (alert["condition"], alert["open"]) == (None, False)

    def test_an_open_alert_is_never_pushed_off_by_newer_history(self, db_session):
        self._send(db_session, "Still broken", condition="old")
        for i in range(12):
            self._send(db_session, f"event {i}", dedupe_key=f"e{i}")
        alerts = self._alerts(db_session)
        assert alerts[0]["subject"] == "Still broken"
        assert len(alerts) == 11  # the open one, then the ten newest others

    def test_the_overrun_watchdog_resolves_once_the_run_is_over(self, db_session):
        self._send(db_session, "House pipeline overrun", condition="overrun-house")
        _check(db_session)  # no House run is running any more
        assert self._alerts(db_session)[0]["resolvedAt"] is not None

    def test_the_staleness_watchdog_resolves_a_pipeline_that_completed_again(self, db_session):
        self._send(db_session, "House pipeline is stale", condition="stale-pipeline-house")
        db_session.add(HousePipelineRun(
            started_at=utcnow() - timedelta(hours=3), completed_at=utcnow() - timedelta(hours=1),
            status=PipelineStatus.COMPLETED,
        ))
        db_session.commit()
        _check_stale(db_session)
        assert self._alerts(db_session)[0]["resolvedAt"] is not None


class TestEachAlertResolves:
    """Where a condition is seen gone, its alert is resolved with the same
    key it was raised under. resolve_ops_alert never raises, so a wrong
    key would fail silently; these pin the keys."""

    def test_a_clean_problem_report_resolves_its_key(self):
        from app.pipeline.fetch.state_candidates import report_file_problems

        with patch("app.ops_alerts.resolve_ops_alert") as resolve:
            report_file_problems("Election source crawl failed", "lead", [], "election-source-crawl")
        resolve.assert_called_once_with("election-source-crawl")

    def test_lda_lookups_that_mostly_work_resolve_and_none_says_nothing(self):
        from app.pipeline.fetch.lda import alert_if_lda_down

        with patch("app.ops_alerts.resolve_ops_alert") as resolve, \
             patch("app.ops_alerts.send_ops_alert") as send:
            alert_if_lda_down({"lookups": 3, "failed": 1}, "house")
            alert_if_lda_down({"lookups": 0, "failed": 0}, "senate")
        resolve.assert_called_once_with("lda-down-house")
        send.assert_not_called()

    def test_a_night_with_no_failing_state_resolves_the_ingest_alert(self):
        from app.pipeline.election_pipeline import _alert_ingest_failures

        with patch("app.ops_alerts.resolve_ops_alert") as resolve:
            _alert_ingest_failures([], "2026-11-03")
        resolve.assert_called_once_with("ballot-measure-ingest-2026-11-03")

    def test_a_clean_ground_truth_gate_resolves_its_chamber(self):
        from app.pipeline import run_checks

        run, db = MagicMock(), MagicMock()
        with patch.object(run_checks, "resolve_ops_alert") as resolve, \
             patch.object(run_checks, "send_ops_alert") as send:
            run_checks.persist_ground_truth_failures(
                db, run, [], alert_title="t", alert_body="b", dedupe_key="k", condition="ground-truth-house",
            )
        resolve.assert_called_once_with("ground-truth-house")
        send.assert_not_called()
