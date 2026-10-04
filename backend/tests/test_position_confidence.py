"""The reliability weight on a congress-specific roll-call position (v6.27):
scripts/calibrate_position_confidence.py measures it from Voteview's own
positions and writes app/data/position_confidence.json, which the scorer
reads. One n0 per chamber (or one for both, whichever predicts held-out
members better), so nothing in it follows the sitting Congress."""

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


def _row(icpsr, party, x, n, dim2="0.1", state="OH", career=""):
    return {"icpsr": icpsr, "party_code": party, "nokken_poole_dim1": x, "nokken_poole_dim2": dim2,
            "nominate_number_of_votes": n, "state_abbrev": state, "nominate_dim1": career}


def test_only_a_no_count_position_with_a_career_is_calibrated_as_uncounted(tmp_path, monkeypatch):
    """The score weights a no-count position with a career DW-NOMINATE
    position by uncounted_weight and reads one with neither as no votes, so
    only the first kind enters the calibration's no-count sample."""
    script = _script()
    full = [_row(str(i), "100" if i % 2 else "200", f"{0.1 * (i % 5) - (0.4 if i % 2 else -0.4):.2f}", "500",
                 career="0.3") for i in range(40)]
    early = full + [_row("90", "100", "-0.5", "", career="-0.4"), _row("91", "100", "-0.6", "")]
    late = full + [_row("90", "100", "-0.5", "500", career="-0.4"), _row("91", "100", "-0.6", "500")]
    rows = {101: early, 102: late}
    monkeypatch.setattr(script, "member_rows", lambda chamber, congress, cache=None: rows[congress])
    monkeypatch.setattr(script, "_usable", lambda chamber, congress, cache: True)
    monkeypatch.setattr(script, "party_line_share", lambda chamber, congress, cache=None: 0.5)
    data, _ = script.pairs(None, range(101, 103))
    kinds = {r[1]: r[5] for r in data if r[0] == "S"}
    assert kinds["90"] == "uncounted" and "91" not in kinds


def test_fit_recovers_n0_with_a_drift_per_transition():
    """Pairs generated from full = drift[c] * min(1, w(n) / w(200)) * thin,
    n0 = 60, with very different drift in two transitions: the fit recovers
    n0 (drift from each transition's full pairs, which count 1), and the half
    point follows from it."""
    script = _script()
    data = []
    for c, drift in ((105, 0.66), (115, 1.0)):
        data += [("H", f"F{c}{i}", 600.0, x, drift * x, "full", c, 0.5, False)
                 for i, x in enumerate((-0.2, -0.1, 0.1, 0.3))]
        data += [("H", f"T{c}{n}{i}", float(n), x, drift * float(script.relative_weight(n, 60)) * x, "thin", c, 0.5,
                  False) for n in (5, 20, 60, 150) for i, x in enumerate((-0.3, 0.2, 0.4))]
    assert script.fit_n0(data) == 60.0
    assert abs(float(script.relative_weight(script.half_point(60), 60)) - 0.5) < 1e-9
    # A thin pair in a transition with no full pairs has no drift and is left out.
    assert script.fit_n0(data + [("H", "X", 5.0, 0.4, 0.0, "thin", 130, 0.5, False)]) == 60.0


def test_a_departing_members_pair_uses_the_reverse_drift():
    """Full pairs where the later position is 0.8 of the earlier: forward
    drift 0.8, reverse 1.25. A departing member's (thin later) full earlier
    record is predicted from the thin one with the reverse."""
    script = _script()
    full = [("H", f"F{i}", 600.0, x, 0.8 * x, "full", 110, 0.5, False) for i, x in enumerate((-0.3, 0.1, 0.4))]
    forward, reverse = script.drifts(full)[("H", 110)]
    assert abs(forward - 0.8) < 1e-12 and abs(reverse - 1.25) < 1e-12
    thin = [("H", f"T{i}", 60.0, x, 1.25 * float(script.relative_weight(60, 60)) * x, "thin", 110, 0.5, True)
            for i, x in enumerate((-0.3, 0.2, 0.4))]
    assert script.fit_n0(full + thin) == 60.0


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
    assert dev["1"][2] is False  # no career DW-NOMINATE position in these rows
    assert "5" not in dev  # no position
    assert dev["3"][0] == 10.0 and abs(dev["3"][1] - 0.2) < 1e-12  # 0.2 left of the Democrats' center (-0.4)
    assert dev["4"] == (300.0, 0.0, False)  # float-coded party parses
    assert dev["6"][0] == 0.0  # no count reported


