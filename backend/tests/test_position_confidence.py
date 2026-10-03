"""The calibrated vote count behind position congruence's thin-record
shrinkage (v6.27): scripts/calibrate_position_confidence.py fits it and
writes app/data/position_confidence.json, which the scorer reads."""

import importlib.util
import json
import pathlib

from app.pipeline.analyze import score_calculator

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "calibrate_position_confidence.py"
_DATA = pathlib.Path(__file__).resolve().parents[1] / "app" / "data" / "position_confidence.json"


def _script():
    spec = importlib.util.spec_from_file_location("calibrate_position_confidence", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fit_recovers_drift_and_noise():
    """gap^2 = drift + k / votes, exactly: the fit returns both and the
    count at which the noise equals the drift."""
    obs = [(n, 0.004 + 0.32 / n) for n in (10, 20, 40, 80, 160, 320, 640)]
    fit = _script().fit_noise(obs)
    assert abs(fit["drift"] - 0.004) < 1e-9 and abs(fit["k"] - 0.32) < 1e-9
    assert abs(fit["full_confidence_votes"] - 80) < 1e-6


def test_observations_skip_members_without_both_positions_or_a_count():
    rows = [
        {"nominate_number_of_votes": "100", "nokken_poole_dim1": "0.5", "nominate_dim1": "0.4"},
        {"nominate_number_of_votes": "", "nokken_poole_dim1": "0.5", "nominate_dim1": ""},
        {"nominate_number_of_votes": "0", "nokken_poole_dim1": "0.5", "nominate_dim1": "0.4"},
        {"nominate_number_of_votes": "50", "nokken_poole_dim1": "", "nominate_dim1": "0.4"},
    ]
    obs = _script().observations(rows)
    assert len(obs) == 1 and obs[0][0] == 100 and abs(obs[0][1] - 0.01) < 1e-12


def test_shipped_file_is_its_own_fit_and_documents_its_source():
    data = json.loads(_DATA.read_text())
    assert "calibrate_position_confidence.py" in data["_source"]
    assert data["k"] > 0 and data["drift"] > 0
    assert data["full_confidence_votes"] == round(data["k"] / data["drift"])


def test_scorer_reads_the_shipped_file(monkeypatch):
    monkeypatch.setattr(score_calculator, "_position_full_confidence_cache", None)
    assert score_calculator._position_full_confidence_votes() == json.loads(_DATA.read_text())["full_confidence_votes"]


def test_confidence_is_count_over_the_calibration(monkeypatch):
    monkeypatch.setattr(score_calculator, "_position_full_confidence_cache", 80.0)
    assert score_calculator.position_confidence(40) == 0.5
    assert score_calculator.position_confidence(400) == 1.0
    assert score_calculator.position_confidence(0) == 0.0
    assert score_calculator.position_confidence(None) == 1.0
