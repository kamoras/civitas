"""Constituent Alignment (v6.13): measured, symmetric, no safe-seat scaling.

Each design choice here was decided by testing it against U.S. House
re-election results (docs/research/constituent-alignment.md):

- The seat's expected break rate is measured from the chamber every run,
  per party (compute_constituent_reference), not a hand-set curve.
- Below-expected loyalty scores below neutral (it was held at 50).
- Neither component is scaled by seat safety (both were).
- Position is congress-specific Nokken-Poole, not career DW-NOMINATE.

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
        # 0% vs 10% expected: half the saturation deviation -> 25.
        assert score(record(0)) == 25

    def test_breaking_more_than_expected_scores_above_neutral(self):
        assert score(record(20)) == 75

    def test_symmetric_around_the_expectation(self):
        assert score(record(20)) - 50 == 50 - score(record(0))

    def test_saturates_at_the_chambers_p90_deviation(self):
        assert score(record(30)) == 100 and score(record(60)) == 100

    def test_no_safe_seat_discount_on_either_side(self):
        # The same deviation from the seat's expectation scores the same in
        # a safe seat (5% expected) as in a swing seat (10% expected).
        assert score(record(15), state="DS") == score(record(20), state="SW") == 75
        assert score(record(0), state="DS") == score(record(5), state="SW")

    def test_opposed_seat_expects_more_crossing(self):
        # An R in a D+15 state is expected to break 30% of the time.
        assert score(record(30), state="DS", party="R") == 50
        assert score(record(10), state="DS", party="R") == 0

    def test_expectation_is_per_party(self):
        ref = {"senate": {"deviation_p90": 0.2, "expected": {
            "D": {"a": 0.10, "b": 0.0}, "R": {"a": 0.0, "b": 0.0}}}}
        assert score(record(10), party="D", reference=ref) == 50
        assert score(record(10), party="R", reference=ref) == 75

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

    def test_vote_weights_are_used(self):
        rec = {"keyVotes": [
            {"billId": "a", "votedWithParty": False, "partyAlignmentWeight": 1.0},
            {"billId": "b", "votedWithParty": True, "partyAlignmentWeight": 0.5},
            {"billId": "c", "votedWithParty": True, "partyAlignmentWeight": 0.5},
        ], "recentVotes": []}
        assert party_break_rate(rec) == (0.5, 3)

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

    def test_monotonic_in_break_rate(self):
        scores = [score(record(b)) for b in (0, 5, 10, 20, 40)]
        assert scores == sorted(scores) and scores[-1] - scores[0] == 75

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
        ]
        assert constituent_reference_inputs(members) == [("D", 0.0, 0.2), ("D", 1.0, 0.1)]

    def test_pipeline_persists_this_runs_reference(self):
        from app.pipeline.senate_pipeline import _live_constituent_reference

        members = [{"state": "SW", "party": p, "votingRecord": record(10 + i % 5)}
                   for p in ("D", "R") for i in range(25)]
        merged = _live_constituent_reference("house", members)
        assert merged["house"]["n"] == 50
        assert CONSTITUENT_REFERENCE.load()["house"]["n"] == 50
        assert merged["senate"]["deviation_p90"] == 0.2  # untouched

    def test_too_few_members_keeps_the_last_reference(self):
        from app.pipeline.senate_pipeline import _live_constituent_reference

        merged = _live_constituent_reference("senate", [{"state": "SW", "party": "D", "votingRecord": record(10)}])
        assert merged == CONSTITUENT_REFERENCE.load()


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
