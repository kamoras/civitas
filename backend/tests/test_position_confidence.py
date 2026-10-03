"""The reliability weight on a congress-specific roll-call position (v6.27):
scripts/calibrate_position_confidence.py measures n0 of n / (n + n0) and
writes app/data/position_confidence.json, which the scorer reads."""

import importlib.util
import json
import pathlib

import numpy as np

from app.pipeline.analyze import score_calculator

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "calibrate_position_confidence.py"
_DATA = pathlib.Path(__file__).resolve().parents[1] / "app" / "data" / "position_confidence.json"


def _script():
    spec = importlib.util.spec_from_file_location("calibrate_position_confidence", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_half_weight_fit_recovers_n0():
    points = [(n, n / (n + 24.0)) for n in (10, 20, 40, 80, 160)]
    assert abs(_script().fit_half_weight(points) - 24.0) < 0.11


def test_a_placeholder_is_not_a_position():
    script = _script()
    assert script.is_placeholder({"nokken_poole_dim1": "0.0", "nokken_poole_dim2": "0.0"})
    assert not script.is_placeholder({"nokken_poole_dim1": "0.0", "nokken_poole_dim2": "0.3"})


def test_a_member_is_located_from_their_votes():
    """Roll calls with known logits; a member at +0.3 who votes as the model
    predicts is placed near +0.3, and from fewer votes, less precisely."""
    script = _script()
    rng = np.random.default_rng(0)
    b = rng.uniform(4, 12, 4000)
    a = -b * rng.uniform(-0.8, 0.8, 4000)
    y = (rng.random(4000) < 1 / (1 + np.exp(-(a + b * 0.3)))).astype(float)
    assert abs(script.locate(a, b, y, 0.0) - 0.3) < 0.03
    errors = [abs(script.locate(a[s], b[s], y[s], 0.0) - 0.3)
              for s in (rng.choice(4000, 15, replace=False) for _ in range(200))]
    assert np.median(errors) > 0.03


def test_roll_call_fit_recovers_the_cut():
    """Members left of 0 vote Nay, right of it Yea: b positive, cut near 0."""
    script = _script()
    x = np.linspace(-1, 1, 101)
    y = (x > 0).astype(float)[:, None]
    a, b = script.fit_rollcalls(x, y, np.ones_like(y))
    assert b[0] > 0 and abs(-a[0] / b[0]) < 0.05


def test_shipped_file_is_its_own_fit_and_documents_its_source():
    data = json.loads(_DATA.read_text())
    assert "calibrate_position_confidence.py" in data["_source"]
    assert set(data["chambers"]) == {"senate", "house"}
    for chamber in data["chambers"].values():
        own = [(int(n), w) for n, w in chamber["weights"].items()]
        assert abs(_script().fit_half_weight(own) - chamber["half_weight_votes"]) < 0.11
        weights = [w for _, w in sorted((int(n), w) for n, w in chamber["weights"].items())]
        assert weights == sorted(weights) and 0 < weights[0] < weights[-1] < 1


def test_scorer_reads_each_chambers_own_value(monkeypatch):
    monkeypatch.setattr(score_calculator, "_position_half_weight_cache", None)
    chambers = json.loads(_DATA.read_text())["chambers"]
    for chamber in ("senate", "house"):
        assert score_calculator._position_half_weight_votes(chamber) == chambers[chamber]["half_weight_votes"]


def test_weight_is_votes_over_votes_plus_half_weight():
    assert score_calculator.position_confidence(24, 24) == 0.5
    assert score_calculator.position_confidence(0, 24) == 0.0
    assert abs(score_calculator.position_confidence(576, 24) - 0.96) < 1e-12
    assert score_calculator.position_confidence(None, 24) == 1.0  # no counts: a pre-v6.27 section
    assert score_calculator.position_confidence(5, 0) == 1.0  # no half weight: DW-NOMINATE, or no calibration
