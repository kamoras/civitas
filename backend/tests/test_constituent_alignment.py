"""Constituent Alignment (v6.16): measured, no safe-seat scaling, peaked
at the seat's expectation, read in standard deviations per vote.

Each design choice here was decided by testing it against election results
(docs/research/constituent-alignment.md):

- The seat's expected break rate is measured from the chamber every run,
  per party (compute_constituent_reference), not a hand-set curve.
- Loyalty below expectation costs (it was held at 50), more gently than
  breaking too often.
- Neither component is scaled by seat safety (both were).
- Position is congress-specific Nokken-Poole, not career DW-NOMINATE.
- The vote score is 100 at the seat's expectation and falls both ways
  (v6.16), with the gap measured in standard deviations per vote, not
  percentage points.

conftest pins the reference: expected break rate 10% in a swing seat, 5% in
a maximally safe one, 30% in a maximally opposed one; scale 0.3 standard
deviations per vote (9 points at the swing seat's 10%). With the shape's
defaults the score reaches 0 at 1.5 scales above the expectation (23.5%
in a swing seat) and 3 below (never reached there: 0% is 1.11 below).
"""

import pytest

from app.config import settings
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
    monkeypatch.setattr(score_calculator, "_district_pvi_stamp", None)
    monkeypatch.setattr(score_calculator, "_member_ideal_points_cache", {})


def record(breaks, total=100, **extra):
    votes = [
        {"billId": f"b{i}", "votedWithParty": i >= breaks, "policyArea": "JUSTICE", **extra}
        for i in range(total)
    ]
    return {"keyVotes": votes, "recentVotes": []}


def score(rec, state="SW", party="D", **kw):
    return _calc_constituent_alignment(rec, [], {}, state=state, party=party, **kw)


def _typical_ref():
    """A measured Senate reference whose D members typically score 85."""
    return {"senate": {"statistic": score_calculator.CONSTITUENT_REFERENCE_STATISTIC, "n": 50, "expected": {
        "D": {"a": 0.10, "b": 0.0, "scale": 0.3, "typical": 85.0}}}}