def test_a_placeholder_is_not_a_position():
    script = _script()
    assert script.is_placeholder({"nokken_poole_dim1": "0.000", "nokken_poole_dim2": "-0.0"})
    assert not script.is_placeholder({"nokken_poole_dim1": "0.0", "nokken_poole_dim2": "0.3"})


def test_shipped_file_documents_its_source_and_intervals():
    data = json.loads(_DATA.read_text())
    script = _script()
    assert "calibrate_position_confidence.py" in data["_source"]
    # The structure is the simplest within one standard error of the best.
    test = data["structure_test"]
    assert data["structure"] == next(n for n in script.USABLE if test[n]["above_best"] <= test[n]["standard_error"])
    held = data["heldout_error"]
    for chamber in ("senate", "house"):
        c = data["chambers"][chamber]
        lo, hi = data["interval_90"][chamber]["half_weight_votes"]
        assert lo <= c["half_weight_votes"] <= hi <= 100
        assert abs(script.half_point(c["n0"]) - c["half_weight_votes"]) < 0.06
    assert data["pairs"]["thin"] > 50 and data["pairs"]["full"] > 1000
    lo, hi = data["uncounted_weight_leave_one_out"]
    assert lo <= data["uncounted_weight"] <= hi
    # The rejected party-line term predicts held-out members worse.
    assert data["party_line_test"]["heldout_error"] > held[data["structure"]]
    # The rejected independence model's switch point is reported, and the
    # dependence that rejects it: drift flips a side mostly near the center.
    by_distance = list(data["prior_test"]["full_by_distance"].values())
    assert by_distance == sorted(by_distance)
    # The flank rule's switch: the measured one, at most a full record.
    assert data["prior_until_votes"] == _script().prior_until_votes(data["prior_test"]) <= 200


def test_the_congress_range_follows_the_sitting_congress(monkeypatch):
    """No setting to keep up: a rerun in any Congress includes it."""
    from app.config import settings
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 121)
    assert _script().congresses() == range(101, 122)


def test_scorer_reads_the_shipped_file(monkeypatch):
    monkeypatch.setattr(score_calculator, "_position_reliability_cache", None)
    data = json.loads(_DATA.read_text())
    for chamber in ("senate", "house"):
        assert score_calculator._position_reliability(chamber) == {
            "n0": data["chambers"][chamber]["n0"], "half_weight_votes": data["chambers"][chamber]["half_weight_votes"],
            "reference_votes": data["reference_votes"], "uncounted_weight": data["uncounted_weight"],
            "prior_until_votes": data["prior_until_votes"]}
    assert score_calculator._position_reliability("presidency") == {}


def _pairs(n0s, drift=0.9):
    """Pairs generated exactly from each chamber's n0, full pairs at `drift`."""
    script = _script()
    data = []
    for ch, n0 in n0s.items():
        data += [(ch, f"{ch}F{i}", 600.0, x, drift * x, "full", 110, 0.5, False)
                 for i, x in enumerate((-0.2, -0.1, 0.1, 0.3))]
        data += [(ch, f"{ch}T{n}{i}", float(n), x, drift * float(script.relative_weight(n, n0)) * x, "thin", 110, 0.5,
                  False) for n in (5, 20, 60, 150) for i, x in enumerate((-0.3, 0.2, 0.4))]
    return data


def test_held_out_error_decides_whether_the_chambers_differ():
    """Chambers generated with different n0: one per chamber predicts held-out
    members better by more than the noise, and each chamber's is recovered.
    With the same n0 (and noise), the simpler pooled curve is kept."""
    script = _script()
    data = _pairs({"S": 30, "H": 600})
    structure, report = script.choose_structure(data)
    assert structure == "chamber" and report["pooled"]["above_best"] > report["pooled"]["standard_error"]
    assert script.fit_chambers(data, "chamber") == {"senate": 30.0, "house": 600.0}
    rng = __import__("numpy").random.default_rng(1)
    same = [r[:4] + (r[4] + float(rng.normal(0, 0.02)),) + r[5:] if r[5] == "thin" else r
            for r in _pairs({"S": 80, "H": 80})]
    assert script.choose_structure(same)[0] == "pooled"


def test_the_intervals_do_not_depend_on_the_order_of_the_pairs(monkeypatch):
    script = _script()
    monkeypatch.setattr(script, "BOOTSTRAP", 20)
    data = _pairs({"S": 30, "H": 600})
    assert script.bootstrap(data, "chamber") == script.bootstrap(list(reversed(data)), "chamber")


