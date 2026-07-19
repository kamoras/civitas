"""Tests for the score-weighting constants in config_definitions.py."""

from app.config_definitions import SCORE_WEIGHTS


class TestScoreWeights:
    def test_weights_sum_to_one(self):
        assert abs(sum(SCORE_WEIGHTS.values()) - 1.0) < 1e-9

    def test_funding_diversity_folded_into_funding_independence(self):
        """v6.5: fundingIndependence and fundingDiversity correlated at
        r=0.72 in live data (2026-07 composite-validity audit) — both
        driven by the same funding-profile signal, not two distinct
        dimensions (the audit's reference case: the sitting Senate
        Majority Leader ranked 2nd-from-last Senate-wide almost entirely
        because of this pair, despite above-median scores elsewhere).
        Folded into one dimension outright rather than just rebalanced —
        no separate fundingDiversity key should reappear."""
        assert "fundingDiversity" not in SCORE_WEIGHTS
        assert SCORE_WEIGHTS["fundingIndependence"] == 0.33
