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


@pytest.mark.slow
@pytest.mark.parametrize(
    ("donor_extra", "client", "is_committee"),
    [
        # A PAC with a separate sponsor is searched under the sponsor.
        ({"isCommittee": True, "connectedOrg": "LOCKHEED MARTIN CORPORATION"}, "LOCKHEED MARTIN CORPORATION", False),
        # With none, only its own name is left, where a miss proves nothing.
        ({"isCommittee": True}, "Lockheed Martin PAC", True),
        # An organization's employees: its own name is how the registry lists it.
        ({}, "Lockheed Martin PAC", False),
    ],
)
def test_lobbying_client_says_whether_it_is_a_committees_own_name(donor_extra, client, is_committee):
    clear_alignment_cache()
    try:
        matches = detect_donor_vote_connections(
            donors=[{"type": "Org/Employees", "industry": "DEFENSE",
                     "name": "Lockheed Martin PAC", "total": 50_000, **donor_extra}],
            votes=[{"vote": "Yea", "policyArea": "FOREIGN_POLICY",
                    "billId": "s123-118", "billName": "Foreign Military Sales Authorization Act",
                    "totalYeas": 60, "totalNays": 40}],
            industry_breakdown=[{"industry": "DEFENSE", "total": 50_000}],
        )
        assert matches[0]["lobbyingClient"] == client
        assert matches[0]["lobbyingClientIsCommittee"] is is_committee
    finally:
        clear_alignment_cache()


def test_a_long_bill_title_is_cut_at_a_word(monkeypatch):
    """The title as a Senate vote carries it; cut mid-word it read "...
    under chapter 8 of ti" in the stored description."""
    monkeypatch.setattr("app.pipeline.analyze.policy_alignment.industry_policy_similarity", lambda *a: 0.9)
    title = ("A joint resolution providing for congressional disapproval under chapter 8 of title 5, "
             "United States Code, of the rule submitted by the Department of Education")
    matches = detect_donor_vote_connections(
        donors=[{"type": "Org/Employees", "industry": "EDUCATION", "name": "A University", "total": 1}],
        votes=[{"vote": "Yea", "policyArea": "EDUCATION", "billId": "sjres1-119", "billName": title,
                "totalYeas": 50, "totalNays": 49}],
        industry_breakdown=[{"industry": "EDUCATION", "total": 1}],
    )
    assert "under chapter 8 of… (EDUCATION)" in matches[0]["description"]
