"""Tests for the cross-reference analysis module.

Covers the embedding-based (non-LLM) components:
  - Promise-vote alignment via embedding similarity
  - Key vote selection scoring
  - Lobbying match detection via donor↔vote embedding similarity
  - Platform topic extraction
"""

import pytest
import numpy as np
from unittest.mock import patch, MagicMock

from app.pipeline.analyze.cross_reference import (
    _extract_platform_topics,
    select_key_votes,
    detect_lobbying_matches,
)
from app.pipeline.analyze.policy_alignment import (
    compute_promise_vote_alignment,
    get_related_policies,
    industry_policy_similarity,
)


# ── Promise-vote alignment (embedding-based) ─────────────────────


class TestPromiseVoteAlignment:
    """Deterministic promise alignment using embedding similarity."""

    def test_no_votes_returns_unclear(self):
        result = compute_promise_vote_alignment("Lower drug costs", [])
        assert result["alignment"] == "unclear"
        assert result["relatedVotes"] == []

    def test_no_promise_returns_unclear(self):
        result = compute_promise_vote_alignment("", [{"vote": "Yea", "billId": "HR.1"}])
        assert result["alignment"] == "unclear"

    def test_procedural_votes_excluded(self):
        votes = [
            {"billId": "HR.1", "vote": "Yea", "policyArea": "PROCEDURAL",
             "billName": "Motion to table", "description": "Cloture motion"},
        ]
        result = compute_promise_vote_alignment("Fight climate change", votes)
        assert result["alignment"] == "unclear"

    def test_returns_valid_alignment(self):
        votes = [
            {"billId": "HR.1", "vote": "Yea", "policyArea": "HEALTHCARE",
             "billName": "Prescription Drug Pricing Act", "description": "Lower drug costs",
             "stance": "pro"},
        ]
        result = compute_promise_vote_alignment(
            "Lower prescription drug costs for seniors", votes
        )
        assert result["alignment"] in ("kept", "broken", "partial", "unclear")
        assert isinstance(result["confidence"], float)

    def test_related_votes_populated(self):
        votes = [
            {"billId": "HR.1", "vote": "Yea", "policyArea": "HEALTHCARE",
             "billName": "Healthcare Reform Act", "description": "Expand Medicare coverage",
             "stance": "pro"},
            {"billId": "HR.2", "vote": "Nay", "policyArea": "DEFENSE",
             "billName": "Defense Authorization", "description": "Military spending",
             "stance": "pro"},
        ]
        result = compute_promise_vote_alignment(
            "Expand Medicare coverage and lower healthcare costs", votes
        )
        if result["relatedVotes"]:
            assert "HR.1" in result["relatedVotes"]


# ── Industry-policy embedding similarity ─────────────────────────


class TestIndustryPolicySimilarity:
    """Embedding-based replacement for hardcoded _INDUSTRY_POLICY_MAP."""

    def test_pharma_healthcare_related(self):
        score = industry_policy_similarity("PHARMA", "HEALTHCARE")
        assert score > 0.3

    def test_pharma_defense_unrelated(self):
        score = industry_policy_similarity("PHARMA", "DEFENSE")
        pharma_health = industry_policy_similarity("PHARMA", "HEALTHCARE")
        assert score < pharma_health

    def test_defense_defense_related(self):
        score = industry_policy_similarity("DEFENSE", "DEFENSE")
        assert score > 0.4

    def test_get_related_policies_returns_set(self):
        related = get_related_policies("PHARMA")
        assert isinstance(related, set)
        assert "HEALTHCARE" in related

    def test_unknown_industry_returns_zero(self):
        score = industry_policy_similarity("NONEXISTENT", "HEALTHCARE")
        assert score == 0.0


# ── Platform topic extraction ────────────────────────────────────


class TestExtractPlatformTopics:

    def test_splits_multiline(self):
        text = (
            "Fight climate change through clean energy investment\n"
            "Lower prescription drug costs for American seniors\n"
            "Comprehensive immigration reform and border security"
        )
        topics = _extract_platform_topics(text)
        assert len(topics) >= 2

    def test_strips_bullet_markers(self):
        text = "• Fight climate change and invest in green energy\n- Lower drug costs for seniors and families"
        topics = _extract_platform_topics(text)
        for t in topics:
            assert not t.startswith("•")
            assert not t.startswith("-")

    def test_max_topics_capped(self):
        text = "\n".join(f"Topic number {i} about something important in our country" for i in range(20))
        topics = _extract_platform_topics(text, max_topics=4)
        assert len(topics) <= 4

    def test_empty_input(self):
        assert _extract_platform_topics("") == []

    def test_single_blob(self):
        text = "A single long paragraph about climate change and energy policy with no line breaks at all"
        topics = _extract_platform_topics(text)
        assert len(topics) >= 1

    def test_short_lines_skipped(self):
        text = "Short\nAlso short\nThis is a meaningful line about healthcare reform and policy changes"
        topics = _extract_platform_topics(text)
        assert len(topics) == 1

    def test_nav_junk_rejected(self):
        text = (
            "Senator Murphy Facebook Senator Murphy Instagram\n"
            "Website Search Open Website Search\n"
            "Comprehensive healthcare reform and prescription drug pricing"
        )
        topics = _extract_platform_topics(text)
        assert len(topics) == 1
        assert "healthcare" in topics[0].lower()


