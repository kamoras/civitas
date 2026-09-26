"""Constituent Alignment (v6.14): measured, no safe-seat scaling, peaked.

Each design choice here was decided by testing it against U.S. House
re-election results (docs/research/constituent-alignment.md):

- The seat's expected break rate is measured from the chamber every run,
  per party (compute_constituent_reference), not a hand-set curve.
- Below-expected loyalty scores below neutral (it was held at 50).
- Neither component is scaled by seat safety (both were).
- Position is congress-specific Nokken-Poole, not career DW-NOMINATE.
- Breaking past the saturation deviation lowers the score again (v6.14):
  own-party primary voters punish it and the whole seat stops rewarding it.

conftest pins the reference: expected break rate 10% in a swing seat, 5% in
a maximally safe one, 30% in a maximally opposed one; saturation at a
20-point deviation.
"""

import pytest

from app.pipeline.analyze import score_calculator
from app.pipeline.analyze.population_reference import CONSTITUENT_REFERENCE
from app.pipeline.analyze.score_calculator import (
    _calc_constituent_alignment,
    _constituent_alignment_core,
    compute_constituent_reference,
    constituent_reference_inputs,
    party_break_rate,
)
from app.pipeline.fetch.voteview import _position_column, build_chamber_ideal_points


@pytest.fixture(autouse=True)
def seats(monkeypatch):
    """SW is a swing state, DS is D+15 (maximally safe for a D, maximally
    opposed for an R). No ideal-point data unless a test adds it."""
    monkeypatch.setattr(score_calculator, "_state_pvi_cache", {"SW": 0, "DS": -15, "AL": 15})
    monkeypatch.setattr(score_calculator, "_district_pvi_cache", {"AL-7": -13})
    monkeypatch.setattr(score_calculator, "_member_ideal_points_cache", {})


def record(breaks, total=100, **extra):
    votes = [
        {"billId": f"b{i}", "votedWithParty": i >= breaks, "policyArea": "JUSTICE", **extra}
        for i in range(total)
    ]
    return {"keyVotes": votes, "recentVotes": []}


def score(rec, state="SW", party="D", **kw):
    return _calc_constituent_alignment(rec, [], {}, state=state, party=party, **kw)


