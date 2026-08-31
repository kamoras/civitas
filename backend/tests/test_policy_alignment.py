"""Tests for policy_alignment's industry <-> policy-area anchor coverage."""

import pytest

from app.pipeline.analyze.bill_analyzer import POLICY_TAXONOMY
from app.pipeline.analyze.policy_alignment import (
    POLICY_ANCHORS,
    clear_alignment_cache,
    detect_donor_vote_connections,
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
    def test_a_defense_contractor_donor_surfaces_a_foreign_policy_vote(self):
        """Regression for the 2026-08 gap: FOREIGN_POLICY was absent from
        POLICY_ANCHORS entirely (a flat 0.0 against every industry), and
        an early fix attempt reintroduced the gap in a subtler form — a
        diplomacy-only anchor scored DEFENSE at 0.686, still under the
        0.75 gate detect_donor_vote_connections actually uses. Asserting
        against that real gate end-to-end (not an arbitrary score cutoff)
        is what would have caught the subtler version."""
        clear_alignment_cache()
        try:
            matches = detect_donor_vote_connections(
                donors=[{"type": "Org/Employees", "industry": "DEFENSE",
                         "name": "Lockheed Martin PAC", "total": 50_000}],
                votes=[{"vote": "Yea", "policyArea": "FOREIGN_POLICY",
                        "billId": "s123-118", "billName": "Foreign Military Sales Authorization Act",
                        "totalYeas": 60, "totalNays": 40}],
                industry_breakdown=[{"industry": "DEFENSE", "total": 50_000}],
            )
            assert len(matches) == 1
            assert matches[0]["industry"] == "DEFENSE"
            assert matches[0]["similarity"] >= 0.75
        finally:
            clear_alignment_cache()