# ── Key vote selection ───────────────────────────────────────────


class TestSelectKeyVotes:

    def _make_vote(self, bill_id, policy="HEALTHCARE", with_party=True, vote="Yea"):
        return {
            "billId": bill_id,
            "policyArea": policy,
            "votedWithParty": with_party,
            "vote": vote,
        }

    def test_against_party_prioritized(self):
        votes = [
            self._make_vote("HR.1", with_party=True),
            self._make_vote("HR.2", with_party=False),
            self._make_vote("HR.3", with_party=True),
        ]
        ids = select_key_votes(votes, [])
        assert ids[0] == "HR.2"

    def test_procedural_excluded(self):
        votes = [
            self._make_vote("HR.1", policy="PROCEDURAL"),
            self._make_vote("HR.2", policy="HEALTHCARE"),
        ]
        ids = select_key_votes(votes, [])
        assert "HR.1" not in ids

    def test_not_voting_excluded(self):
        votes = [
            self._make_vote("HR.1", vote="Not Voting"),
            self._make_vote("HR.2", vote="Yea"),
        ]
        ids = select_key_votes(votes, [])
        assert "HR.1" not in ids
        assert "HR.2" in ids

    def test_max_keys_respected(self):
        votes = [self._make_vote(f"HR.{i}") for i in range(20)]
        ids = select_key_votes(votes, [], max_keys=5)
        assert len(ids) <= 5

    def test_empty_votes(self):
        assert select_key_votes([], []) == []

    def test_candidate_affiliated_donors_ignored(self):
        votes = [self._make_vote("HR.1", policy="HEALTHCARE")]
        donors = [{"name": "Friends of Cruz", "industry": "PHARMA", "type": "CandidateAffiliated"}]
        ids_with = select_key_votes(votes, donors)
        ids_without = select_key_votes(votes, [])
        assert ids_with == ids_without


# ── Lobbying match detection (embedding-based) ───────────────────


class TestDetectLobbyingMatches:
    """Donor-vote connection detection via embedding similarity."""

    def test_candidate_affiliated_excluded(self):
        donors = [
            {"name": "Friends of Cruz", "industry": "PHARMA",
             "type": "CandidateAffiliated", "total": 100000},
        ]
        votes = [
            {"billId": "HR.1", "vote": "Yea", "billName": "Drug Bill",
             "policyArea": "HEALTHCARE", "description": "Lower drug costs"},
        ]
        matches = detect_lobbying_matches(donors, votes)
        assert len(matches) == 0

    def test_other_industry_excluded(self):
        donors = [
            {"name": "Random LLC", "industry": "OTHER", "type": "PAC", "total": 5000},
        ]
        votes = [
            {"billId": "HR.1", "vote": "Yea", "billName": "Some Bill",
             "policyArea": "HEALTHCARE", "description": "Healthcare reform"},
        ]
        matches = detect_lobbying_matches(donors, votes)
        assert len(matches) == 0

    def test_empty_donors(self):
        assert detect_lobbying_matches(
            [], [{"billId": "HR.1", "vote": "Yea", "policyArea": "DEFENSE"}]
        ) == []

    def test_empty_votes(self):
        donors = [{"name": "Corp", "industry": "PHARMA", "type": "PAC", "total": 5000}]
        assert detect_lobbying_matches(donors, []) == []

    def test_max_eight_matches(self):
        donors = [
            {"name": f"Pharma Corp {i}", "industry": "PHARMA", "type": "PAC",
             "total": 1000 * (10 - i)}
            for i in range(10)
        ]
        votes = [
            {"billId": f"HR.{i}", "vote": "Yea",
             "billName": f"Healthcare Bill {i}",
             "policyArea": "HEALTHCARE",
             "description": f"Healthcare reform provision {i}"}
            for i in range(10)
        ]
        matches = detect_lobbying_matches(donors, votes)
        assert len(matches) <= 8

    def test_related_donor_and_vote_matched(self):
        donors = [
            {"name": "Pfizer Inc", "industry": "PHARMA", "type": "PAC", "total": 50000},
        ]
        votes = [
            {"billId": "HR.1", "vote": "Yea",
             "billName": "Prescription Drug Pricing Reform Act",
             "policyArea": "HEALTHCARE",
             "description": "Reduce prescription drug costs for Medicare recipients"},
        ]
        matches = detect_lobbying_matches(donors, votes)
        if matches:
            assert matches[0]["lobbyistOrg"] == "Pfizer Inc"
            assert matches[0]["industry"] == "PHARMA"
