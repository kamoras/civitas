"""The post-run component-overlap check (analyze/signal_overlap.py)."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.analyze import signal_overlap
from app.pipeline.analyze.signal_overlap import band, measure, pearson, record_signal_overlap


def _breakdown(leadership=None, coalition=None, vote=None, congruence=None) -> dict:
    le = [{"label": "Bill significance & advancement (V&W-based)", "score": 40.0}]
    if leadership is not None:
        le.append({"label": "Legislative leadership", "score": leadership})
    if coalition is not None:
        le.append({"label": "Bipartisan coalition attraction", "score": coalition})
    ca = []
    if vote is not None:
        ca.append({"label": "Seat-relative vote alignment", "score": vote})
    if congruence is not None:
        ca.append({"label": "Position congruence", "score": congruence})
    return {"legislativeEffectiveness": {"components": le}, "constituentAlignment": {"components": ca}}


def test_pearson():
    assert pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert pearson([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    # Too few pairs, or no spread on one side: nothing to report.
    assert pearson([1, 2], [1, 2]) is None
    assert pearson([1, 2, 3], [5, 5, 5]) is None


def test_bands():
    assert band(None) == "none"
    assert band(0.1) == "ok"
    assert band(-0.45) == "watch"
    assert band(0.6) == "action"
    assert band(-0.9) == "action"


def test_a_member_without_a_component_is_left_out_of_that_pair_only():
    got = measure([
        _breakdown(10, 20, 50, 60),
        _breakdown(20, 40, 60, 50),
        _breakdown(30, 60, 70, 40),
        # No cosponsorship data: no coalition component, still in the CA pair.
        _breakdown(40, None, 80, 30),
    ])
    assert got["effectiveness"]["n"] == 3
    assert got["effectiveness"]["r"] == pytest.approx(1.0)
    assert got["effectiveness"]["band"] == "action"
    assert got["constituent"]["n"] == 4
    assert got["constituent"]["r"] == pytest.approx(-1.0)
    assert got["effectiveness"]["labels"] == ["Legislative leadership", "Bipartisan coalition attraction"]


def test_the_labels_are_the_ones_the_score_calculator_gives():
    """A renamed component would silently empty a pair."""
    import inspect

    from app.pipeline.analyze import score_calculator

    source = inspect.getsource(score_calculator)
    for _, _, first, second in signal_overlap.PAIRS:
        assert f'"{first}"' in source and f'"{second}"' in source


def test_record_persists_and_alerts_only_in_the_action_band():
    overlapping = [_breakdown(x, 2 * x, 50 + x, 50) for x in (10.0, 20.0, 30.0, 40.0)]
    with patch.object(signal_overlap, "chamber_breakdowns", return_value=overlapping), \
            patch.object(signal_overlap.SIGNAL_OVERLAP, "write") as write, \
            patch("app.ops_alerts.send_ops_alert") as alert:
        result = record_signal_overlap(None, "house")
    write.assert_called_once_with("house", {"pairs": result})
    assert result["effectiveness"]["band"] == "action"
    alert.assert_called_once()
    assert "Legislative leadership vs Bipartisan coalition attraction" in alert.call_args.args[1]

    independent = [_breakdown(10.0, 50.0), _breakdown(20.0, 30.0), _breakdown(30.0, 60.0), _breakdown(40.0, 40.0)]
    with patch.object(signal_overlap, "chamber_breakdowns", return_value=independent), \
            patch.object(signal_overlap.SIGNAL_OVERLAP, "write"), \
            patch("app.ops_alerts.send_ops_alert") as alert:
        record_signal_overlap(None, "house")
    alert.assert_not_called()


def test_record_never_raises():
    with patch.object(signal_overlap, "chamber_breakdowns", side_effect=RuntimeError("db gone")):
        assert record_signal_overlap(None, "senate") is None


def test_only_the_two_chambers_are_measured():
    with pytest.raises(ValueError):
        signal_overlap.chamber_breakdowns(None, "presidents")


def test_the_endpoint_serves_each_chamber_or_null():
    stored = {"senate": {"pairs": {"effectiveness": {"r": 0.083, "n": 98, "band": "ok"}},
                         "computed_at": "2026-09-28T04:00:00"}}
    with patch.object(signal_overlap.SIGNAL_OVERLAP, "load", return_value=stored):
        body = TestClient(app).get("/api/signal-overlap").json()
    assert body["chambers"]["senate"]["pairs"]["effectiveness"]["r"] == 0.083
    assert body["chambers"]["senate"]["computedAt"] == "2026-09-28T04:00:00"
    assert body["chambers"]["house"] is None
    assert body["actionR"] == signal_overlap.ACTION_R


def test_no_scoring_code_imports_the_check():
    """Exempt from the analysis hash because nothing it computes feeds a
    score; an import from scoring code would break that."""
    import ast
    import pathlib

    pipeline_dir = pathlib.Path(signal_overlap.__file__).resolve().parent.parent
    # Callers that run it after their scores are committed, never before.
    allowed = {"senate_pipeline.py", "house_pipeline.py", "constituent_rescore.py", "signal_overlap.py"}
    for py in pipeline_dir.rglob("*.py"):
        if py.name in allowed:
            continue
        for node in ast.walk(ast.parse(py.read_text())):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            else:
                continue
            assert not any("signal_overlap" in n for n in names), py


@pytest.mark.parametrize("chamber", ["senate", "house"])
def test_chamber_breakdowns_runs_against_the_schema(db_session, chamber):
    assert signal_overlap.chamber_breakdowns(db_session, chamber) == []


def test_measures_current_members_through_the_api_breakdown(db_session):
    from app.models import Senator

    for i, (leadership, coalition) in enumerate([(0.2, 0.3), (0.5, 0.6), (0.8, 0.4)]):
        db_session.add(Senator(
            id=f"S{i}", name=f"Senator {i}", party="D", state="VT", score_legislative_effectiveness=60,
            leadership_score=leadership, attracted_bipartisanship_score=coalition, years_in_office=10,
        ))
    # Departed (in the removal grace window): not in the population.
    db_session.add(Senator(
        id="S9", name="Senator 9", party="R", state="WY", is_current=False, years_in_office=10,
        leadership_score=0.9, attracted_bipartisanship_score=0.9,
    ))
    db_session.commit()
    got = measure(signal_overlap.chamber_breakdowns(db_session, "senate"))
    assert got["effectiveness"]["n"] == 3
    assert got["effectiveness"]["r"] is not None