class TestSeatRelativeVotes:
    def test_no_votes_is_neutral(self):
        assert score({"keyVotes": [], "recentVotes": []}) == 50

    def test_matching_the_measured_expectation_is_neutral(self):
        assert score(record(10)) == 50

    def test_loyalty_below_expectation_scores_below_neutral(self):
        # 0% vs 10% expected: half a saturation deviation below, on a loyal
        # side that reaches 0 at four of them -> 50 - 50 * 0.5 / 4.
        assert score(record(0)) == 44

    def test_breaking_more_than_expected_scores_above_neutral(self):
        assert score(record(20)) == 75

    def test_extra_loyalty_costs_less_than_extra_disloyalty(self):
        # v6.14: at equal distance from the seat's norm, loyalty is penalized
        # less than disloyalty past the peak (20-point gap, on the shape).
        from app.pipeline.analyze.score_calculator import _peaked_vote_shape

        assert _peaked_vote_shape(-0.4, 0.2) == pytest.approx(25)
        assert _peaked_vote_shape(0.4, 0.2) == pytest.approx(50)
        assert _peaked_vote_shape(0.6, 0.2) == pytest.approx(0, abs=1e-9)
        assert _peaked_vote_shape(-0.6, 0.2) == pytest.approx(12.5)
        # Loyalty reaches the floor only at four gaps below.
        assert _peaked_vote_shape(-0.8, 0.2) == pytest.approx(0, abs=1e-9)
        assert _peaked_vote_shape(-0.7, 0.2) > 0

    def test_peaks_at_the_chambers_p90_deviation(self):
        assert score(record(30)) == 100
        assert score(record(29)) < 100 and score(record(31)) < 100

    def test_breaking_past_saturation_declines_at_the_rate_it_rose(self):
        # 10% expected, 20-point saturation: 100 at 30%, back to 50 at 50%
        # (twice the saturation deviation), 0 at 70% and beyond.
        assert score(record(40)) == 75 == score(record(20))
        assert score(record(50)) == 50
        assert score(record(70)) == 0 and score(record(90)) == 0

    def test_few_votes_shrink_toward_neutral(self):
        # 10 votes, 7 breaks: 70% against 10% expected is three saturation
        # deviations past it (0 at full confidence), but 10 of the 20 votes
        # full confidence needs pulls it halfway back to 50.
        assert score(record(7, total=10)) == 25
        assert score(record(1, total=10)) == 50
        # From 20 votes on, no shrinkage.
        assert score(record(14, total=20)) == 0

    def test_breakdown_says_when_few_votes_shrink_it(self):
        detail = _constituent_alignment_core(record(7, total=10), [], {}, state="SW", party="D")["components"][0]["detail"]
        assert "only 10 votes" in detail

    def test_full_loyalty_in_an_opposed_seat(self):
        # An R in a D+15 state is expected to break 30%; never breaking is
        # 1.5 gaps below: 50 - 50 * 1.5 / 4.
        assert score(record(0), state="DS", party="R") == 31

    def test_no_safe_seat_discount_on_either_side(self):
        # The same deviation from the seat's expectation scores the same in
        # a safe seat (5% expected) as in a swing seat (10% expected).
        assert score(record(15), state="DS") == score(record(20), state="SW") == 75
        assert score(record(0), state="DS") == score(record(5), state="SW")

    def test_opposed_seat_expects_more_crossing(self):
        # An R in a D+15 state is expected to break 30% of the time.
        assert score(record(30), state="DS", party="R") == 50
        assert score(record(10), state="DS", party="R") == 38  # one gap below

    def test_expectation_is_per_party(self):
        ref = {"senate": {"deviation_p90": 0.2, "statistic": score_calculator.CONSTITUENT_REFERENCE_STATISTIC, "expected": {
            "D": {"a": 0.10, "b": 0.0}, "R": {"a": 0.0, "b": 0.0}}}}
        assert score(record(10), party="D", reference=ref) == 50
        assert score(record(10), party="R", reference=ref) == 75

    def test_a_reference_measured_on_another_statistic_is_not_used(self):
        # A v6.13 reference left on the /data volume (content-weighted rate,
        # thin records included) must not set v6.14's saturation point: the
        # score falls back to the bundled prior (conftest: 20-point
        # saturation, 10% swing-seat expectation).
        import json

        stale = {c: {"expected": {"D": {"a": 0.0, "b": 0.0}}, "deviation_p90": 0.01}
                 for c in ("senate", "house")}
        CONSTITUENT_REFERENCE.live_path.write_text(json.dumps(stale))
        CONSTITUENT_REFERENCE._cache = None
        assert score(record(20)) == 75
        # Passed in, unstamped, it isn't used either.
        assert score(record(20), reference=stale) == 75

    def test_setting_the_statistic_drops_what_was_loaded_under_the_old_one(self):
        # A load() before score_calculator set the stamp must not keep a
        # stale entry usable.
        import json

        stale = {"senate": {"expected": {"D": {"a": 0.0, "b": 0.0}}, "deviation_p90": 0.01}}
        CONSTITUENT_REFERENCE.live_path.write_text(json.dumps(stale))
        stamp = CONSTITUENT_REFERENCE.statistic
        try:
            CONSTITUENT_REFERENCE.statistic = None
            assert CONSTITUENT_REFERENCE.load()["senate"]["deviation_p90"] == 0.01
            CONSTITUENT_REFERENCE.statistic = stamp
            assert CONSTITUENT_REFERENCE.load()["senate"]["deviation_p90"] == 0.2  # bundled
        finally:
            CONSTITUENT_REFERENCE.statistic = stamp

    def test_a_malformed_stored_entry_falls_back_instead_of_crashing(self):
        import json

        CONSTITUENT_REFERENCE.live_path.write_text(json.dumps({"senate": "x", "house": [1, 2]}))
        CONSTITUENT_REFERENCE._cache = None
        assert score(record(20)) == 75  # the bundled prior

    def test_no_usable_reference_scores_neutral(self):
        import json

        stale = {c: {"expected": {"D": {"a": 0.0, "b": 0.0}}, "deviation_p90": 0.01}
                 for c in ("senate", "house")}
        CONSTITUENT_REFERENCE.live_path.write_text(json.dumps(stale))
        CONSTITUENT_REFERENCE.bundled_path.write_text(json.dumps(stale))
        CONSTITUENT_REFERENCE._cache = None
        core = _constituent_alignment_core(record(20), [], {}, state="SW", party="D")
        assert core["score"] == 50 and "no measured expectation" in core["components"][0]["detail"]

    def test_breakdown_does_not_call_the_preset_prior_a_measurement(self):
        preset = {c: {"expected": {"D": {"a": 0.10, "b": -0.05, "b_opposed": -0.15}},
                      "deviation_p90": 0.2, "n": None,
                      "statistic": score_calculator.CONSTITUENT_REFERENCE_STATISTIC}
                  for c in ("senate", "house")}
        detail = _constituent_alignment_core(record(45), [], {}, state="SW", party="D",
                                             reference=preset)["components"][0]["detail"]
        assert "preset curve" in detail and "the preset saturation gap" in detail
        assert "members of this chamber" not in detail

    def test_measured_references_carry_the_statistic(self):
        ref = compute_constituent_reference(
            [("D", 0.0, 0.1 + 0.01 * (i % 5)) for i in range(25)]
            + [("R", 0.0, 0.1 + 0.01 * (i % 5)) for i in range(25)]
        )
        assert ref["statistic"] == score_calculator.CONSTITUENT_REFERENCE_STATISTIC

    def test_district_lean_sets_a_house_members_expectation(self):
        # AL-7 is D+13 while Alabama is R+15: a Democrat there holds a safe
        # seat, so 10% breaking is well above what the seat expects.
        with_district = score(record(10), state="AL", district=7)
        state_only = score(record(10), state="AL")
        assert with_district > 50 > state_only

    def test_unknown_district_falls_back_to_state(self):
        assert score(record(10), state="AL", district=99) == score(record(10), state="AL")

    def test_an_independent_is_scored_against_their_caucus(self):
        rec = {**record(10), "effectiveParty": "D"}
        assert score(rec, party="I") == score(record(10), party="D")

    def test_no_caucus_no_expectation_neutral(self):
        core = _constituent_alignment_core(record(10), [], {}, state="SW", party="I")
        assert core["score"] == 50
        assert "no measured expectation" in core["components"][0]["detail"]

    def test_each_party_labeled_roll_call_counts_once_unweighted(self):
        # v6.14: partyAlignmentWeight is the bill's content lean, not how
        # the roll call split; weighting by it made a content-bipartisan
        # bill (0.0, read as 1.0) outweigh a 0.01-lean one a hundredfold.
        rec = {"keyVotes": [
            {"billId": "a", "votedWithParty": False, "partyAlignmentWeight": 1.0},
            {"billId": "b", "votedWithParty": True, "partyAlignmentWeight": 0.0},
            {"billId": "c", "votedWithParty": True, "partyAlignmentWeight": 0.01},
        ], "recentVotes": []}
        assert party_break_rate(rec) == (pytest.approx(1 / 3), 3)

    def test_malformed_vote_entries_are_skipped(self):
        # A None left by a partial normalize must not crash the break rate
        # (or calculate_confidence, which counts through it).
        rec = record(10)
        rec["recentVotes"] = [None, "junk"]
        assert party_break_rate(rec) == (0.1, 100)
        assert score_calculator.calculate_confidence({"votingRecord": rec})["constituentAlignment"] == "high"

    def test_each_roll_call_counts_once(self):
        rec = record(10)
        rec["recentVotes"] = [dict(v) for v in rec["keyVotes"] if not v["votedWithParty"]]
        assert party_break_rate(rec) == (0.1, 100)

    def test_nominations_count_like_legislation(self):
        assert score(record(10, stance="nomination")) == score(record(10, stance="neutral"))

    def test_lobbying_matches_do_not_affect_it(self):
        lobbying = [{"donationToSenator": 1_000_000, "isConsensusVote": False}]
        assert _calc_constituent_alignment(record(20), lobbying, {}, state="SW", party="D") == \
            score(record(20))

    def test_rises_to_saturation_then_falls(self):
        rising = [score(record(b)) for b in (0, 5, 10, 20, 30)]
        falling = [score(record(b)) for b in (30, 40, 50, 60)]
        assert rising == sorted(rising) and falling == sorted(falling, reverse=True)

    def test_breakdown_says_when_past_saturation(self):
        past = _constituent_alignment_core(record(45), [], {}, state="SW", party="D")["components"][0]["detail"]
        within = _constituent_alignment_core(record(25), [], {}, state="SW", party="D")["components"][0]["detail"]
        assert "breaking further lowers the score" in past
        # The turning point as a rate, and the gap in points, not "20% above 10%".
        assert "more than 30.0% (20.0 points above that)" in past
        assert "breaking further" not in within

    def test_breakdown_names_the_comparison(self):
        detail = _constituent_alignment_core(record(20), [], {}, state="SW", party="D")["components"][0]["detail"]
        assert "20.0% of 100" in detail and "break on 10.0%" in detail


