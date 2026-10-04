"""The reliability weight on a congress-specific roll-call position (v6.27):
scripts/calibrate_position_confidence.py measures it from Voteview's own
positions and writes app/data/position_confidence.json, which the scorer
reads. One n0 for both chambers or one per chamber (the one-standard-error
rule on held-out members), replaced by the latest era's only if it
predicts that era's members better by more than the noise, so nothing in
it follows the sitting Congress."""

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
    monkeypatch.setattr(script, "member_rows", lambda chamber, congress, cache=None: rows.get(congress, []))
    monkeypatch.setattr(script, "attendance", lambda chamber, congress, cache=None: {})
    monkeypatch.setattr(script, "_usable", lambda chamber, congress, cache: True)
    monkeypatch.setattr(script, "party_line_share", lambda chamber, congress, cache=None: 0.5)
    data, _ = script.pairs(None, range(101, 103))
    kinds = {r[1]: r[5] for r in data if r[0] == "S"}
    assert kinds["90"] == "uncounted" and "91" not in kinds



def _pair_rows():
    full = [_row(str(i), "100" if i % 2 else "200", f"{0.1 * (i % 5) - (0.4 if i % 2 else -0.4):.2f}", "500")
            for i in range(40)]
    return {100: full + [_row("71", "100", "-0.5", "500")],
            101: full + [_row("70", "100", "-0.6", "50"), _row("71", "100", "-0.6", "50"),
                         _row("72", "100", "-0.5", "500"), _row("73", "100", "-0.5", "500")],
            102: full + [_row("70", "100", "-0.5", "500"), _row("71", "100", "-0.5", "500"),
                         _row("72", "100", "-0.6", "50"), _row("73", "100", "-0.6", "50")]}


def test_every_thin_record_is_paired_and_the_flank_rules_case_flagged(monkeypatch):
    """Every thin record next to a full one is a pair (the score sees thin
    records from partial service and from absence alike). The tenth field
    flags the flank rule's case: arrived or left, attending as full records
    do; the eleventh, arrived or left at all. 70 arrived; 71 sat in the 100th; 72 and 73 serve on into the 103rd
    at first; once they're gone 73 counts and 72, who missed most of their
    span, doesn't; once the 103rd isn't published a leaver can't be
    checked."""
    import urllib.error
    script = _script()
    rows = _pair_rows()
    rows[103] = rows[101]

    def member_rows(chamber, congress, cache=None):
        if congress not in rows:
            raise urllib.error.HTTPError("u", 404, "Not Found", None, None)
        return rows[congress]
    monkeypatch.setattr(script, "member_rows", member_rows)
    monkeypatch.setattr(script, "attendance", lambda chamber, congress, cache=None: {"72": 0.6})
    monkeypatch.setattr(script, "_usable", lambda chamber, congress, cache: congress in (101, 102))
    monkeypatch.setattr(script, "party_line_share", lambda chamber, congress, cache=None: 0.5)
    data, _ = script.pairs(None, range(101, 103))
    flags = {r[1]: r[9] for r in data if r[0] == "S" and r[5] == "thin"}
    assert set(flags) == {"70", "71", "72", "73"}
    assert {i for i, run in flags.items() if run} == {"70"}
    assert {r[1] for r in data if r[0] == "S" and r[5] == "thin" and r[10]} == {"70"}  # 71 sat before; 72, 73 stay
    rows[103] = rows[100]  # 73 gone by the 103rd: a checked leaver
    data, _ = script.pairs(None, range(101, 103))
    assert {r[1] for r in data if r[0] == "S" and r[5] == "thin" and r[9]} == {"70", "73"}
    assert {r[1] for r in data if r[0] == "S" and r[5] == "thin" and r[10]} == {"70", "72", "73"}  # 72 left, absent
    del rows[103]  # unpublished: 73 can't be checked
    data, _ = script.pairs(None, range(101, 103))
    assert {r[1] for r in data if r[0] == "S" and r[5] == "thin" and r[9]} == {"70"}

    def failing(chamber, congress, cache=None):
        if congress not in rows:
            raise urllib.error.HTTPError("u", 503, "Unavailable", None, None)
        return rows[congress]
    monkeypatch.setattr(script, "member_rows", failing)
    try:
        script.pairs(None, range(101, 103))
    except urllib.error.HTTPError:
        pass
    else:
        raise AssertionError("an outage must stop the run, not switch the check off")