def test_the_independence_crossover_is_where_thin_records_would_match_full_ones():
    """The rejected alternative: full pairs agree in sign 80% of the time
    across a Congress; thin ones from 10% at 10 votes rising with log n.
    Under independence they'd match a last full record where their observed
    agreement reaches 0.8^2 + 0.2^2 = 0.68, near 100 votes."""
    script = _script()
    full = [("H", f"F{i}", 600.0, 0.1, 0.1 if i % 5 else -0.1, "full", 110, 0.5, False) for i in range(100)]
    thin = []
    for n, agree in ((10, 1), (30, 4), (100, 8), (300, 10)):
        thin += [("H", f"T{n}{i}", float(n), 0.1, 0.1 if i < agree else -0.1, "thin", 110, 0.5, False)
                 for i in range(10)]
    assert 50 < script.prior_crossover(full + thin) < 100
    # Agreement that doesn't rise with the count never crosses.
    flat = [("H", f"T{i}", float(10 + i), 0.1, -0.1, "thin", 110, 0.5, False) for i in range(10)]
    assert script.prior_crossover(full + flat) is None


def test_weight():
    """Relative to a full record (reference_votes or more counts 1), in [0, 1]."""
    rel = {"n0": 40.0, "reference_votes": 360.0, "uncounted_weight": 0.2}
    assert score_calculator.position_confidence(40, rel) == 0.5 / 0.9
    assert score_calculator.position_confidence(0, rel) == 0.0
    assert score_calculator.position_confidence(360, rel) == 1.0
    assert score_calculator.position_confidence(900, rel) == 1.0  # capped
    assert score_calculator.position_confidence(None, rel) == 0.2  # no count reported: its measured weight
    assert score_calculator.position_confidence(None, {**rel, "uncounted_weight": -0.1}) == 0.0  # clamped
    assert score_calculator.position_confidence(40, {"n0": 40.0}) == 0.5  # no reference
    assert score_calculator.position_confidence(5, {"n0": -3.0}) == 1.0  # unusable calibration
    assert score_calculator.position_confidence(5, None) == 1.0  # a pre-v6.27 section
    assert score_calculator.position_confidence(5, {}) == 1.0  # no calibration


def test_deviations_can_center_as_the_flank_rule_does():
    """With a weight, each party's center is its weighted mean over every
    member, not the median of its full records."""
    rows = [_row("1", "100", "-0.5", "300"), _row("2", "100", "-0.3", "300"), _row("3", "100", "-0.9", "0")]
    plain = _script().deviations(rows)
    weighted = _script().deviations(rows, lambda votes, career: 1.0 if votes else 0.5)
    assert abs(plain["3"][1] - 0.5) < 1e-12  # 0.5 left of the full records' median (-0.4)
    center = (-0.5 - 0.3 - 0.9 * 0.5) / 2.5
    assert abs(weighted["3"][1] - (center + 0.9)) < 1e-12


def test_the_switch_takes_the_drift_out_within_each_distance_stratum():
    """Full pairs agree 60% near the center and 100% far from it, half and
    half. Thin records near the center are observed at 0.6 p + 0.4 (1 - p),
    so where they look as good as full ones there (60%), they are better
    within their own Congress: the switch comes before the raw crossing.
    No switch when agreement doesn't rise with the count."""
    script = _script()
    full = [("H", f"N{i}", 600.0, 0.01, 0.01 if i % 5 < 3 else -0.01, "full", 110, 0.5, False) for i in range(50)]
    full += [("H", f"F{i}", 600.0, 0.3, 0.3, "full", 110, 0.5, False) for i in range(50)]
    thin = []
    for n, near, far in ((10, 3, 6), (30, 5, 8), (100, 6, 10), (300, 8, 10)):
        thin += [("H", f"T{n}n{i}", float(n), 0.01, 0.01 if i < near else -0.01, "thin", 110, 0.5, False)
                 for i in range(10)]
        thin += [("H", f"T{n}f{i}", float(n), 0.3, 0.3 if i < far else -0.3, "thin", 110, 0.5, False)
                 for i in range(10)]
    switch = script.prior_switch(full + thin)
    assert switch is not None and 10 < switch < 300
    assert script.prior_until_votes({"switch_votes": switch}) == min(switch, 200.0)
    assert script.prior_until_votes({"switch_votes": None}) == 200.0
    flat = [r[:2] + (float(10 + i),) + r[3:] for i, r in enumerate(t for t in thin if t[2] == 10.0)]
    assert script.prior_switch(full + flat) is None
