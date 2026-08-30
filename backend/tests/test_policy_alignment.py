"""Tests for policy_alignment's industry <-> policy-area anchor coverage."""

import pytest

from app.pipeline.analyze.bill_analyzer import POLICY_TAXONOMY
from app.pipeline.analyze.policy_alignment import (
    POLICY_ANCHORS,
    clear_alignment_cache,
    industry_policy_similarity,
)


class TestPolicyAnchorCoverage:
    """POLICY_ANCHORS must cover every category votes can actually be
    classified into (POLICY_TAXONOMY) — a missing key isn't a style gap,
    it's a silent 0.0 similarity for every vote in that category, for
    every industry, regardless of true relevance."""

    def test_every_policy_taxonomy_category_has_an_anchor(self):
        missing = set(POLICY_TAXONOMY) - set(POLICY_ANCHORS)
        assert not missing, f"POLICY_ANCHORS is missing: {missing}"

    @pytest.mark.slow
    def test_a_vote_in_a_previously_missing_category_gets_a_real_similarity(self):
        """Regression for the 2026-08 gap: ABORTION, ECONOMY, and
        FOREIGN_POLICY were absent from POLICY_ANCHORS, so any vote
        classified into them always scored 0.0 against every industry."""
        clear_alignment_cache()
        try:
            score = industry_policy_similarity("DEFENSE", "FOREIGN_POLICY")
            assert score > 0.5
        finally:
            clear_alignment_cache()
