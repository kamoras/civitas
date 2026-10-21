"""The outlet-repost gate's threshold is fitted on labels the posts supply
(scripts/calibrate_engagement.py), never typed, and closes when the fit
can't meet the precision rule."""

import importlib.util
import json
from pathlib import Path

from app.pipeline.analyze import bluesky_engagement as be
from app.precision_rule import PRECISION_LOWER_BOUND

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "calibrate_engagement.py"
_spec = importlib.util.spec_from_file_location("calibrate_engagement", _SCRIPT)
calibrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(calibrate)


def test_the_bundled_calibration_says_where_it_came_from():
    data = json.loads(be.CALIBRATION_PATH.read_text())
    assert "calibrate_engagement.py" in data["_source"] and data["labels_sha256"]
    assert data["labelled_posts"] == len(data["labels"])
    t = data["threshold"]
    assert t is None or data["at_threshold"]["precision_exact_lower"] >= PRECISION_LOWER_BOUND
    # What the gate runs at, and whether anyone measured it, is stated beside the fit.
    assert data["in_use"]["measured"] is (t is not None)
    assert data["in_use"]["threshold"] == (t if t is not None else be.UNMEASURED_THRESHOLD)


def test_without_a_fit_the_gate_runs_at_the_unmeasured_value(monkeypatch):
    monkeypatch.setattr(be, "_calibration_cache", {"threshold": None})
    assert be.engage_threshold() == be.UNMEASURED_THRESHOLD
    monkeypatch.setattr(be, "_calibration_cache", {"threshold": 0.85})
    assert be.engage_threshold() == 0.85


def test_an_article_is_keyed_without_scheme_query_or_trailing_slash():
    assert calibrate.article_key("https://www.npr.org/2026/10/01/x/?utm=1") == "npr.org/2026/10/01/x"
    assert calibrate.article_key(None) is None