class TestMeasuredReference:
    def _members(self, party, n=40, a=0.1, b=-0.05, c=-0.15, opposed=True):
        out = []
        for i in range(n):
            al = (i / (n - 1)) * 2 - 1 if opposed else i / (n - 1)
            noise = 0.01 if i % 2 else -0.01
            out.append((party, al, a + b * al + c * min(al, 0.0) + noise))
        return out

    def test_recovers_the_chambers_kinked_expectation(self):
        ref = compute_constituent_reference(self._members("D") + self._members("R", a=0.05))
        d, r = ref["expected"]["D"], ref["expected"]["R"]
        assert (d["a"], d["b"], d["b_opposed"]) == pytest.approx((0.1, -0.05, -0.15), abs=2e-3)
        assert r["a"] == pytest.approx(0.05, abs=2e-3)

    def test_no_bend_without_enough_opposed_seats(self):
        ref = compute_constituent_reference(self._members("D", opposed=False) + self._members("R"))
        assert ref["expected"]["D"]["b_opposed"] == 0.0

    def test_both_parties_or_neither(self):
        assert compute_constituent_reference(self._members("D") + self._members("R", n=10)) is None

    def test_saturation_is_the_p90_deviation(self):
        members = [("D", 0.0, 0.1 + (0.01 if i % 2 else -0.01) * (i % 10)) for i in range(40)]
        members += [(p, al, br) for p, al, br in self._members("R")]
        ref = compute_constituent_reference(members)
        assert 0 < ref["deviation_p90"] <= 0.09

    def test_inputs_are_what_the_score_compares(self):
        members = [
            {"state": "SW", "party": "D", "votingRecord": record(20)},
            {"state": "DS", "party": "I", "votingRecord": {**record(10), "effectiveParty": "D"}},
            {"state": "SW", "party": "I", "votingRecord": record(10)},  # no caucus: excluded
            {"state": "SW", "party": "R", "votingRecord": record(1, total=2)},  # too few votes
            # Scorable but thin: shrunk in its own score, and kept out of the
            # saturation point everyone else is measured against.
            {"state": "SW", "party": "R", "votingRecord": record(5, total=10)},
        ]
        assert constituent_reference_inputs(members) == [("D", 0.0, 0.2), ("D", 1.0, 0.1)]

    def test_pipeline_persists_this_runs_reference(self):
        from app.pipeline.live_references import live_constituent_reference_measured

        members = [{"state": "SW", "party": p, "votingRecord": record(10 + i % 5)}
                   for p in ("D", "R") for i in range(25)]
        merged, measured = live_constituent_reference_measured("house", members)
        assert measured and merged["house"]["n"] == 50
        assert CONSTITUENT_REFERENCE.load()["house"]["n"] == 50
        assert merged["senate"]["deviation_p90"] == 0.2  # untouched

    def test_too_few_members_keeps_the_last_reference(self):
        from app.pipeline.live_references import live_constituent_reference_measured

        merged, measured = live_constituent_reference_measured("senate", [{"state": "SW", "party": "D", "votingRecord": record(10)}])
        assert not measured and merged == CONSTITUENT_REFERENCE.load()