def test_attendance_is_the_share_of_a_members_span_missed(tmp_path):
    (tmp_path / "S150_votes.csv").write_text(
        "congress,chamber,rollnumber,icpsr,cast_code\n" + "".join(
            f"150,Senate,{rc},{i},{c}\n" for rc in range(1, 5) for i, c in ((1, 1), (2, 9 if rc < 4 else 6))))
    assert _script().attendance("S", 150, tmp_path) == {"1": 0.0, "2": 0.75}
    # No row at all is a roll call missed too (a Speaker who doesn't vote).
    (tmp_path / "S151_votes.csv").write_text(
        "congress,chamber,rollnumber,icpsr,cast_code\n151,Senate,1,3,1\n151,Senate,4,3,1\n")
    assert _script().attendance("S", 151, tmp_path) == {"3": 0.5}


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
    # The structure is the forward test's choice: one curve unless an
    # applicable structure predicts the next Congress better by more than
    # the standard error.
    forward = data["forward_test"]
    passing = [n for n in ("chamber", "era") if forward[n]["above"] < -forward[n]["standard_error"]]
    assert data["structure"] == forward["chosen"] == (
        min(passing, key=lambda n: forward[n]["error"]) if passing else "pooled")
    held = data["heldout_error"]
    for chamber in ("senate", "house"):
        c = data["chambers"][chamber]
        lo, hi = data["interval_90"][chamber]["half_weight_votes"]
        assert lo <= c["half_weight_votes"] <= hi <= 100
        assert abs(script.half_point(c["n0"]) - c["half_weight_votes"]) < 0.06
    assert data["pairs"]["thin"] > 50 and data["pairs"]["full"] > 1000
    lo, hi = data["uncounted_weight_leave_one_out"]
    assert lo <= data["uncounted_weight"] <= hi
    # The rejected party-line term predicts held-out members no better
    # than the shipped curve by more than the noise.
    plt = data["party_line_test"]
    assert plt["above"] >= -plt["standard_error"]
    # Era, direction and attendance are tested and reported beside them.
    assert {"era", "direction", "attendance"} <= set(held)
    assert {"rule_shape", "leavers", "all"} <= set(data["prior_test"]["switch_test"])
    # The rejected independence model's switch point is reported, and the
    # dependence that rejects it: drift flips a side mostly near the center.
    by_distance = list(data["prior_test"]["full_by_distance"].values())
    assert by_distance == sorted(by_distance)
    # The flank rule's switch: a full record unless a shorter one is shown
    # to save sides out of bag on pairs shaped like the rule's case.
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


def _switch_pairs(near_rates, far_rates, counts=(10, 30, 100, 300)):
    """Full pairs agreeing 60% near the party's center, 100% far from it,
    half and half; ten thin pairs per count and stratum, agreeing as given."""
    full = [("H", f"N{i}", 600.0, 0.01, 0.01 if i % 5 < 3 else -0.01, "full", 110, 0.5, True) for i in range(50)]
    full += [("H", f"F{i}", 600.0, 0.3, 0.3, "full", 110, 0.5, True) for i in range(50)]
    thin = []
    for n, near, far in zip(counts, near_rates, far_rates):
        thin += [("H", f"T{n}n{i}", float(n), 0.01, 0.01 if i < near else -0.01, "thin", 110, 0.5, True)
                 for i in range(10)]
        thin += [("H", f"T{n}f{i}", float(n), 0.3, 0.3 if i < far else -0.3, "thin", 110, 0.5, True)
                 for i in range(10)]
    return full + thin


def test_the_switch_takes_the_drift_out_within_each_distance_stratum():
    """Thin records near the center are observed at 0.6 p + 0.4 (1 - p):
    where they look as good as full ones (60%), they are better within their
    own Congress. With the drift taken out the best switch comes earlier
    than without it (the observed rates read as own-Congress ones)."""
    script = _script()
    data = _switch_pairs((3, 5, 6, 8), (6, 8, 10, 10))
    model = script.switch_model(data)
    switch = script.best_switch(model)
    last, own = model
    raw = min(range(1, 201), key=lambda s: float(sum((1 - last) if n < s else (1 - a)
              for n, a in zip(script.SWITCH_COUNTS, _observed(script, data)))))
    assert switch < raw
    assert script.misplaced(model, switch) <= script.misplaced(model, 200)
    # No switch beats a full record when agreement doesn't rise with the count.
    flat = _switch_pairs((3, 3, 3, 3), (6, 6, 6, 6))
    flat_model = script.switch_model(flat)
    assert flat_model is None or script.best_switch(flat_model) == 200


