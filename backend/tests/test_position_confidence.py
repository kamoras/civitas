"""The reliability weight on a congress-specific roll-call position (v6.27):
scripts/calibrate_position_confidence.py measures it from Voteview's own
positions and writes app/data/position_confidence.json, which the scorer
reads."""

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


def _row(icpsr, party, x, n, dim2="0.1"):
    return {"icpsr": icpsr, "party_code": party, "nokken_poole_dim1": x, "nokken_poole_dim2": dim2,
            "nominate_number_of_votes": n}


def test_fit_recovers_how_n0_rises_with_party_line_voting():
    """Pairs generated from full = 0.9 * n / (n + n0) * thin with
    log n0 = 3.5 + 2 * (share - 0.6), exactly, at two party-line shares."""
    import math
    script = _script()

    def n0(u):
        return math.exp(3.5 + 2.0 * (u - script.CENTER))
    data = []
    for u in (0.5, 0.8):
        data += [("H", f"F{u}{i}", 600.0, x, 0.9 * 600 / (600 + n0(u)) * x, "full", 110, u)
                 for i, x in enumerate((-0.2, -0.1, 0.1, 0.3))]
        data += [("H", f"T{u}{n}{i}", float(n), x, 0.9 * n / (n + n0(u)) * x, "thin", 110, u)
                 for n in (5, 20, 60, 150) for i, x in enumerate((-0.3, 0.2, 0.4))]
    got = script.fit(data)
    assert abs(got["a"] - 3.5) < 0.011 and abs(got["b"] - 2.0) < 0.051
    assert abs(got["drift"] - 0.9) < 1e-3
    assert abs(script.half_weight(got, 0.95, 0.5, 0.8) - n0(0.8)) / n0(0.8) < 0.02  # read at the edge


def test_party_line_share_counts_opposed_majorities(tmp_path):
    """Two roll calls: one where the parties' majorities split, one where
    both vote Yea. IDs written as floats in one file and ints in the other
    still match."""
    (tmp_path / "S150_members.csv").write_text(
        "congress,chamber,icpsr,party_code\n150,Senate,1,100\n150,Senate,2,100\n150,Senate,3,200.0\n150,Senate,4,200\n")
    (tmp_path / "S150_votes.csv").write_text(
        "congress,chamber,rollnumber,icpsr,cast_code\n" + "".join(
            f"150,Senate,{rc},{i}.0,{c}\n" for rc, casts in ((1, (1, 1, 6, 6)), (2, (1, 1, 1, 1)))
            for i, c in zip((1, 2, 3, 4), casts)))
    assert _script().party_line_share("S", 150, tmp_path) == 0.5


def test_deviations_are_flank_signed_from_the_party_center():
    rows = [_row("1", "100", "-0.5", "300"), _row("2", "100", "-0.3", "300"), _row("3", "100", "-0.6", "10"),
            _row("4", "200.0", "0.4", "300"), _row("5", "200", "0", "2", dim2="0"),  # a placeholder
            _row("6", "100", "-0.4", "")]
    dev = _script().deviations(rows)
    assert "5" not in dev  # no position
    assert dev["3"][0] == 10.0 and abs(dev["3"][1] - 0.2) < 1e-12  # 0.2 left of the Democrats' center (-0.4)
    assert dev["4"] == (300.0, 0.0)  # float-coded party parses
    assert dev["6"][0] == 0.0  # no count reported


def test_a_placeholder_is_not_a_position():
    script = _script()
    assert script.is_placeholder({"nokken_poole_dim1": "0.000", "nokken_poole_dim2": "-0.0"})
    assert not script.is_placeholder({"nokken_poole_dim1": "0.0", "nokken_poole_dim2": "0.3"})


def test_shipped_file_documents_its_source_and_intervals():
    data = json.loads(_DATA.read_text())
    assert "calibrate_position_confidence.py" in data["_source"]
    lo, hi = data["model"]["shares_covered"]
    for chamber in ("senate", "house"):
        c = data["chambers"][chamber]
        assert c["interval_90"][0] <= c["half_weight_votes"] <= c["interval_90"][1]
        assert lo <= c["read_at"] <= hi
        assert c["read_at"] == min(max(c["party_line_share"], lo), hi)
    assert data["model"]["b_interval_90"][0] > 0  # n0 rises with party-line voting
    assert data["pairs"]["thin"] > 50 and data["pairs"]["full"] > 1000


def test_scorer_reads_each_chambers_value(monkeypatch):
    monkeypatch.setattr(score_calculator, "_position_reliability_cache", None)
    data = json.loads(_DATA.read_text())
    for chamber in ("senate", "house"):
        assert score_calculator._position_reliability(chamber) == {
            "half_weight_votes": data["chambers"][chamber]["half_weight_votes"]}


def test_weight():
    rel = {"half_weight_votes": 64.0}
    assert score_calculator.position_confidence(64, rel) == 0.5
    assert score_calculator.position_confidence(0, rel) == 0.0
    assert score_calculator.position_confidence(None, rel) == 0.0  # no count reported: none
    assert abs(score_calculator.position_confidence(576, rel) - 0.9) < 1e-12
    assert score_calculator.position_confidence(5, None) == 1.0  # a pre-v6.27 section
    assert score_calculator.position_confidence(5, {}) == 1.0  # no calibration