class TestSeatRelativeVotes:
    def test_no_votes_is_neutral(self):
        assert score({"keyVotes": [], "recentVotes": []}) == 50

    def test_matching_the_measured_expectation_scores_highest(self):
        # v6.16: doing what the seat's same-party members do is doing what
        # the seat elected the member to do.
        assert score(record(10)) == 100
        assert score(record(9)) < 100 and score(record(11)) < 100

    def test_loyalty_below_expectation_costs_gently(self):
        # 0% vs 10% expected: 0.1 / sqrt(0.1 * 0.9) = 0.33 SD per vote below,
        # 1.11 scales, on a loyal side that reaches 0 at 3 -> 63.
        assert score(record(0)) == 63

    def test_breaking_more_than_expected_costs(self):
        # 19% is 0.3 SD per vote above, one scale: 100 * (1 - 1 / 1.5).
        assert score(record(19)) == 33
        assert score(record(24)) == 0 and score(record(40)) == 0

    def test_extra_loyalty_costs_less_than_extra_disloyalty(self):
        from app.pipeline.analyze.score_calculator import _vote_shape

        assert _vote_shape(-0.3, 0.3) == pytest.approx(100 * (1 - 1 / 3))
        assert _vote_shape(0.3, 0.3) == pytest.approx(100 * (1 - 1 / 1.5))
        assert _vote_shape(0.45, 0.3) == pytest.approx(0, abs=1e-9)
        assert _vote_shape(-0.9, 0.3) == pytest.approx(0, abs=1e-9)
        assert _vote_shape(-0.85, 0.3) > 0

    def test_a_gap_counts_for_more_where_the_seat_rarely_breaks(self):
        # v6.16: four extra points on a seat whose members break 10% of the
        # time is a modest departure; 4.5 extra on one whose members break
        # 1.5% is breaking four times as often.
        ref = {"senate": {"deviation_p90": 0.3, "n": 100,
                          "statistic": score_calculator.CONSTITUENT_REFERENCE_STATISTIC, "expected": {
                              "D": {"a": 0.10, "b": 0.0}, "R": {"a": 0.015, "b": 0.0}}}}
        assert score(record(14), party="D", reference=ref) == 70
        assert score(record(6), party="R", reference=ref) == 18

    def test_few_votes_shrink_toward_neutral(self):
        # 10 votes, 7 breaks: 0 at full confidence, but 10 of the 20 votes
        # full confidence needs pulls it halfway back to 50.
        assert score(record(7, total=10)) == 25
        assert score(record(1, total=10)) == 75
        # From 20 votes on, no shrinkage.
        assert score(record(14, total=20)) == 0

    def test_breakdown_says_when_few_votes_shrink_it(self):
        detail = _constituent_alignment_core(record(7, total=10), [], {}, state="SW", party="D")["components"][0]["detail"]
        assert "only 10 votes" in detail

    def test_full_loyalty_in_an_opposed_seat(self):
        # An R in a D+15 state is expected to break 30%; never breaking is
        # 0.3 / sqrt(0.3 * 0.7) = 0.65 SD per vote below, 2.18 scales.
        assert score(record(0), state="DS", party="R") == 27

    def test_no_safe_seat_discount_on_either_side(self):
        # Matching the seat's expectation scores the same in a safe seat (5%)
        # as in a swing seat (10%). Never breaking costs less in the safe
        # seat only because it is fewer standard deviations from what that
        # seat's members do, not by any safe-seat rule.
        assert score(record(5), state="DS") == score(record(10), state="SW") == 100
        assert score(record(0), state="DS") == 75 > score(record(0), state="SW")

    def test_opposed_seat_expects_more_crossing(self):
        # An R in a D+15 state is expected to break 30% of the time.
        assert score(record(30), state="DS", party="R") == 100
        assert score(record(10), state="DS", party="R") == 52

    def test_expectation_is_per_party(self):
        ref = {"senate": {"deviation_p90": 0.3, "statistic": score_calculator.CONSTITUENT_REFERENCE_STATISTIC, "expected": {
            "D": {"a": 0.10, "b": 0.0}, "R": {"a": 0.0, "b": 0.0}}}}
        assert score(record(10), party="D", reference=ref) == 100
        assert score(record(10), party="R", reference=ref) == 0

    def test_an_expectation_of_zero_is_half_a_vote_not_zero(self):
        # A linear fit can predict 0% for the safest seats; the residual keeps
        # p half a vote of the member's record from 0, so one break in 100
        # is a small departure, not an infinite one.
        from app.pipeline.analyze.score_calculator import seat_residual

        assert seat_residual(0.01, 0.0, 100) == pytest.approx(0.005 / (0.005 * 0.995) ** 0.5)
        assert seat_residual(0.0, 0.0, 100) < 0

    def test_a_reference_measured_on_another_statistic_is_not_used(self):
        # A reference measured on another statistic left on the /data volume
        # (v6.15's least-squares fit and point-scale, v6.13's weighted rate)
        # must not set v6.16's scale: the score falls back to the bundled
        # prior (conftest: 0.3 scale, 10% swing-seat expectation).
        import json

        stale = {c: {"expected": {"D": {"a": 0.0, "b": 0.0}}, "deviation_p90": 0.01}
                 for c in ("senate", "house")}
        CONSTITUENT_REFERENCE.live_path.write_text(json.dumps(stale))
        CONSTITUENT_REFERENCE._cache = None
        assert score(record(20)) == 26
        # Passed in, unstamped, it isn't used either.
        assert score(record(20), reference=stale) == 26

    def test_a_malformed_stored_entry_falls_back_instead_of_crashing(self):
        import json

        CONSTITUENT_REFERENCE.live_path.write_text(json.dumps({"senate": "x", "house": [1, 2]}))
        CONSTITUENT_REFERENCE._cache = None
        assert score(record(20)) == 26  # the bundled prior

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
                      "deviation_p90": 0.3, "n": None,
                      "statistic": score_calculator.CONSTITUENT_REFERENCE_STATISTIC}
                  for c in ("senate", "house")}
        detail = _constituent_alignment_core(record(45), [], {}, state="SW", party="D",
                                             reference=preset)["components"][0]["detail"]
        assert "preset expectation" in detail
        assert "members of this chamber" not in detail

    def test_measured_references_carry_the_statistic(self):
        ref = compute_constituent_reference(
            [("D", 0.0, 0.1 + 0.01 * (i % 5), 40) for i in range(25)]
            + [("R", 0.0, 0.1 + 0.01 * (i % 5), 40) for i in range(25)]
        )
        assert ref["statistic"] == score_calculator.CONSTITUENT_REFERENCE_STATISTIC

    def test_district_lean_sets_a_house_members_expectation(self):
        # AL-7 is D+13 while Alabama is R+15: a Democrat there holds a safe
        # seat, expected to break about 5.7%, where the state alone would
        # expect 30%.
        from app.pipeline.analyze.score_calculator import _seat_vote_expectation

        _, with_district, _, _, _ = _seat_vote_expectation("AL", "D", None, 7, None)
        _, state_only, _, _, _ = _seat_vote_expectation("AL", "D", None, None, None)
        assert with_district == pytest.approx(0.1 - 0.05 * 13 / 15)
        assert state_only == pytest.approx(0.30)
        assert score(record(6), state="AL", district=7) > score(record(6), state="AL")

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
        # v6.15: partyAlignmentWeight is the bill's content lean, not how
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

    def test_peaks_at_the_expectation_and_falls_both_ways(self):
        rising = [score(record(b)) for b in (0, 5, 10)]
        falling = [score(record(b)) for b in (10, 15, 20, 25)]
        assert rising == sorted(rising) and falling == sorted(falling, reverse=True)

    def test_breakdown_states_the_gap_in_standard_deviations(self):
        above = _constituent_alignment_core(record(19), [], {}, state="SW", party="D")["components"][0]["detail"]
        below = _constituent_alignment_core(record(0), [], {}, state="SW", party="D")["components"][0]["detail"]
        assert "0.30 standard deviations per vote more independent than that" in above
        assert "0 at 0.45 more independent: 1.5 × 0.30, the gap beyond which the most out-of-pattern tenth of D members" in above
        assert "0.33 standard deviations per vote more loyal than that" in below
        assert "0 at 0.90 more loyal: 3 × 0.30" in below
        at = _constituent_alignment_core(record(10), [], {}, state="SW", party="D")["components"][0]["detail"]
        assert "exactly that" in at and "standard deviations per vote more" not in at

    def test_breakdown_says_when_the_half_vote_floor_moved_the_expectation(self):
        # 10 votes against a 0% expectation: the gap is read against 5%,
        # half a vote of this record, and the text says so.
        ref = {"senate": {"statistic": score_calculator.CONSTITUENT_REFERENCE_STATISTIC, "n": 50, "expected": {
            "D": {"a": 0.0, "b": 0.0, "scale": 0.3, "typical": 85.0}}}}
        detail = _constituent_alignment_core(record(1, total=10), [], {}, state="SW", party="D",
                                             reference=ref)["components"][0]["detail"]
        assert "break on 0.0% (read as 5.0%, half a vote of this record)" in detail

    def test_thin_records_are_pulled_toward_the_partys_typical_score(self):
        # v6.16: matching the norm scores 100, so 50 sits below nearly every
        # member; a thin record is pulled toward what a typical member of the
        # party scores instead (here 85). 10 votes keep half the distance.
        ref = _typical_ref()
        assert score(record(1, total=10), reference=ref) == 92  # 85 + (100 - 85) / 2
        assert score(record(7, total=10), reference=ref) == 42  # 85 + (0 - 85) / 2
        detail = _constituent_alignment_core(record(7, total=10), [], {}, state="SW", party="D",
                                             reference=ref)["components"][0]["detail"]
        assert "pulled toward 85, the typical score for a D member" in detail

    def test_no_readable_record_scores_the_partys_typical(self):
        core = _constituent_alignment_core(record(1, total=2), [], {}, state="SW", party="D",
                                           reference=_typical_ref())
        assert core["score"] == 85 and "typical score for a D member of this chamber, 85" in core["components"][0]["detail"]

    def test_breakdown_names_the_comparison(self):
        detail = _constituent_alignment_core(record(20), [], {}, state="SW", party="D")["components"][0]["detail"]
        assert "20.0% of 100" in detail and "break on 10.0%" in detail