def _observed(script, data):
    """The fitted observed (cross-Congress) agreement at each count, in the
    full pairs' mix: the model without the drift taken out."""
    import numpy as np
    full = [r for r in data if r[5] == "full"]
    thin = [r for r in data if r[5] == "thin"]
    k = len(script.SWITCH_STRATA)
    mix = np.array([sum(script._stratum(r[3]) == s for r in full) for s in range(k)], float) / len(full)
    x = np.column_stack([np.array([[script._stratum(r[4]) == s for s in range(k)] for r in thin], float),
                         np.log([r[2] for r in thin])])
    y = np.array([script._same_side(r) for r in thin], float)
    b = np.zeros(k + 1)
    for _ in range(100):
        p = 1 / (1 + np.exp(-x @ b))
        b += np.linalg.solve(x.T @ (x * (p * (1 - p))[:, None]) + 1e-6 * np.eye(k + 1), x.T @ (y - p) - 1e-6 * b)
    present = mix > 0
    a = 1 / (1 + np.exp(-(b[:k][None, present] + b[-1] * np.log(script.SWITCH_COUNTS)[:, None])))
    return a @ (mix[present] / mix[present].sum())


def test_a_switch_short_of_a_full_record_must_save_sides_out_of_bag(monkeypatch):
    """prior_until_votes is a full record unless the rule-shaped pairs'
    switch, chosen on resampled members, saves sides on the members left
    out in 95% of resamples."""
    script = _script()
    monkeypatch.setattr(script, "BOOTSTRAP", 40)
    strong = script.switch_test(_switch_pairs((6, 9, 10, 10), (8, 10, 10, 10)))
    assert strong["switch_votes"] < 200 and strong["saved"] > 0
    adopted = {**strong, "saved_out_of_bag": [0.01, 0.02, 0.03]}
    assert script.prior_until_votes({"switch_test": {"rule_shape": adopted}}) == float(strong["switch_votes"])
    assert strong["thin_pairs"] == 80 and strong["thin_pairs_over_100"] == 20
    weak = {"switch_votes": 150, "saved": 0.001, "saved_out_of_bag": [-0.01, 0.0, 0.001], "share_saving": 0.4}
    assert script.prior_until_votes({"switch_test": {"rule_shape": weak}}) == 200.0
    assert script.prior_until_votes({"switch_test": {"rule_shape": None}}) == 200.0


def test_the_out_of_bag_test_judges_on_the_members_left_out(monkeypatch):
    """Strong evidence for an early switch saves sides on left-out members
    in most draws; with too few thin members to model any left-out set,
    nothing is judged and no switch is adopted."""
    script = _script()
    monkeypatch.setattr(script, "BOOTSTRAP", 40)
    strong = script.switch_test(_switch_pairs((6, 9, 10, 10), (8, 10, 10, 10)))
    assert strong["draws_judged"] > 20 and strong["share_saving"] > 0.5
    assert strong["saved_out_of_bag"][1] > 0
    few = [r for r in _switch_pairs((6, 9, 10, 10), (8, 10, 10, 10)) if r[5] == "full"]
    few += [("H", f"T{i}", float(10 * (i + 1)), 0.3, 0.3, "thin", 110, 0.5, True) for i in range(5)]
    sparse = script.switch_test(few)
    assert sparse is not None and sparse["draws_judged"] < 40
    if sparse["draws_judged"] == 0:
        assert sparse["saved_out_of_bag"] is None
    assert script.prior_until_votes({"switch_test": {"rule_shape": {**sparse, "saved_out_of_bag": None}}}) == 200.0


