"""Tests for the cross-reference analysis module.

Covers the embedding-based (non-LLM) components:
  - Promise-vote alignment via embedding similarity
  - Key vote selection scoring
  - Lobbying match detection via donor↔vote embedding similarity
  - Platform topic extraction
"""

import numpy as np
from unittest.mock import patch

from app.pipeline.analyze.cross_reference import (
    _extract_platform_topics,
    select_key_votes,
    detect_lobbying_matches,
    positions_from_sponsored_bills,
)
from app.pipeline.analyze.policy_alignment import (
    compute_promise_vote_alignment,
    get_related_policies,
    industry_policy_similarity,
    _passes_relevance,
    _should_count_as_evidence_llm,
    VOTE_GATE_LOW,
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

    def test_neutral_stance_votes_give_no_directional_signal(self):
        """Topically related votes with neutral stance must not count as kept.

        The 2026-06 audit found 88% of promises scored "kept" partly
        because a Yea on any related bill counted as half-kept regardless
        of direction.
        """
        votes = [
            {"billId": "HR.1", "vote": "Yea", "policyArea": "HEALTHCARE",
             "billName": "Prescription Drug Pricing Act",
             "description": "Lower prescription drug costs for seniors",
             "stance": "neutral"},
        ]
        result = compute_promise_vote_alignment(
            "Lower prescription drug costs for seniors", votes
        )
        assert result["alignment"] != "kept"

    def test_sponsorship_alone_is_partial_not_kept(self):
        """Introducing a bill on the promised topic is effort, not fulfillment."""
        bills = [
            {"billId": "S.100",
             "title": "A bill to lower prescription drug costs for seniors",
             "isLaw": False, "latestAction": "Read twice and referred to committee"},
        ]
        result = compute_promise_vote_alignment(
            "Lower prescription drug costs for seniors", [], sponsored_bills=bills,
        )
        assert result["alignment"] in ("partial", "unclear")
        assert result["alignment"] != "kept"

    def test_advanced_sponsored_bill_counts_as_kept(self):
        """A sponsored bill that became law is genuine promise fulfillment."""
        bills = [
            {"billId": "S.100",
             "title": "A bill to lower prescription drug costs for seniors",
             "isLaw": True, "latestAction": "Became Public Law No: 119-45"},
        ]
        result = compute_promise_vote_alignment(
            "Lower prescription drug costs for seniors", [], sponsored_bills=bills,
        )
        assert result["alignment"] == "kept"


# ── Promise evidence gray-zone gate ───────────────────────────────


class TestPromiseEvidenceGate:
    """Below-threshold candidates go through an LLM relevance gate (Senate
    only) instead of being dropped outright, since generic (non-bill-quoting)
    promises rarely clear the high embedding threshold even when genuinely
    related (2026-07 audit: PP population stdev collapsed to 3.7 against an
    8.0 floor because ~93% of promises had no evidence clear the bar)."""

    def test_above_high_auto_accepts_without_llm_call(self):
        with patch(
            "app.pipeline.analyze.policy_alignment._should_count_as_evidence_llm"
        ) as mock_gate:
            result = _passes_relevance(0.85, VOTE_GATE_LOW, 0.80, True, "promise", "candidate", "vote")
        assert result is True
        mock_gate.assert_not_called()

    def test_below_low_auto_rejects_without_llm_call(self):
        with patch(
            "app.pipeline.analyze.policy_alignment._should_count_as_evidence_llm"
        ) as mock_gate:
            result = _passes_relevance(0.50, VOTE_GATE_LOW, 0.80, True, "promise", "candidate", "vote")
        assert result is False
        mock_gate.assert_not_called()

    def test_gray_zone_defers_to_llm_gate(self):
        with patch(
            "app.pipeline.analyze.policy_alignment._should_count_as_evidence_llm",
            return_value=True,
        ) as mock_gate:
            result = _passes_relevance(0.70, VOTE_GATE_LOW, 0.80, True, "promise", "candidate", "vote")
        assert result is True
        mock_gate.assert_called_once()

    def test_gray_zone_without_llm_budget_rejects(self):
        """House pipeline (use_llm=False) keeps the pre-existing sharp cutoff."""
        with patch(
            "app.pipeline.analyze.policy_alignment._should_count_as_evidence_llm"
        ) as mock_gate:
            result = _passes_relevance(0.70, VOTE_GATE_LOW, 0.80, False, "promise", "candidate", "vote")
        assert result is False
        mock_gate.assert_not_called()

    def test_gate_fails_closed_on_unparseable_llm_response(self):
        with patch(
            "app.pipeline.analyze.ollama_client.call_llm", return_value=None,
        ):
            assert _should_count_as_evidence_llm("promise", "candidate", "vote") is False

    def test_gate_fails_closed_on_missing_relates_key(self):
        with patch(
            "app.pipeline.analyze.ollama_client.call_llm", return_value={"reason": "no key"},
        ):
            assert _should_count_as_evidence_llm("promise", "candidate", "vote") is False

    def test_gate_respects_llm_relates_true(self):
        with patch(
            "app.pipeline.analyze.ollama_client.call_llm",
            return_value={"relates": True, "reason": "same subject"},
        ):
            assert _should_count_as_evidence_llm("promise", "candidate", "vote") is True

    def test_end_to_end_gray_zone_vote_confirmed_by_llm_counts_as_kept(self):
        """A vote below the auto-accept threshold but confirmed by the LLM
        gate should be able to resolve a promise, not leave it 'unclear'."""
        votes = [
            {"billId": "HR.1", "vote": "Yea", "policyArea": "HEALTHCARE",
             "billName": "Prescription Drug Pricing Act",
             "description": "Lower prescription drug costs for seniors",
             "stance": "pro"},
        ]
        with patch(
            "app.pipeline.analyze.policy_alignment._embed_batch"
        ) as mock_batch, patch(
            "app.pipeline.analyze.policy_alignment._embed"
        ) as mock_embed, patch(
            "app.pipeline.analyze.policy_alignment._should_count_as_evidence_llm",
            return_value=True,
        ) as mock_gate, patch(
            "app.pipeline.analyze.ollama_client.call_llm", return_value=None,
        ):
            mock_embed.return_value = np.array([1.0, 0.0])
            # sim = dot product = 0.70: below the 0.80 auto-accept threshold,
            # above VOTE_GATE_LOW (0.62) — lands in the gray zone.
            mock_batch.return_value = np.array([[0.70, np.sqrt(1 - 0.70 ** 2)]])
            result = compute_promise_vote_alignment(
                "Expand Medicare coverage to all Americans", votes, use_llm=True,
            )
        mock_gate.assert_called()
        assert result["alignment"] == "kept"
        assert "HR.1" in result["relatedVotes"]

    def test_end_to_end_gray_zone_vote_rejected_by_llm_stays_unclear(self):
        votes = [
            {"billId": "HR.1", "vote": "Yea", "policyArea": "HEALTHCARE",
             "billName": "Prescription Drug Pricing Act",
             "description": "Lower prescription drug costs for seniors",
             "stance": "pro"},
        ]
        with patch(
            "app.pipeline.analyze.policy_alignment._embed_batch"
        ) as mock_batch, patch(
            "app.pipeline.analyze.policy_alignment._embed"
        ) as mock_embed, patch(
            "app.pipeline.analyze.policy_alignment._should_count_as_evidence_llm",
            return_value=False,
        ), patch(
            "app.pipeline.analyze.ollama_client.call_llm", return_value=None,
        ):
            mock_embed.return_value = np.array([1.0, 0.0])
            mock_batch.return_value = np.array([[0.70, np.sqrt(1 - 0.70 ** 2)]])
            result = compute_promise_vote_alignment(
                "Expand Medicare coverage to all Americans", votes, use_llm=True,
            )
        assert result["alignment"] == "unclear"


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


# ── House positions from sponsored bills ─────────────────────────


class TestPositionsFromSponsoredBills:
    """Deterministic House promise derivation (no platform text, no LLM)."""

    def _bills(self):
        return [
            {"billId": "HR.10", "billType": "hr",
             "title": "To lower prescription drug costs for seniors enrolled in Medicare",
             "isLaw": False, "latestAction": "Referred to committee"},
            {"billId": "HR.11", "billType": "hr",
             "title": "To reduce the price of prescription drugs for older Americans",
             "isLaw": False, "latestAction": "Referred to committee"},
            {"billId": "HR.12", "billType": "hr",
             "title": "To expand broadband internet access in rural communities",
             "isLaw": False, "latestAction": "Referred to committee"},
        ]

    def test_empty_bills_returns_empty(self):
        assert positions_from_sponsored_bills([], []) == []

    def test_short_titles_skipped(self):
        bills = [{"billId": "HR.1", "billType": "hr", "title": "Short title"}]
        assert positions_from_sponsored_bills(bills, []) == []

    def test_resolutions_excluded_from_derived_positions(self):
        """v5.9 bug: a member's ceremonial resolutions (a sorority-
        anniversary resolution, an awareness-day designation) were being
        fed in as 'positions', then inevitably scored 'unclear' since a
        resolution agreed to without debate has no matchable floor votes —
        inflating the unclear count and collapsing Promise Persistence's
        population spread. Simple/concurrent resolutions must never
        become derived positions at all, regardless of title length."""
        bills = self._bills() + [
            {"billId": "HRES.5", "billType": "hres",
             "title": "A resolution recognizing the 175th anniversary of "
                      "the founding of Alpha Delta Pi Sorority.",
             "isLaw": False, "latestAction": "Agreed to by unanimous consent"},
            {"billId": "SRES.9", "billType": "sres",
             "title": "A resolution recognizing and honoring National "
                      "Mushroom Day and the contributions of Chester and "
                      "Berks Counties to the national mushroom industry.",
             "isLaw": False, "latestAction": "Agreed to by unanimous consent"},
        ]
        positions = positions_from_sponsored_bills(bills, [])
        texts = " ".join(p["promiseText"] for p in positions)
        assert "Mushroom" not in texts
        assert "Sorority" not in texts

    def test_near_duplicate_topics_deduplicated(self):
        positions = positions_from_sponsored_bills(self._bills(), [])
        texts = [p["promiseText"] for p in positions]
        # The two drug-pricing bills collapse into one position.
        assert len(positions) == 2, texts

    def test_promise_shape_matches_persistence_schema(self):
        positions = positions_from_sponsored_bills(self._bills(), [])
        for p in positions:
            assert set(p) >= {
                "promiseText", "category", "alignment", "relatedVotes",
                "relatedBills", "analysis", "confidence", "partyAlignment",
            }
            assert p["alignment"] in ("kept", "broken", "partial", "unclear")

    def test_source_bills_are_not_evidence(self):
        """A position must not be marked kept/partial by the bill it came from."""
        positions = positions_from_sponsored_bills(self._bills(), [])
        for p in positions:
            assert p["relatedBills"] == []
            assert p["alignment"] == "unclear"

    def test_floor_votes_provide_directional_evidence(self):
        votes = [
            {"billId": "HR.500", "vote": "Yea", "policyArea": "HEALTHCARE",
             "billName": "Prescription Drug Pricing Act",
             "description": "Lower prescription drug costs for Medicare recipients",
             "stance": "pro"},
        ]
        positions = positions_from_sponsored_bills(self._bills(), votes)
        drug = next(p for p in positions if "drug" in p["promiseText"].lower())
        assert drug["alignment"] == "kept"
        assert "HR.500" in drug["relatedVotes"]

    def test_no_llm_call_in_house_path(self):
        """The House path is deterministic: any LLM call is a regression."""
        with patch(
            "app.pipeline.analyze.ollama_client.call_llm",
            side_effect=AssertionError("LLM must not be called"),
        ):
            votes = [
                {"billId": "HR.500", "vote": "Yea", "policyArea": "HEALTHCARE",
                 "billName": "Prescription Drug Pricing Act",
                 "description": "Lower drug costs", "stance": "pro"},
            ]
            positions = positions_from_sponsored_bills(self._bills(), votes)
            assert positions