class TestMeasuredReference:
    def _members(self, party, n=40, a=-2.2, b=-0.6, c=-1.2, opposed=True, votes=40):
        """Rates from a known kinked logit, with symmetric multiplicative
        noise so the fit has something to average out."""
        import math

        out = []
        for i in range(n):
            al = (i / (n - 1)) * 2 - 1 if opposed else i / (n - 1)
            rate = 1 / (1 + math.exp(-(a + b * al + c * min(al, 0.0))))
            out.append((party, al, rate * (1.02 if i % 2 else 0.98), votes))
        return out

    def test_recovers_the_chambers_kinked_expectation(self):
        ref = compute_constituent_reference(self._members("D") + self._members("R", a=-2.9))
        d, r = ref["expected"]["D"], ref["expected"]["R"]
        assert d["link"] == r["link"] == "logit"
        assert (d["a"], d["b"], d["b_opposed"]) == pytest.approx((-2.2, -0.6, -1.2), abs=0.03)
        assert r["a"] == pytest.approx(-2.9, abs=0.03)

    def test_keeps_each_partys_average_rate(self):
        # The logit fit's score equations match the fitted to the observed
        # sum, so the expectation stays on the party's own average (to the
        # stored coefficients' 5-decimal rounding).
        from app.pipeline.analyze.score_calculator import _expected_break_rate

        members = self._members("D") + self._members("R", a=-2.9)
        ref = compute_constituent_reference(members)
        for party in ("D", "R"):
            rows = [(al, br) for p, al, br, _ in members if p == party]
            fitted = sum(_expected_break_rate(ref["expected"][party], al) for al, _ in rows)
            assert fitted == pytest.approx(sum(br for _, br in rows), rel=1e-4)

    def test_never_predicts_zero(self):
        # The line it replaced gave the safest seats 0%, where no residual
        # can be read.
        from app.pipeline.analyze.score_calculator import _expected_break_rate

        members = [(p, al, max(0.0, 0.08 - 0.1 * al) + 0.001 * (i % 3), 40)
                   for p in ("D", "R") for i, al in enumerate(x / 20 for x in range(-20, 21))]
        ref = compute_constituent_reference(members)
        assert _expected_break_rate(ref["expected"]["D"], 1.0) > 0

    # The 118th Senate's Democrats with 20+ party-unity votes (Voteview):
    # party-signed seat alignment and break rate, rounded to 4 places.
    _SENATE_118_D = list(zip(
        [-0.1407, -0.1407, 0.8427, 0.8427, 0.8427, 0.3114, 0.3114, 0.5285, 0.5285, 0.4906, 0.4906, -0.1432, -0.1432, 0.851, 0.851, 0.4261, 0.4261, 0.1602, 0.9841, 0.9841, 0.9898, 0.9898, -0.057, -0.057, 0.0914, 0.0914, -0.711, -0.0697, -0.0697, 0.0987, 0.0987, 0.3869, 0.3869, 0.3869, 0.2167, 0.2167, 0.6299, 0.6299, -0.423, 0.4026, 0.4026, -0.1119, -0.1119, 0.5554, 0.5554, 1.0, 1.0, 0.1924, 0.1924, 0.5105, 0.5105, -1.0, -1.0, -0.1299],
        [0.0116, 0.0865, 0.0059, 0.0033, 0.0102, 0.0075, 0.0115, 0.0019, 0.0, 0.0075, 0.0019, 0.0076, 0.0, 0.0037, 0.0019, 0.002, 0.0, 0.019, 0.0019, 0.0, 0.0077, 0.0056, 0.0113, 0.0038, 0.0076, 0.0, 0.0527, 0.0281, 0.0056, 0.0078, 0.0094, 0.0211, 0.0039, 0.0, 0.0019, 0.0038, 0.0225, 0.0019, 0.0246, 0.0057, 0.0116, 0.0096, 0.012, 0.0019, 0.0, 0.0019, 0.0417, 0.0038, 0.0056, 0.0, 0.0019, 0.2288, 0.1266, 0.0094],
    ))

    def test_fit_converges_where_plain_reweighting_diverged(self):
        # Plain iteratively reweighted least squares overshot to coefficients
        # in the billions on this chamber (and then overflowed exp()); the
        # step-halving Newton fit lands on the convex objective's minimum.
        import numpy as np
        from scipy.optimize import minimize

        from app.pipeline.analyze.score_calculator import _fractional_logit

        al = np.array([a for a, _ in self._SENATE_118_D])
        br = np.array([b for _, b in self._SENATE_118_D])
        X = np.column_stack([np.ones_like(al), al, np.minimum(al, 0.0)])
        beta = _fractional_logit(X, br)

        def loss(b):
            return float(np.sum(np.logaddexp(0.0, X @ b) - br * (X @ b)))

        best = minimize(loss, np.zeros(3), method="BFGS")
        assert np.all(np.abs(beta) < 50)
        assert loss(beta) <= best.fun + 1e-8

    def test_records_each_partys_typical_score(self):
        import numpy as np

        from app.pipeline.analyze.score_calculator import _expected_break_rate, _vote_shape, seat_residual

        members = self._members("D") + self._members("R", a=-2.9)
        ref = compute_constituent_reference(members)
        for party in ("D", "R"):
            fit = ref["expected"][party]
            shapes = [_vote_shape(seat_residual(br, _expected_break_rate(fit, al), n), fit["scale"])
                      for p, al, br, n in members if p == party]
            assert fit["typical"] == pytest.approx(float(np.median(shapes)), abs=0.01)

    def test_separable_opposed_seats_fall_back_to_one_slope(self):
        # Every opposed-seat member at zero breaks (common early in a
        # Congress): the kink has no finite coefficient. The fit drops the
        # kink instead of running it off to a step at the swing seat.
        members = [("D", al, 0.0 if al < 0 else 0.05 + 0.01 * (i % 3), 40)
                   for i, al in enumerate(x / 12 for x in range(-12, 13))]
        members += self._members("R", a=-2.9)
        ref = compute_constituent_reference(members)
        d = ref["expected"]["D"]
        assert d["b_opposed"] == 0.0 and abs(d["a"]) < 50 and abs(d["b"]) < 50

    def test_no_finite_fit_means_no_reference(self):
        # One member breaks, at the edge of the lean; everyone else never
        # does. No slope, kinked or not, is finite: fall back rather than
        # extrapolate toward a 100% expectation.
        members = [("D", al, 0.0, 40) for al in (x / 12 for x in range(-12, 12))] + [("D", 1.0, 0.2, 40)]
        members += self._members("R", a=-2.9)
        assert compute_constituent_reference(members) is None

    def test_no_bend_without_enough_opposed_seats(self):
        ref = compute_constituent_reference(self._members("D", opposed=False) + self._members("R"))
        assert ref["expected"]["D"]["b_opposed"] == 0.0

    def test_both_parties_or_neither(self):
        assert compute_constituent_reference(self._members("D") + self._members("R", n=10)) is None

    def test_a_party_that_never_breaks_has_no_expectation(self):
        members = self._members("D") + [("R", 0.5, 0.0, 40) for _ in range(30)]
        assert compute_constituent_reference(members) is None

    def test_each_partys_scale_is_its_own_p90_residual(self):
        import numpy as np

        from app.pipeline.analyze.score_calculator import _expected_break_rate, seat_residual

        members = self._members("D") + self._members("R", a=-2.9)
        ref = compute_constituent_reference(members)
        for party in ("D", "R"):
            fit = ref["expected"][party]
            residuals = [abs(seat_residual(br, _expected_break_rate(fit, al), n))
                         for p, al, br, n in members if p == party]
            assert fit["scale"] == pytest.approx(float(np.quantile(residuals, 0.9)), abs=1e-5)

    def test_a_cohesive_party_is_not_favored(self):
        # One party tightly bunched around its norm, the other spread out: on
        # one pooled scale the cohesive party would score higher just for
        # being cohesive. Per party, a member as far out as their own party's
        # 90th percentile scores the same in either.
        import math

        from app.pipeline.analyze.score_calculator import _vote_shape

        def spread(party, width):
            return [(party, 0.0, 0.1 * (1 + width * math.sin(i)), 40) for i in range(40)]

        ref = compute_constituent_reference(spread("D", 0.05) + spread("R", 0.8))
        d, r = ref["expected"]["D"]["scale"], ref["expected"]["R"]["scale"]
        assert r > 5 * d
        assert _vote_shape(d, d) == _vote_shape(r, r)

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
        assert constituent_reference_inputs(members) == [("D", 0.0, 0.2, 100), ("D", 1.0, 0.1, 100)]

    def test_pipeline_persists_this_runs_reference(self):
        from app.pipeline.live_references import live_constituent_reference_measured

        members = [{"state": "SW", "party": p, "votingRecord": record(10 + i % 5)}
                   for p in ("D", "R") for i in range(25)]
        merged, measured = live_constituent_reference_measured("house", members)
        assert measured and merged["house"]["n"] == 50
        assert CONSTITUENT_REFERENCE.load()["house"]["n"] == 50
        assert merged["senate"]["deviation_p90"] == 0.3  # untouched

    def test_too_few_members_keeps_the_last_reference(self):
        from app.pipeline.live_references import live_constituent_reference_measured

        merged, measured = live_constituent_reference_measured("senate", [{"state": "SW", "party": "D", "votingRecord": record(10)}])
        assert not measured and merged == CONSTITUENT_REFERENCE.load()