def test_the_frontend_quotes_the_shipped_figures():
    """The about page and the v6.27 changelog entry state the half point,
    the full-strength count and the flank rule's switch in prose; a rerun
    that moves one must move them too."""
    src = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
    about = src / "app" / "about" / "scores" / "page.tsx"
    versions = src / "lib" / "scoreVersions.ts"
    if not about.exists():  # the backend image ships without the frontend
        return
    data = json.loads(_DATA.read_text())
    half = round(data["chambers"]["house"]["half_weight_votes"])
    full = round(data["reference_votes"])
    switch = round(data["prior_until_votes"])
    # The prose calls the switch a full record and a convention; a rerun
    # that measures a shorter one must rewrite it, so this fails until then.
    assert data["prior_until_votes"] == data["reference_votes"], "rewrite the prose that calls the switch a full record"
    page = " ".join(about.read_text().split())
    entry = " ".join(versions.read_text().split())
    assert f"about {half} votes counts half" in page
    assert f"{full} or more counts in full" in page
    assert f"once it rests on {switch} roll calls" in page
    assert f"about {half} votes counts half" in entry
    assert f"{full} votes or more counts in full" in entry
    assert f"until the new one is full ({switch} votes)" in entry
    # AGENTS.md and README quote the forward test's margin.
    root = src.parents[1]
    agents = " ".join((root / "AGENTS.md").read_text().split())
    readme = " ".join((root / "README.md").read_text().split())
    chosen = data["forward_test"][data["structure"]]
    last = chosen["last_three"]
    assert f"by {abs(chosen['above']):.3f} (standard error {chosen['standard_error']:.3f};" in agents
    assert round(abs(chosen["above"]) / chosen["standard_error"]) == 2
    assert round(abs(last["above"]) / last["standard_error"]) == 1
    assert "over the last three transitions alone the gain is about one standard error" in agents
    assert "by about two standard errors overall, about one over the last three transitions alone" in readme
    # ... and the flank rule's switch and where a full record starts.
    assert f"(`app/data/position_confidence.json`: {switch}, a full record)" in agents
    assert f"(`prior_until_votes`, {switch} votes, a convention)" in readme
    assert f"({full} votes or more, a convention)" in readme
    # A position published with no count: "about a fifth" in the entry.
    assert round(data["uncounted_weight"] * 5) == 1 and "about a fifth" in entry
    # The era the shipped curve is measured on, by its first year. Both texts
    # describe the era curve; a rerun choosing another structure must
    # rewrite them (and README and AGENTS.md), so this fails until it does.
    assert data["structure"] == "era", "rewrite the prose that describes the era curve"
    year = 1789 + 2 * (data["era_split"] - 1)
    assert f"since {year}" in page and f"since {year}" in entry


def test_the_switch_tests_take_the_pairs_their_names_say(monkeypatch):
    """rule_shape takes later-thin attended leavers, leavers every later-thin
    member who left (attended or not, never one who stayed), all every
    counted pair; full pairs go to each, uncounted ones to none."""
    script = _script()
    seen = {}
    calls = iter(("rule_shape", "leavers", "all"))
    monkeypatch.setattr(script, "switch_test", lambda rows: seen.__setitem__(next(calls), {r[1] for r in rows}))
    monkeypatch.setattr(script, "prior_crossover", lambda rows: None)

    def row(i, kind, later, run, moved):
        return ("H", i, 50.0, 0.2, 0.3, kind, 110, 0.5, later, run, moved)

    script.prior_test([
        row("full", "full", False, False, False),
        row("run", "thin", True, True, True),
        row("unattended", "thin", True, False, True),
        row("stayer", "thin", True, False, False),
        row("arrival", "thin", False, True, True),
        row("uncounted", "uncounted", True, True, True),
    ])
    assert seen["rule_shape"] == {"full", "run"}
    assert seen["leavers"] == {"full", "run", "unattended"}
    assert seen["all"] == {"full", "run", "unattended", "stayer", "arrival"}


def test_a_member_who_switched_parties_mid_congress_is_left_out():
    """Voteview lists a mid-Congress party switch under a second ICPSR id
    with the same bioguide id; neither id's partial record is a thin record
    of one member, so both are left out. Members without a bioguide id, or
    with one id, stay."""
    script = _script()
    rows = [_row(str(i), "100" if i % 2 else "200", "0.3" if i % 2 == 0 else "-0.3", "500") for i in range(10)]
    rows += [{**_row("80", "200", "0.1", "161"), "bioguide_id": "X000001"},
             {**_row("81", "100", "-0.3", "428"), "bioguide_id": "X000001"},
             {**_row("82", "100", "-0.4", "500"), "bioguide_id": "Y000002"}]
    kept = script.deviations(rows)
    assert "80" not in kept and "81" not in kept
    assert "82" in kept and "0" in kept