class TestPositionCongruence:
    """Synthetic D fit: expected dim1 = -0.35 + 0.006 * seat PVI, saturation
    0.2. Vote component held at 50 (break rate at expectation)."""

    @pytest.fixture(autouse=True)
    def ideal_points(self, monkeypatch):
        def patch(members):
            monkeypatch.setattr(score_calculator, "_member_ideal_points_cache", {"senate": {
                "members": members,
                "fit": {"D": {"a": -0.35, "b": 0.006}, "R": {"a": 0.35, "b": 0.006}},
                "extremity_p90": 0.2,
                "measure": "Nokken-Poole",
            }})
        self.patch = patch

    def test_flank_ward_scores_below_neutral(self):
        self.patch({"X1": -0.55})
        assert score(record(10), bioguide_id="X1") == 35  # 50*0.7 + 0*0.3

    def test_center_ward_scores_above_neutral(self):
        self.patch({"X1": -0.15})
        assert score(record(10), bioguide_id="X1") == 65

    def test_no_safe_seat_scaling(self):
        # DS is D+15: expected dim1 -0.44. The same 0.2 flank-ward and
        # center-ward residuals score exactly as they do in a swing seat.
        self.patch({"F": -0.64, "C": -0.24})
        assert score(record(5), state="DS", bioguide_id="F") == 35
        assert score(record(5), state="DS", bioguide_id="C") == 65

    def test_symmetric(self):
        self.patch({"F": -0.40, "C": -0.30})
        assert score(record(10), bioguide_id="F") - 50 == 50 - score(record(10), bioguide_id="C")

    def test_missing_data_skips_the_component(self):
        self.patch({})
        core = _constituent_alignment_core(record(10), [], {}, state="SW", party="D", bioguide_id="X1")
        assert [c["label"] for c in core["components"]] == ["Seat-relative vote alignment"]
        assert core["components"][0]["weight"] == 1.0

    def test_breakdown_weights_and_measure(self):
        self.patch({"X1": -0.15})
        core = _constituent_alignment_core(record(10), [], {}, state="SW", party="D", bioguide_id="X1")
        by_label = {c["label"]: c for c in core["components"]}
        assert by_label["Seat-relative vote alignment"]["weight"] == 0.7
        assert by_label["Position congruence"]["weight"] == 0.3
        assert by_label["Position congruence"]["detail"].startswith("Nokken-Poole dim1")


