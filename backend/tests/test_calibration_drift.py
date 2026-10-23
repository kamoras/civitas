"""A hand-labelled calibration is checked against the stream it gates: each
gate's pass rate in the last four weeks against its own first four weeks
under the calibration in use (app/calibration_drift.py)."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from app import calibration_drift as cd
from app.pipeline.analyze import trend_relevance


@pytest.fixture
def store(db_session, monkeypatch):
    monkeypatch.setattr(cd, "SessionLocal", lambda: SimpleNamespace(
        query=db_session.query, add=db_session.add, commit=db_session.commit, execute=db_session.execute,
        rollback=db_session.rollback, get=db_session.get, close=lambda: None, flush=db_session.flush,
        bind=db_session.bind, merge=db_session.merge))
    cd._pending.clear()
    return db_session


def _count(gate, day, n, passed, freeze_utcnow):
    freeze_utcnow(datetime.combine(day, datetime.min.time()).replace(hour=12))
    cd.observe(gate, [1.0] * passed + [0.0] * (n - passed), 0.5)
    cd.flush()


def test_decisions_are_counted_in_memory_and_written_by_flush(store, freeze_utcnow):
    freeze_utcnow(datetime(2028, 3, 1, 12))
    cd.observe("committee_routing", [0.2, 0.6, 0.9], 0.5)
    cd.observe("committee_routing", [0.7], 0.5)
    assert cd._days(store, "committee_routing", date(2028, 3, 1), date(2028, 3, 2)) == (0, 0)
    cd.flush()
    assert cd._days(store, "committee_routing", date(2028, 3, 1), date(2028, 3, 2)) == (4, 3)
    cd.observe("committee_routing", [0.1], 0.5)
    cd.flush()
    assert cd._days(store, "committee_routing", date(2028, 3, 1), date(2028, 3, 2)) == (5, 3)


def test_a_moved_pass_rate_is_flagged_and_noise_is_not(store, freeze_utcnow):
    start = date(2028, 1, 1)
    freeze_utcnow(datetime(2028, 1, 1, 12))
    with patch.object(cd, "_calibration_sha", return_value="v1"):
        assert cd.drift("committee_routing", start) is None  # first seen: the baseline starts today
        for d in range(28):
            _count("committee_routing", start + timedelta(days=d), 40, 12, freeze_utcnow)  # 30%
        today = start + timedelta(days=70)
        for d in range(28):
            _count("committee_routing", today - timedelta(days=d), 40, 14, freeze_utcnow)  # 35%: within noise
        assert cd.drift("committee_routing", today) is None
        later = today + timedelta(days=40)
        for d in range(28):
            _count("committee_routing", later - timedelta(days=d), 40, 26, freeze_utcnow)  # 65%
        why = cd.drift("committee_routing", later)
        assert "65%" in why and "30%" in why and "committee_routing_calibration.json" in why


def test_a_refit_starts_a_new_baseline(store, freeze_utcnow):
    start = date(2028, 1, 1)
    freeze_utcnow(datetime(2028, 1, 1, 12))
    with patch.object(cd, "_calibration_sha", return_value="v1"):
        cd.drift("committee_routing", start)
        for d in range(28):
            _count("committee_routing", start + timedelta(days=d), 40, 4, freeze_utcnow)
    today = start + timedelta(days=90)
    for d in range(28):
        _count("committee_routing", today - timedelta(days=d), 40, 30, freeze_utcnow)
    with patch.object(cd, "_calibration_sha", return_value="v2"):
        assert cd.drift("committee_routing", today) is None  # refit: no baseline yet under v2
    with patch.object(cd, "_calibration_sha", return_value="v1"):
        assert cd.drift("committee_routing", today) is None  # the v1 record was replaced, so v1 starts over too


def test_too_few_decisions_say_nothing(store, freeze_utcnow):
    start = date(2028, 1, 1)
    freeze_utcnow(datetime(2028, 1, 1, 12))
    with patch.object(cd, "_calibration_sha", return_value="v1"):
        cd.drift("member_photo", start)
        _count("member_photo", start, 20, 2, freeze_utcnow)
        _count("member_photo", start + timedelta(days=80), 20, 18, freeze_utcnow)
        assert cd.drift("member_photo", start + timedelta(days=80)) is None


def test_check_all_alerts_per_gate_and_resolves_the_rest(store):
    with patch.object(cd, "drift", side_effect=lambda g: "moved" if g == "trend_loaded" else None), \
         patch.object(cd, "send_ops_alert") as send, patch.object(cd, "resolve_ops_alert") as resolve:
        out = cd.check_all()
    assert out["trend_loaded"] == "moved"
    assert send.call_args.kwargs["condition"] == "calibration-drift-trend-loaded"
    assert "calibration-drift-committee-routing" in [c.args[0] for c in resolve.call_args_list]


def test_every_gate_names_a_calibration_file_that_exists():
    for gate, (filename, _) in cd.GATES.items():
        assert (cd.pathlib.Path(cd.__file__).parent / "data" / filename).exists(), gate


def test_the_gates_report_what_they_decide(monkeypatch):
    """The instrumented gates count every decision they make."""
    seen = []
    monkeypatch.setattr(cd, "observe", lambda gate, scores, threshold: seen.append((gate, list(scores), threshold)))
    monkeypatch.setattr(trend_relevance, "relevance_votes", lambda *a: np.array([0.2, 0.9]))
    monkeypatch.setattr(trend_relevance, "_reference", lambda gate: None)
    trend_relevance.gate_votes("tragedy", np.zeros((2, 3)))
    assert seen[0][0] == "trend_tragedy" and seen[0][1] == [0.2, 0.9]


def test_a_self_refitting_gate_gets_a_refit_and_is_alerted_only_if_drift_outlasts_it(store, freeze_utcnow):
    """Drift on a gate that refits itself from data-supplied labels makes its
    refit due; only drift still there REFIT_GRACE_DAYS later is alerted."""
    gate = "committee_routing"
    assert gate in cd.REFITTING
    freeze_utcnow(datetime(2028, 3, 1, 12))
    moved = lambda g: "moved" if g == gate else None  # noqa: E731
    with patch.object(cd, "drift", side_effect=moved), patch.object(cd.data_refresh, "request_refit") as request, \
         patch.object(cd, "send_ops_alert") as send, patch.object(cd, "resolve_ops_alert"):
        cd.check_all()
        request.assert_called_once()
        assert request.call_args.args[0] == cd.REFITTING[gate]
        assert not [c for c in send.call_args_list if c.kwargs["condition"] == "calibration-drift-committee-routing"]
        freeze_utcnow(datetime(2028, 3, 1, 12) + timedelta(days=cd.REFIT_GRACE_DAYS))
        cd.check_all()
        sent = [c for c in send.call_args_list if c.kwargs["condition"] == "calibration-drift-committee-routing"]
        assert sent and "remains" in sent[0].args[1]
    # Cleared, the episode ends: the next drift starts a fresh grace period.
    with patch.object(cd, "drift", return_value=None), patch.object(cd, "send_ops_alert"), \
         patch.object(cd, "resolve_ops_alert"):
        cd.check_all()
    with patch.object(cd, "drift", side_effect=moved), patch.object(cd.data_refresh, "request_refit"), \
         patch.object(cd, "send_ops_alert") as send, patch.object(cd, "resolve_ops_alert"):
        cd.check_all()
        assert not [c for c in send.call_args_list if c.kwargs["condition"] == "calibration-drift-committee-routing"]


def test_a_daily_refit_keeps_the_baseline_while_its_parameters_hold(tmp_path, monkeypatch):
    """A self-refitting gate's file is rewritten daily with new measurements;
    its baseline follows only the parameters that decide."""
    path = tmp_path / "committee_routing_calibration.json"
    monkeypatch.setattr(cd, "runtime_data_path", lambda name: str(path))
    keys = cd.PARAMETERS["committee_routing"]
    path.write_text('{"reference": "data", "k": 15, "alpha": 0.0, "temperature": 5.0, "threshold": 0.5, "x": 1}')
    first = cd._calibration_sha("committee_routing_calibration.json", keys)
    path.write_text('{"reference": "data", "k": 15, "alpha": 0.0, "temperature": 5.0, "threshold": 0.5, "x": 2}')
    assert cd._calibration_sha("committee_routing_calibration.json", keys) == first
    path.write_text('{"reference": "data", "k": 15, "alpha": 0.0, "temperature": 5.0, "threshold": 0.55, "x": 2}')
    assert cd._calibration_sha("committee_routing_calibration.json", keys) != first