def test_the_era_curve_is_judged_only_where_it_would_apply(monkeypatch):
    """era_test compares the latest era's curve with the chosen structure on
    the latest era's pairs alone: a better fit to an earlier era, whose
    curve is never applied, doesn't count. era_split_test repeats it at
    every split with thin pairs on both sides."""
    script = _script()
    data = [("H", f"M{c}{k}", 50.0, 0.2, 0.2, "thin", c, 0.5, False) for c in (101, 105, 110) for k in range(3)]
    seen = []

    def errors(rows, structure, only=None):
        kept = [r for r in rows if r[5] == "thin" and (only is None or only(r))]
        seen.append((structure, len(kept)))
        # On the late pairs the era curve is better for two members, worse
        # for one: no better than the noise.
        if structure != "_split":
            return {r[1]: 1.0 for r in kept}
        return {r[1]: 1.002 if r[1].endswith("0") else 0.999 for r in kept}
    monkeypatch.setattr(script, "heldout_errors", errors)
    out = script.era_test(data, "pooled", 110)
    assert out["members"] == 3 and not out["adopted"]
    assert all(n == 3 for _, n in seen)  # only the latest era's pairs judged
    assert "_split" not in script.STRUCTURES
    split = script.era_split_test(data, "pooled")
    assert set(split["splits"]) == {"105", "110"} and split["of"] == 2
    assert script.era_test(data[:1], "pooled", 101) is None


def test_the_era_structure_fits_the_latest_era():
    """Adopted, an era structure applies its latest era's curve to both
    chambers: the era every Congress scored from now on falls in."""
    script = _script()
    full = [("H", f"F{c}{k}", 300.0, x, x, "full", c, 0.5, False) for c in (101, 115)
            for k, x in enumerate((-0.3, -0.1, 0.1, 0.3))]
    early = [("H", f"E{k}", 20.0, x, x, "thin", 101, 0.5, False) for k, x in enumerate((-0.3, 0.3))]
    late = [("H", f"L{k}", 20.0, x, x / 3, "thin", 115, 0.5, False) for k, x in enumerate((-0.3, 0.3))]
    n0 = script.fit_chambers(full + early + late, "era")
    assert n0["senate"] == n0["house"] == script.fit_n0(full + late, script.drifts(full + early + late))


def test_the_switcher_test_compares_the_latest_and_the_longer_record(monkeypatch):
    """A member listed under two ids in a Congress, with a full record in
    the next: the latest record (the id whose first roll call comes last),
    the longer one and the vote-weighted mean are each compared with that
    full position."""
    script = _script()
    rows = {101: [{**_row("10", "200", "0.5", "300"), "bioguide_id": "X1"},
                  {**_row("90", "328", "0.1", "100"), "bioguide_id": "X1"},
                  {**_row("11", "100", "-0.4", "500"), "bioguide_id": "Y1"}],
            102: [{**_row("90", "328", "0.12", "600"), "bioguide_id": "X1"},
                  {**_row("11", "100", "-0.4", "500"), "bioguide_id": "Y1"}]}

    def member_rows(chamber, congress, cache=None):
        if chamber != "H" or congress not in rows:
            raise OSError
        return rows[congress]
    monkeypatch.setattr(script, "member_rows", member_rows)
    monkeypatch.setattr(script, "roll_spans", lambda ch, c, cache=None: {"10": (1, 50), "90": (51, 90)})
    out = script.switcher_test(span=range(101, 103))
    assert out["members"] == out["people"] == 1
    assert out["latest_not_longer"] == out["latest_closer"] == 1
    assert abs(out["latest_minus_longer"]["mean"] - (0.02 ** 2 - 0.38 ** 2)) < 1e-4
    assert abs(out["mean_squared_gap_latest"] - 0.02 ** 2) < 1e-4
    assert abs(out["mean_squared_gap_longer"] - 0.38 ** 2) < 1e-4
    assert abs(out["mean_squared_gap_weighted"] - 0.28 ** 2) < 1e-4


def test_the_pipeline_reads_the_switcher_record_the_evidence_allows():
    """voteview reads a switcher's latest record, a stated choice; the
    shipped measurement must not show either other reading predicting
    better by more than the noise."""
    test = json.loads(_DATA.read_text()).get("switcher_test")
    if test and test["members"] > 1:
        for other in ("latest_minus_longer", "latest_minus_weighted"):
            assert test[other]["mean"] <= test[other]["standard_error"]


