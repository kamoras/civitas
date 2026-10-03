"""The reliability weight on a congress-specific roll-call position (v6.27):
scripts/calibrate_position_confidence.py measures it from Voteview's own
positions and writes app/data/position_confidence.json, which the scorer
reads. One n0, so nothing in it follows the sitting Congress."""

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


def _row(icpsr, party, x, n, dim2="0.1", state="OH"):
    return {"icpsr": icpsr, "party_code": party, "nokken_poole_dim1": x, "nokken_poole_dim2": dim2,
            "nominate_number_of_votes": n, "state_abbrev": state}


def test_fit_recovers_n0_with_a_drift_per_congress():
    """Pairs generated from full = drift[c] * n / (n + 40) * thin with very
    different drift in two Congresses: the fit recovers n0, which one drift
    for every Congress would confuse with how the eras differ."""
    script = _script()
    data = []
    for c, drift in ((105, 0.66), (115, 1.0)):
        data += [("H", f"F{c}{i}", 600.0, x, drift * 600 / 640 * x, "full", c, 0.5)
                 for i, x in enumerate((-0.2, -0.1, 0.1, 0.3))]
        data += [("H", f"T{c}{n}{i}", float(n), x, drift * n / (n + 40) * x, "thin", c, 0.5)
                 for n in (5, 20, 60, 150) for i, x in enumerate((-0.3, 0.2, 0.4))]
    assert script.fit_n0(data) == 40.0


def test_voteview_party_line_share_counts_opposed_majorities(tmp_path):
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
            _row("6", "100", "-0.4", ""), _row("7", "100", "-0.9", "40", state="VI")]  # a delegate
    dev = _script().deviations(rows)
    assert "7" not in dev
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
    lo, hi = data["interval_90"]["half_weight_votes"]
    assert lo <= data["half_weight_votes"] <= hi
    assert data["pairs"]["thin"] > 50 and data["pairs"]["full"] > 1000
    # The rejected party-line term: no better fit than one n0.
    test = data["party_line_test"]
    assert test["loss_with"] <= test["loss_without"] < 1.01 * test["loss_with"]


def test_the_congress_range_follows_the_sitting_congress(monkeypatch):
    """No setting to keep up: a rerun in any Congress includes it."""
    from app.config import settings
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 121)
    assert _script().congresses() == range(101, 122)


def test_scorer_reads_the_shipped_file(monkeypatch):
    monkeypatch.setattr(score_calculator, "_position_reliability_cache", None)
    data = json.loads(_DATA.read_text())
    assert score_calculator._position_reliability() == {
        "half_weight_votes": data["half_weight_votes"], "full_record_votes": data["full_record_votes"],
        "uncounted_weight": data["uncounted_weight"]}


def test_weight():
    """Relative to a typical full record (the calibration measures thin
    records against full ones in the same Congress), capped at 1."""
    rel = {"half_weight_votes": 40.0, "full_record_votes": 360.0, "uncounted_weight": 0.2}
    assert score_calculator.position_confidence(40, rel) == 0.5 / 0.9
    assert score_calculator.position_confidence(0, rel) == 0.0
    assert score_calculator.position_confidence(360, rel) == 1.0
    assert score_calculator.position_confidence(900, rel) == 1.0  # capped
    assert score_calculator.position_confidence(None, rel) == 0.2  # no count reported: its measured weight
    assert score_calculator.position_confidence(40, {"half_weight_votes": 40.0}) == 0.5  # no reference
    assert score_calculator.position_confidence(5, None) == 1.0  # a pre-v6.27 section
    assert score_calculator.position_confidence(5, {}) == 1.0  # no calibration
