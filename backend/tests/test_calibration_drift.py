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
    cd.observe("policy_relevance", [0.2, 0.6, 0.9], 0.5)
    cd.observe("policy_relevance", [0.7], 0.5)
    assert cd._days(store, "policy_relevance", date(2028, 3, 1), date(2028, 3, 2)) == (0, 0)
    cd.flush()
    assert cd._days(store, "policy_relevance", date(2028, 3, 1), date(2028, 3, 2)) == (4, 3)
    cd.observe("policy_relevance", [0.1], 0.5)
    cd.flush()
    assert cd._days(store, "policy_relevance", date(2028, 3, 1), date(2028, 3, 2)) == (5, 3)


def test_a_moved_pass_rate_is_flagged_and_noise_is_not(store, freeze_utcnow):
    start = date(2028, 1, 1)
    freeze_utcnow(datetime(2028, 1, 1, 12))
    with patch.object(cd, "_calibration_sha", return_value="v1"):
        assert cd.drift("policy_relevance", start) is None  # first seen: the baseline starts today
        for d in range(28):
            _count("policy_relevance", start + timedelta(days=d), 40, 12, freeze_utcnow)  # 30%
        today = start + timedelta(days=70)
        for d in range(28):
            _count("policy_relevance", today - timedelta(days=d), 40, 14, freeze_utcnow)  # 35%: within noise
        assert cd.drift("policy_relevance", today) is None
        later = today + timedelta(days=40)
        for d in range(28):
            _count("policy_relevance", later - timedelta(days=d), 40, 26, freeze_utcnow)  # 65%
        why = cd.drift("policy_relevance", later)
        assert "65%" in why and "30%" in why and "policy_relevance_calibration.json" in why


def test_a_refit_starts_a_new_baseline(store, freeze_utcnow):
    start = date(2028, 1, 1)
    freeze_utcnow(datetime(2028, 1, 1, 12))
    with patch.object(cd, "_calibration_sha", return_value="v1"):
        cd.drift("cluster_pairs", start)
        for d in range(28):
            _count("cluster_pairs", start + timedelta(days=d), 40, 4, freeze_utcnow)
    today = start + timedelta(days=90)
    for d in range(28):
        _count("cluster_pairs", today - timedelta(days=d), 40, 30, freeze_utcnow)
    with patch.object(cd, "_calibration_sha", return_value="v2"):
        assert cd.drift("cluster_pairs", today) is None  # refit: no baseline yet under v2
    with patch.object(cd, "_calibration_sha", return_value="v1"):
        assert cd.drift("cluster_pairs", today) is None  # the v1 record was replaced, so v1 starts over too


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
    assert "calibration-drift-policy-relevance" in [c.args[0] for c in resolve.call_args_list]


def test_every_gate_names_a_calibration_file_that_exists():
    for gate, (filename, _) in cd.GATES.items():
        assert (cd.pathlib.Path(cd.__file__).parent / "data" / filename).exists(), gate


def test_the_gates_report_what_they_decide(monkeypatch):
    """The instrumented gates count every decision they make."""
    seen = []
    monkeypatch.setattr(cd, "observe", lambda gate, scores, threshold: seen.append((gate, list(scores), threshold)))
    monkeypatch.setattr(trend_relevance, "relevance_votes", lambda *a: np.array([0.2, 0.9]))
    monkeypatch.setattr(trend_relevance, "_reference", lambda gate: None)
    trend_relevance.gate_votes("relevance", np.zeros((2, 3)))
    assert seen[0][0] == "trend_relevance" and seen[0][1] == [0.2, 0.9]