def test_calibrate_ships_the_forward_tests_choice(monkeypatch):
    """calibrate ships the structure forward_test chooses, fitting the
    latest era's curve for both chambers when it is "era"; era_split_test
    is judged against the one-standard-error rule's own choice."""
    script = _script()
    full = [("H", f"F{c}{k}", 300.0, x, x, "full", c, 0.5, False, False, False) for c in (101, 115)
            for k, x in enumerate((-0.3, -0.1, 0.1, 0.3))]
    thin = [("H", f"E{k}", 20.0, x, x, "thin", 101, 0.5, False, False, False) for k, x in enumerate((-0.3, 0.3))]
    thin += [("H", f"L{k}", 20.0, x, x / 3, "thin", 115, 0.5, False, False, False) for k, x in enumerate((-0.3, 0.3))]
    data = full + thin
    monkeypatch.setattr(script, "pairs", lambda cache, span, weight=None, shares=None: (data, {"S": {}, "H": {}}))
    monkeypatch.setattr(script, "choose_structure", lambda d: ("pooled", {}))
    monkeypatch.setattr(script, "heldout_errors", lambda d, s, only=None: {"L0": 1.0, "L1": 1.0})
    monkeypatch.setattr(script, "heldout_error", lambda d, s: 0.0)
    monkeypatch.setattr(script, "era_test", lambda d, s, split=110: None)
    split_base = []
    monkeypatch.setattr(script, "era_split_test", lambda d, s: split_base.append(s) or {})
    for name in ("party_line_test", "trend_test", "prior_test", "switcher_test"):
        monkeypatch.setattr(script, name, lambda *a, **k: {})
    monkeypatch.setattr(script, "bootstrap", lambda *a, **k: {"house": {"half_weight_votes": [0.0, 0.0]}})
    monkeypatch.setattr(script, "prior_until_votes", lambda t: 200.0)
    for chosen in ("era", "pooled"):
        monkeypatch.setattr(script, "forward_test", lambda d, c=chosen: {"chosen": c})
        out = script.calibrate()
        assert out["structure"] == chosen and split_base[-1] == "pooled"
        assert out["chambers"]["house"]["n0"] == script.fit_chambers(data, chosen)["house"]
    assert script.fit_chambers(data, "era") != script.fit_chambers(data, "pooled")


def test_the_forward_test_adopts_a_structure_only_past_the_noise(monkeypatch):
    """forward_test ships one curve unless chamber or era predicts the next
    Congress better by more than its standard error, the better of the two
    if both do; the trend is never chosen."""
    script = _script()
    base = {f"M{i}": 1.0 for i in range(6)}

    def errs(gain, noise=0.0):
        return {m: 1.0 + gain + (noise if i % 2 else -noise) for i, m in enumerate(base)}

    def fake(era, chamber, trend=-1.0):
        table = {"pooled": base, "chamber": chamber, "era": era, "trend": errs(trend), "window": errs(0.0)}

        def forward_errors(d, split=110, names=("pooled", "chamber", "era", "trend"), window=None,
                           predict_from=None):
            return {n: table[n] for n in names}, {n: {110: sum(table[n].values())} for n in names}
        return forward_errors
    for era, chamber, chosen in ((errs(-0.1), errs(0.0), "era"), (errs(-0.01, 0.2), errs(0.0), "pooled"),
                                 (errs(0.0), errs(-0.1), "chamber"), (errs(-0.05), errs(-0.1), "chamber"),
                                 (errs(-0.1), errs(-0.05), "era")):
        monkeypatch.setattr(script, "forward_errors", fake(era, chamber))
        out = script.forward_test([("H", "M0", 20.0, 0.1, 0.1, "thin", 105, 0.5, False)])
        assert out["chosen"] == chosen
        if chosen != "pooled":
            assert len(out[chosen]["without_most_helped_refitted"]) == 3
            assert out[chosen]["last_three"]["above"] == round(sum(era.values() if chosen == "era" else chamber.values())
                                                                   - sum(base.values()), 4)
        assert out["window_against_era"] is not None


def test_the_trend_term_finds_n0_rising_with_time():
    """fit_party_line with the decades term: thin records that predict full
    ones in full early and only weakly late give n0 rising with time (b >
    0); trend_test reports it beside the paired comparisons."""
    script = _script()
    full = [("H", f"F{c}{k}", 300.0, x, x, "full", c, 0.5, False) for c in (101, 116)
            for k, x in enumerate((-0.3, -0.1, 0.1, 0.3))]
    early = [("H", f"E{k}", 20.0, x, x, "thin", 101, 0.5, False) for k, x in enumerate((-0.3, 0.3, -0.2, 0.2))]
    late = [("H", f"L{k}", 20.0, x, x / 4, "thin", 116, 0.5, False) for k, x in enumerate((-0.3, 0.3, -0.2, 0.2))]
    data = full + early + late
    b, _, _ = script.fit_party_line(data, "pooled", script.drifts(data), script._decades)
    assert b > 0
    out = script.trend_test(data, "pooled")
    assert out["b"] == round(b, 2) and out["latest_congress"] == 116
    assert {"all", "latest", "latest_without_most_influential_member"} <= set(out)