class TestPositionCongruence:
    """Synthetic D fit: expected dim1 = -0.35 + 0.006 * seat PVI, saturation
    0.2. Vote component held at 100 (break rate at expectation)."""

    @pytest.fixture(autouse=True)
    def ideal_points(self, monkeypatch):
        def patch(members, votes=None, reliability=None, **extra):
            section = {
                "members": members,
                "fit": {"D": {"a": -0.35, "b": 0.006}, "R": {"a": 0.35, "b": 0.006}},
                "extremity_p90": 0.2,
                "measure": "Nokken-Poole",
                **extra,
            }
            if votes is not None:
                section.update(votes=votes, reliability=reliability or {"n0": 24})
            monkeypatch.setattr(score_calculator, "_member_ideal_points_cache", {"senate": section})
        self.patch = patch

    def _congruence(self, bioguide_id="X1"):
        core = _constituent_alignment_core(record(10), [], {}, state="SW", party="D", bioguide_id=bioguide_id)
        return {c["label"]: c for c in core["components"]}["Position congruence"]

    def test_flank_ward_scores_below_neutral(self):
        self.patch({"X1": -0.55})
        assert score(record(10), bioguide_id="X1") == 70  # 100*0.7 + 0*0.3

    def test_center_ward_scores_above_neutral(self):
        self.patch({"X1": -0.15})
        assert score(record(10), bioguide_id="X1") == 100

    def test_no_safe_seat_scaling(self):
        # DS is D+15: expected dim1 -0.44. The same 0.2 flank-ward and
        # center-ward residuals score exactly as they do in a swing seat.
        self.patch({"F": -0.64, "C": -0.24})
        assert score(record(5), state="DS", bioguide_id="F") == 70
        assert score(record(5), state="DS", bioguide_id="C") == 100

    def test_symmetric(self):
        self.patch({"F": -0.40, "C": -0.30})
        # Vote part at 100 (70 of the score); congruence symmetric about 50.
        assert score(record(10), bioguide_id="F") + score(record(10), bioguide_id="C") == 2 * (70 + 15)

    def test_missing_data_skips_the_component(self):
        self.patch({})
        core = _constituent_alignment_core(record(10), [], {}, state="SW", party="D", bioguide_id="X1")
        assert [c["label"] for c in core["components"]] == ["Seat-relative vote alignment"]
        assert core["components"][0]["weight"] == 1.0

    def test_a_position_is_weighted_by_its_reliability(self):
        """v6.27: 24 roll calls against n0 24 (no reference), so a position
        at saturation toward the flank counts half: 25, not 0."""
        self.patch({"X1": -0.55}, votes={"X1": 24})
        assert self._congruence()["score"] == 25.0
        assert "rests on 24 roll calls, so it counts at 50% strength" in self._congruence()["detail"]
        self.patch({"X1": -0.55}, votes={"X1": 24}, reliability={"n0": 24, "half_weight_votes": 24})
        assert "half strength at about 24 roll calls" in self._congruence()["detail"]

    def test_a_weight_just_under_one_never_reads_as_full_strength(self):
        """199 votes against n0 56 and a full record at 200 weighs 0.999: the
        detail says 99%, not a 100% that is still pulled toward 50."""
        self.patch({"X1": -0.55}, votes={"X1": 199}, reliability={"n0": 56, "reference_votes": 200})
        assert "counts at 99% strength" in self._congruence()["detail"]

    def test_the_shown_percentage_survives_float_error(self):
        """0.57 * 100 is 56.999... in floating point; it still reads 57%."""
        from app.pipeline.analyze.score_calculator import _shown_percent
        assert [_shown_percent(w) for w in (0.57, 0.29, 0.5599999999999999, 0.9985)] == [57, 29, 56, 99]

    def test_the_weight_applies_before_the_scale_saturates(self):
        """The weight is on the position: a position twice past saturation
        on 24 votes (half weight) is still at saturation, 0, not halfway."""
        self.patch({"X1": -0.75}, votes={"X1": 24})
        assert self._congruence()["score"] == 0.0

    def test_a_full_record_is_barely_weighted(self):
        self.patch({"X1": -0.55}, votes={"X1": 576})
        assert self._congruence()["score"] == 2.0  # 50 - 50 * 576 / 600
        self.patch({"X1": -0.15}, votes={"X1": 576})
        assert self._congruence()["score"] == 98.0

    def test_no_count_gets_the_measured_uncounted_weight(self):
        """Voteview reports no count: the position counts at the weight
        measured for such positions (0.2 here), and the breakdown says so
        rather than "0 roll calls"."""
        self.patch({"X1": -0.55}, votes={}, reliability={"n0": 24, "uncounted_weight": 0.2})
        assert self._congruence()["score"] == 40.0
        assert "reports no count" in self._congruence()["detail"]
        assert "0 roll calls" not in self._congruence()["detail"]

    def test_a_position_from_no_counted_votes_sits_at_neutral(self):
        """No count and no career position: stored as 0 votes at ingest, so
        the position counts nothing, and the breakdown says that plainly."""
        self.patch({"X1": -0.55}, votes={"X1": 0}, reliability={"n0": 24, "uncounted_weight": 0.2})
        assert self._congruence()["score"] == 50.0
        assert "has no career position yet" in self._congruence()["detail"]
        # With no calibration the position counts in full, and the text says nothing of 50.
        self.patch({"X1": -0.55}, votes={"X1": 0}, reliability={"n0": 0})
        assert self._congruence()["score"] == 0.0
        assert "has no career position yet" not in self._congruence()["detail"]

    def test_a_full_record_counts_in_full(self):
        """A full record (here the reference, 576 votes, or more) counts in full."""
        self.patch({"X1": -0.55}, votes={"X1": 800},
                   reliability={"n0": 24, "reference_votes": 576})
        assert self._congruence()["score"] == 0.0

    def test_no_position_in_a_current_section_sits_at_neutral(self):
        """A seated member Voteview hasn't placed (or gave its 0, 0
        placeholder) gets the neutral 50 a position from no votes would,
        not a dropped component that would let the vote part carry it all."""
        self.patch({"X2": -0.4}, votes={"X2": 500})
        core = _constituent_alignment_core(record(10), [], {}, state="SW", party="D", bioguide_id="X1")
        by_label = {c["label"]: c for c in core["components"]}
        assert by_label["Position congruence"]["score"] == 50.0
        assert "no position" in by_label["Position congruence"]["detail"]

    def test_a_section_without_counts_is_not_weighted(self):
        """A file written before v6.27 has no counts or reliability."""
        self.patch({"X1": -0.55})
        assert self._congruence()["score"] == 0.0
        self.patch({})
        core = _constituent_alignment_core(record(10), [], {}, state="SW", party="D", bioguide_id="X1")
        assert [c["label"] for c in core["components"]] == ["Seat-relative vote alignment"]

    def test_a_section_without_counts_says_nothing_of_strength(self):
        """A position read from a pre-v6.27 section (no counts, no
        reliability) counts in full, and its detail says nothing of a
        strength or a pull toward 50."""
        self.patch({"X1": -0.55})
        detail = self._congruence()["detail"]
        assert "strength" not in detail and "pulled toward 50" not in detail
        assert "reports no count" not in detail

    def test_no_calibration_no_weighting(self):
        self.patch({"X1": -0.55}, votes={"X1": 5}, reliability={"n0": 0})
        assert self._congruence()["score"] == 0.0

    def test_another_congresss_section_is_not_read(self):
        """A refresh that failed after Jan 3 leaves the last Congress's
        positions on disk; they are not this term's record (principle 6)."""
        rec = {**record(10), "partyLineRecord": {"congress": 120, "votes": 0, "breaks": []}}
        self.patch({"X1": -0.55}, votes={"X1": 500}, congress=119)
        core = _constituent_alignment_core(rec, [], {}, state="SW", party="D", bioguide_id="X1")
        assert [c["label"] for c in core["components"]] == ["Seat-relative vote alignment"]
        self.patch({"X1": -0.55}, votes={"X1": 500}, congress=120)
        core = _constituent_alignment_core(rec, [], {}, state="SW", party="D", bioguide_id="X1")
        assert {c["label"]: c for c in core["components"]}["Position congruence"]["score"] < 5

    def test_a_member_with_no_party_line_record_is_judged_by_the_sitting_congress(self, monkeypatch):
        """Matched to no roll call, a member has no record's Congress, so the
        section is judged against the sitting one."""
        monkeypatch.setattr(settings, "CURRENT_CONGRESS", 120)
        self.patch({"X1": -0.55}, votes={"X1": 500}, congress=119)
        core = _constituent_alignment_core(record(10), [], {}, state="SW", party="D", bioguide_id="X1")
        assert [c["label"] for c in core["components"]] == ["Seat-relative vote alignment"]
        self.patch({"X1": -0.55}, votes={"X1": 500}, congress=120)
        assert self._congruence()["score"] < 5

    def test_the_breakdown_reads_the_positions_of_the_records_congress(self, monkeypatch):
        """Across Jan 3, a stored 119th score's breakdown still reads the
        119th positions it was scored on, until the next run rescores it."""
        monkeypatch.setattr(settings, "CURRENT_CONGRESS", 120)
        self.patch({"X1": -0.55}, votes={"X1": 500}, congress=119)
        rec = {**record(10), "partyLineRecord": {"congress": 119, "votes": 0, "breaks": []}}
        core = _constituent_alignment_core(rec, [], {}, state="SW", party="D", bioguide_id="X1")
        assert "Position congruence" in [c["label"] for c in core["components"]]

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
        data, _ = build_chamber_ideal_points(self._rows(), "senate", {"SW": 0}, {}, reliability={})
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



