"""Tests for the score-weighting constants in config_definitions.py."""

from app.config_definitions import SCORE_WEIGHTS


class TestScoreWeights:
    def test_weights_sum_to_one(self):
        assert abs(sum(SCORE_WEIGHTS.values()) - 1.0) < 1e-9

    def test_correlated_funding_pair_does_not_dominate(self):
        """fundingIndependence and fundingDiversity correlate at r=0.72 in
        live data (2026-07 composite-validity audit) — both driven by the
        same funding-profile signal, not two distinct dimensions. Their
        combined weight must not exceed what one genuinely distinct
        dimension gets, or the redundant signal silently dominates the
        overall score (the audit's reference case: the sitting Senate
        Majority Leader ranked 2nd-from-last Senate-wide almost entirely
        because of this pair, despite above-median scores elsewhere)."""
        correlated_pair = SCORE_WEIGHTS["fundingIndependence"] + SCORE_WEIGHTS["fundingDiversity"]
        other_dimensions = [
            SCORE_WEIGHTS["promisePersistence"],
            SCORE_WEIGHTS["independentVoting"],
            SCORE_WEIGHTS["legislativeEffectiveness"],
        ]
        assert correlated_pair <= max(other_dimensions) + 1e-9