def test_an_adopting_split_reports_its_most_influential_member(monkeypatch):
    """At a split where the recent curve would be adopted, era_split_test
    reports its n0, the range with each of the era's thin members left out,
    and the test rerun without the member whose absence moves n0 most; with
    no member moving it, none."""
    script = _script()
    data = [("H", f"M{c}{k}", 50.0, 0.2, 0.2, "thin", c, 0.5, False) for c in (101, 110) for k in range(3)]
    reran = []

    def era_test(rows, structure, split=110):
        reran.append({r[1] for r in rows})
        return {"above": -1.0, "standard_error": 0.1, "members": 3, "adopted": True, "half_weight_votes": 98.0}
    monkeypatch.setattr(script, "era_test", era_test)
    monkeypatch.setattr(script, "drifts", lambda rows: {})
    # On a log scale 300 is further from 5000 than 20000 is.
    fits = {"M1100": 300.0, "M1101": 20000.0, "M1102": 5000.0}
    monkeypatch.setattr(script, "fit_n0", lambda rows, d=None: min(
        (fits[m] for m in fits if m not in {r[1] for r in rows}), default=5000.0))
    out = script.era_split_test(data, "pooled")["splits"]["110"]
    assert out["n0"] == 5000.0 and out["n0_leaving_one_member_out"] == [300.0, 20000.0]
    assert "M1100" not in reran[-1] and out["without_most_influential_member"]["adopted"]
    fits.update(M1100=5000.0, M1101=5000.0)
    assert script.era_split_test(data, "pooled")["splits"]["110"]["without_most_influential_member"] is None


def test_forward_errors_fit_only_on_earlier_transitions(monkeypatch):
    """Each transition is predicted from fits on earlier transitions alone,
    only once at least MIN_TRAIN_TRANSITIONS of them have thin pairs, with
    the predicted transition's own drift in the pair's direction."""
    script = _script()
    data = []
    for c in (101, 103, 105, 107, 109):
        data += [("H", f"F{c}{k}", 300.0, x, x * (0.8 if c == 107 else 1.0), "full", c, 0.5, False)
                 for k, x in enumerate((-0.3, 0.3))]
        data += [("H", f"T{c}", 20.0, 0.2, 0.1, "thin", c, 0.5, c == 107)]
    seen = []
    real = script.fit_chambers

    def fit(rows, structure, split=110):
        seen.append(max(r[6] for r in rows))
        return real(rows, structure, split)
    monkeypatch.setattr(script, "fit_chambers", fit)
    out, by_t = script.forward_errors(data, names=("pooled", "era"))
    assert set(out["pooled"]) == {"T107", "T109"} and set(by_t["pooled"]) == {107, 109}
    assert max(seen) == 107  # never a fit that saw the transition it predicts
    # T107 is a later-thin pair: the reverse drift of the 107th's full pairs.
    n0 = real([r for r in data if r[6] < 107], "pooled")["house"]
    k = script.drifts([r for r in data if r[6] == 107])[("H", 107)][1]
    assert abs(out["pooled"]["T107"] - (0.1 - float(script.relative_weight(20.0, n0)) * k * 0.2) ** 2) < 1e-12
    # predict_from predicts only the later transitions, on the same fits.
    later, later_t = script.forward_errors(data, names=("pooled",), predict_from=109)
    assert set(later["pooled"]) == {"T109"} and later["pooled"]["T109"] == out["pooled"]["T109"]