class TestVotePartStatus:
    """calculate_confidence records how the vote part was scored, so the
    scorecard states it instead of re-deriving the rule."""

    def status(self, rec, **kw):
        return score_calculator.calculate_confidence(
            {"state": "SW", "party": "D", "votingRecord": rec, **kw})["constituentAlignmentVotePart"]

    def test_few_votes(self):
        # A measured typical score: that. None (conftest's pinned reference
        # carries none, as the bundled prior doesn't): neutral 50.
        ref = _typical_ref()
        assert self.status(record(1, total=2), constituentReference=ref) == "typical:few-votes"
        assert self.status(record(1, total=2)) == "neutral:few-votes"
        assert self.status(record(1, total=2), party="I") == "neutral:few-votes"

    def test_an_unmeasured_prior_is_never_called_typical(self):
        # The bundled prior has no population: its thin records are neutral
        # 50 and say so, whatever a "typical" key might hold.
        prior = {"senate": {"statistic": score_calculator.CONSTITUENT_REFERENCE_STATISTIC, "n": None, "expected": {
            "D": {"a": 0.10, "b": 0.0, "typical": 85.0}}, "deviation_p90": 0.3}}
        assert self.status(record(1, total=2), constituentReference=prior) == "neutral:few-votes"
        core = _constituent_alignment_core(record(7, total=10), [], {}, state="SW", party="D", reference=prior)
        assert core["score"] == 25 and "pulled toward a neutral 50" in core["components"][0]["detail"]
        assert self.status(record(1, total=10), constituentReference=prior) == "shrunk-neutral:0.50"

    def test_no_expectation(self):
        assert self.status(record(1, total=10), party="I") == "neutral:no-expectation"

    def test_shrunk_share(self):
        ref = _typical_ref()
        assert self.status(record(1, total=12), constituentReference=ref) == "shrunk:0.60"
        # No measured typical (conftest's pinned reference): pulled toward 50.
        assert self.status(record(1, total=12)) == "shrunk-neutral:0.60"

    def test_full(self):
        assert self.status(record(5, total=40)) == "full"


def test_the_bundled_prior_is_stamped_with_the_current_statistic():
    # The loader skips a chamber entry measured on another statistic. v6.20
    # changed the statistic without restamping this file, so a deployment
    # before its first run had no Constituent Alignment reference at all.
    import json
    import pathlib

    from app.config_definitions import CONSTITUENT_REFERENCE_STATISTIC

    bundled = json.loads(
        (pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "constituent_reference.json").read_text()
    )
    for chamber in ("senate", "house"):
        assert bundled[chamber]["statistic"] == CONSTITUENT_REFERENCE_STATISTIC, chamber