class TestPositionMeasure:
    def _rows(self, with_np=True):
        return [
            {"bioguide_id": f"B{i}", "nominate_dim1": "0.3", "nokken_poole_dim1": "0.5" if with_np else "",
             "party_code": "200", "state_abbrev": "SW", "district_code": "0"}
            for i in range(10)
        ]

    def test_prefers_congress_specific_nokken_poole(self):
        assert _position_column(self._rows()) == ("nokken_poole_dim1", "Nokken-Poole")

    def test_whole_chamber_falls_back_when_unpublished(self):
        rows = self._rows()
        for r in rows[:2]:
            r["nokken_poole_dim1"] = ""
        assert _position_column(rows) == ("nominate_dim1", "DW-NOMINATE")
        assert _position_column(self._rows(with_np=False))[0] == "nominate_dim1"

    def test_build_records_the_measure_and_uses_its_values(self):
        data, _ = build_chamber_ideal_points(self._rows(), "senate", {"SW": 0}, {})
        assert data["measure"] == "Nokken-Poole"
        assert set(data["members"].values()) == {0.5}


def test_removed_rules_stay_removed():
    for name in ("CROSSING_QUALITY_DISCOUNT", "POSITION_MISMATCH_MAX_PENALTY", "_party_ideology_bounds"):
        assert not hasattr(score_calculator, name), name
    with pytest.raises(TypeError):
        _calc_constituent_alignment(record(10), [], {}, ideology_score=0.1)


class TestIndependentsInTheBreakdown:
    """The breakdown API rebuilds the scoring input from the database; it
    used to set effectiveParty to None, so an Independent's breakdown was
    scored as belonging to no party while their score used their caucus."""

    def test_validator_keeps_the_caucus(self):
        from app.pipeline.assemble.validator import validate_senator

        s = validate_senator({"id": "x", "name": "N", "state": "VT", "party": "I",
                              "votingRecord": {"effectiveParty": "D"}})
        assert s["votingRecord"]["effectiveParty"] == "D"

    def test_breakdown_entity_reads_the_stored_caucus(self, db_session):
        from app.models import Senator
        from app.services._scorecard_common import build_score_breakdown_entity

        db_session.add(Senator(id="s1", name="N", state="VT", party="I", caucus_party="D"))
        db_session.commit()
        entity = build_score_breakdown_entity(
            db_session.get(Senator, "s1"), lobbying_donation_attr="donation_to_senator",
        )
        assert entity["votingRecord"]["effectiveParty"] == "D"


def test_votes_without_an_identity_are_not_collapsed():
    from app.pipeline.transform.normalize_votes import dedupe_votes

    votes = [{"votedWithParty": True}, {"votedWithParty": False}, {"billId": "a"}, {"billId": "a"}]
    assert len(dedupe_votes(votes)) == 3