def test_the_split_sweep_skips_the_split_it_cannot_test(monkeypatch):
    """At the last transition with thin pairs no later one has thin pairs
    to fit the era curve on, so it equals one curve: not a split tested.
    last_three predicts from the third-from-last predicted transition."""
    script = _script()
    calls = []
    members = {f"M{i}": 1.0 for i in range(4)}

    def forward_errors(d, split=110, names=("pooled", "chamber", "era", "trend"), window=None,
                       predict_from=None):
        calls.append((split, names, predict_from))
        gain = -0.5 if predict_from is None else -0.25
        errs = {n: {m: v + (gain if n == "era" else 0.0) + (0.1 if i % 2 else -0.1) * (n == "era")
                    for i, (m, v) in enumerate(members.items())} for n in names}
        return errs, {n: {t: sum(errs[n].values()) for t in range(110, 116)} for n in names}
    monkeypatch.setattr(script, "forward_errors", forward_errors)
    data = [("H", "M0", 20.0, 0.1, 0.1, "thin", c, 0.5, False) for c in (101, 105, 110, 115)]
    out = script.forward_test(data)
    assert set(out["era_at_every_split"]) == {"105", "110"}
    assert out["chosen"] == "era"
    assert (110, ("pooled", "era"), 113) in calls
    assert out["era"]["last_three"]["above"] == -1.0
    # The shipped file: its last thin transition is never a split tested.
    shipped = json.loads(_DATA.read_text())
    last = max(int(t) for t in shipped["forward_test"]["training_before_split"])
    assert str(last) not in shipped["forward_test"]["era_at_every_split"]


def test_shipped_file_reports_what_the_docs_cite():
    """The figures the research note and v6.27 cite are in the file."""
    data = json.loads(_DATA.read_text())
    if data["structure"] != "pooled":  # one curve has nothing to compare with itself
        chosen = data["forward_test"][data["structure"]]
        assert {"above", "standard_error"} <= set(chosen["last_three"])
        shares = chosen["most_helped_share"]
        assert len(shares) == 3 and shares == sorted(shares, reverse=True)
    assert set(data["forward_test"]["training_before_split"]) == set(
        data["forward_test"]["era"].get("gain_by_transition", data["forward_test"]["training_before_split"]))
    assert {"above", "standard_error"} <= set(data["forward_test"]["window_against_era"])
    lo, hi = data["half_weight_votes_pooled_interval_90"]
    assert lo <= data["half_weight_votes_pooled"] <= hi
    assert sum(e["thin"] for e in data["thin_pairs_by_era"].values()) == data["pairs"]["thin"]
    trend = data["forward_test"]["trend"]
    # The docs say the rule wouldn't choose the trend over every transition.
    assert trend["above"] >= -trend["standard_error"]
    # The research note and v6.27 quote the trend from the first transition
    # its forward fits are all inside the grids, and say it predicts worse
    # than one curve before that.
    inside = trend["inside_grids"]
    start = inside["from"]
    assert sum(v for t, v in trend["gain_by_transition"].items() if int(t) < start) > 0
    root = pathlib.Path(__file__).resolve().parents[2]
    research = root / "docs" / "research" / "constituent-alignment.md"
    method = root / "docs" / "methodology" / "member-score" / "v6.27.md"
    if research.exists():  # the backend image ships without the docs
        note, entry = (" ".join(path.read_text().split()) for path in (research, method))

        def quoted(d):
            return f"{abs(d['above']):.3f} (standard error {d['standard_error']:.3f})"
        era, one = inside["against_era"], inside["against_one_curve"]
        overall = f"{abs(trend['above']):.3f} better than one curve (standard error {trend['standard_error']:.3f})"
        assert f"through the {start - 1}th" in note and f"from the {start}th" in note
        assert quoted(one) in note and f"{abs(era['above']):.3f} ({era['standard_error']:.3f})" in note
        assert quoted(era) in note and overall in note
        assert f"From the {start}th" in entry and quoted(era) in entry and overall in entry
    for lo, hi in data["drift_range"].values():
        assert 0 < lo <= hi
    # Full records show no gradient in their count.
    assert all(abs(s - 1) < 0.05 for s in data["full_slope_by_votes"].values())


def test_the_docs_quote_a_five_vote_saturated_score():
    """The research note and v6.27 say what a position at saturation on 5
    votes scores under the shipped curve."""
    from app.pipeline.analyze.score_calculator import position_confidence, position_congruence_score
    data = json.loads(_DATA.read_text())
    rel = {"n0": data["chambers"]["house"]["n0"], "reference_votes": data["reference_votes"]}
    score = round(position_congruence_score(0.3, 0.3, position_confidence(5, rel)))
    root = pathlib.Path(__file__).resolve().parents[2]
    for doc in ("docs/research/constituent-alignment.md", "docs/methodology/member-score/v6.27.md"):
        path = root / doc
        if path.exists():  # the backend image ships without the docs
            assert f"saturation scores about {score} instead of 0" in " ".join(path.read_text().split())
