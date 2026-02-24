"""Tests for the cross-reference analysis module.

Covers the algorithmic (non-LLM) components:
  - Alignment inference from analysis text
  - Hallucination detection via platform word overlap
  - Filler analysis filtering
  - Promise parsing and validation
  - Key vote selection scoring
  - Lobbying match detection (with mocked vector search)
"""

import pytest
from unittest.mock import patch

from app.pipeline.analyze.cross_reference import (
    _infer_alignment_from_analysis,
    _platform_word_overlap,
    _parse_promises,
    _extract_platform_topics,
    select_key_votes,
    detect_lobbying_matches,
)


# ── Alignment inference ──────────────────────────────────────────


class TestInferAlignment:
    """The LLM often writes correct analysis but picks the wrong label.
    _infer_alignment_from_analysis should override based on textual evidence."""

    def test_kept_signals_override_broken_label(self):
        analysis = "Senator voted Yea on HR 1234, which aligns with their stated support for clean energy."
        assert _infer_alignment_from_analysis(analysis, "broken") == "kept"

    def test_broken_signals_override_kept_label(self):
        analysis = "Senator voted against the minimum wage bill, contradicting their campaign pledge."
        assert _infer_alignment_from_analysis(analysis, "kept") == "broken"

    def test_mixed_signals_downgrade_to_unclear(self):
        analysis = "Senator supports the bill in principle but voted against the final version."
        assert _infer_alignment_from_analysis(analysis, "kept") == "unclear"

    def test_no_signals_trusts_llm_label(self):
        analysis = "The bill concerns taxation of overseas earnings."
        assert _infer_alignment_from_analysis(analysis, "partial") == "partial"

    def test_empty_analysis_trusts_label(self):
        assert _infer_alignment_from_analysis("", "kept") == "kept"
        assert _infer_alignment_from_analysis("", "broken") == "broken"

    def test_empty_analysis_with_invalid_label(self):
        assert _infer_alignment_from_analysis("", "maybe") == "unclear"

    def test_weak_analysis_downgraded(self):
        analysis = "This is related to his stated platform on healthcare."
        # _WEAK_ANALYSIS pattern: "which is (not )?related to his/her/their (stated )?platform"
        analysis_weak = "This PAC which is related to their stated platform on healthcare gave $5000."
        assert _infer_alignment_from_analysis(analysis_weak, "kept") == "unclear"

    def test_donor_as_evidence_downgraded(self):
        analysis = "Donations from PHARMA PAC indicates support for deregulation."
        assert _infer_alignment_from_analysis(analysis, "kept") == "unclear"

    def test_genuine_alignment_not_false_positive(self):
        analysis = (
            "Senator voted Yea on the Affordable Care Act expansion, "
            "consistent with their promise to lower prescription drug costs."
        )
        assert _infer_alignment_from_analysis(analysis, "kept") == "kept"

    def test_genuine_broken_not_false_positive(self):
        analysis = (
            "Senator voted against the climate bill, contradicting their "
            "campaign pledge to fight climate change."
        )
        assert _infer_alignment_from_analysis(analysis, "broken") == "broken"


# ── Platform word overlap (hallucination detection) ──────────────


class TestPlatformWordOverlap:
    """Low overlap between promise text and actual platform = hallucinated."""

    PLATFORM = (
        "I will fight to lower prescription drug costs, expand Medicare "
        "coverage, protect Social Security benefits, and invest in clean "
        "energy infrastructure for American families."
    )

    def test_high_overlap_real_promise(self):
        promise = "Lower prescription drug costs for Medicare recipients"
        overlap = _platform_word_overlap(promise, self.PLATFORM)
        assert overlap >= 0.5

    def test_low_overlap_hallucinated_promise(self):
        promise = "Build a space elevator to colonize Mars by 2030"
        overlap = _platform_word_overlap(promise, self.PLATFORM)
        assert overlap < 0.3

    def test_no_platform_returns_full(self):
        assert _platform_word_overlap("anything here", "") == 1.0

    def test_very_short_promise_returns_full(self):
        assert _platform_word_overlap("tax", self.PLATFORM) == 1.0

    def test_stopwords_excluded(self):
        promise = "the and for with from into about"
        overlap = _platform_word_overlap(promise, self.PLATFORM)
        assert overlap == 1.0  # no significant words


# ── Promise parsing and validation ───────────────────────────────


class TestParsePromises:
    """Test the full promise parsing pipeline including all quality guards."""

    ALL_VOTES = [
        {"billId": "HR.1", "vote": "Yea", "policyArea": "HEALTHCARE"},
        {"billId": "HR.2", "vote": "Nay", "policyArea": "ENERGY"},
        {"billId": "HR.3", "vote": "Yea", "policyArea": "DEFENSE"},
        {"billId": "S.100", "vote": "Yea", "policyArea": "TAXES"},
    ]

    PLATFORM = (
        "I will fight to lower prescription drug costs and expand Medicare. "
        "I support clean energy investment and climate action. "
        "I will strengthen border security and reform immigration."
    )

    def test_valid_promises_parsed(self):
        raw = [
            {
                "promiseText": "Lower prescription drug costs and expand Medicare",
                "category": "healthcare",
                "alignment": "kept",
                "analysis": "Senator voted Yea on HR.1, supporting healthcare expansion.",
                "relatedBills": ["HR.1"],
            }
        ]
        result = _parse_promises(raw, self.ALL_VOTES, self.PLATFORM)
        assert len(result) == 1
        assert result[0]["alignment"] == "kept"
        assert result[0]["relatedVotes"] == ["HR.1"]

    def test_invalid_bill_ids_filtered(self):
        raw = [
            {
                "promiseText": "Lower prescription drug costs for Medicare",
                "category": "healthcare",
                "alignment": "kept",
                "analysis": "Voted for this.",
                "relatedBills": ["HR.1", "FAKE.999"],
            }
        ]
        result = _parse_promises(raw, self.ALL_VOTES, self.PLATFORM)
        assert result[0]["relatedVotes"] == ["HR.1"]

    def test_hallucinated_promise_downgraded(self):
        raw = [
            {
                "promiseText": "Build a space elevator for intergalactic commerce",
                "category": "other",
                "alignment": "kept",
                "analysis": "Clearly supports space programs.",
                "relatedBills": [],
            }
        ]
        result = _parse_promises(raw, self.ALL_VOTES, self.PLATFORM)
        assert result[0]["alignment"] == "unclear"
        assert "could not be verified" in result[0]["analysis"]

    def test_duplicate_bill_sets_downgraded(self):
        raw = [
            {
                "promiseText": "Lower prescription drug costs for families",
                "category": "healthcare",
                "alignment": "kept",
                "analysis": "Supported healthcare reform.",
                "relatedBills": ["HR.1", "HR.2"],
            },
            {
                "promiseText": "Expand Medicare coverage for seniors",
                "category": "healthcare",
                "alignment": "kept",
                "analysis": "Supported Medicare expansion.",
                "relatedBills": ["HR.1", "HR.2"],
            },
        ]
        result = _parse_promises(raw, self.ALL_VOTES, self.PLATFORM)
        for p in result:
            assert p["alignment"] == "unclear"
            assert p["relatedVotes"] == []

    def test_filler_analysis_stripped(self):
        raw = [
            {
                "promiseText": "Lower prescription drug costs for families",
                "category": "healthcare",
                "alignment": "kept",
                "analysis": "Senator has received funding from healthcare PACs.",
                "relatedBills": ["HR.1"],
            }
        ]
        result = _parse_promises(raw, self.ALL_VOTES, self.PLATFORM)
        assert result[0]["analysis"] == ""

    def test_invalid_alignment_normalized(self):
        raw = [
            {
                "promiseText": "Lower prescription drug costs and expand Medicare",
                "category": "healthcare",
                "alignment": "partially kept",
                "analysis": "Some progress made.",
                "relatedBills": [],
            }
        ]
        result = _parse_promises(raw, self.ALL_VOTES, self.PLATFORM)
        assert result[0]["alignment"] in ("kept", "broken", "partial", "unclear")

    def test_invalid_category_defaults_to_other(self):
        raw = [
            {
                "promiseText": "Lower prescription drug costs for families",
                "category": "space_exploration",
                "alignment": "unclear",
                "analysis": "",
                "relatedBills": [],
            }
        ]
        result = _parse_promises(raw, self.ALL_VOTES, self.PLATFORM)
        assert result[0]["category"] == "other"

    def test_empty_input(self):
        assert _parse_promises(None) == []
        assert _parse_promises([]) == []
        assert _parse_promises("not a list") == []

    def test_max_five_related_bills(self):
        raw = [
            {
                "promiseText": "Lower prescription drug costs for Medicare",
                "category": "healthcare",
                "alignment": "kept",
                "analysis": "Supports healthcare.",
                "relatedBills": ["HR.1", "HR.2", "HR.3", "S.100", "HR.1", "HR.2", "HR.3"],
            }
        ]
        result = _parse_promises(raw, self.ALL_VOTES, self.PLATFORM)
        assert len(result[0]["relatedVotes"]) <= 5

    def test_alignment_inferred_from_analysis_not_label(self):
        """LLM says 'broken' but analysis text says 'aligns with' -- should override."""
        raw = [
            {
                "promiseText": "Expand Medicare coverage for seniors",
                "category": "healthcare",
                "alignment": "broken",
                "analysis": "Senator voted Yea on HR.1, which aligns with this promise to expand healthcare.",
                "relatedBills": ["HR.1"],
            }
        ]
        result = _parse_promises(raw, self.ALL_VOTES, self.PLATFORM)
        assert result[0]["alignment"] == "kept"


# ── Platform topic extraction ────────────────────────────────────


class TestExtractPlatformTopics:

    def test_splits_multiline(self):
        text = "Fight climate change\nLower drug costs\nReform immigration"
        topics = _extract_platform_topics(text)
        assert len(topics) >= 2

    def test_strips_bullet_markers(self):
        text = "• Fight climate change and invest in green energy\n- Lower drug costs for seniors"
        topics = _extract_platform_topics(text)
        for t in topics:
            assert not t.startswith("•")
            assert not t.startswith("-")

    def test_max_topics_capped(self):
        text = "\n".join(f"Topic number {i} about something important" for i in range(20))
        topics = _extract_platform_topics(text, max_topics=4)
        assert len(topics) <= 4

    def test_empty_input(self):
        assert _extract_platform_topics("") == []

    def test_single_blob(self):
        text = "A single long paragraph about climate change and energy policy with no line breaks at all"
        topics = _extract_platform_topics(text)
        assert len(topics) >= 1

    def test_short_lines_skipped(self):
        text = "Short\nAlso short\nThis is a meaningful line about healthcare reform policy"
        topics = _extract_platform_topics(text)
        assert len(topics) == 1


# ── Key vote selection ───────────────────────────────────────────


class TestSelectKeyVotes:
    """Key vote selection uses a scoring heuristic, not LLM."""

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

    def test_donor_industry_match_boosted(self):
        votes = [
            self._make_vote("HR.1", policy="DEFENSE"),
            self._make_vote("HR.2", policy="HEALTHCARE"),
        ]
        donors = [{"name": "Pharma Corp", "industry": "PHARMA", "type": "PAC"}]
        ids = select_key_votes(votes, donors)
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
        votes = [
            self._make_vote("HR.1", policy="HEALTHCARE"),
        ]
        donors = [{"name": "Friends of Cruz", "industry": "PHARMA", "type": "CandidateAffiliated"}]
        ids_with = select_key_votes(votes, donors)
        ids_without = select_key_votes(votes, [])
        assert ids_with == ids_without


# ── Lobbying match detection ─────────────────────────────────────


class TestDetectLobbyingMatches:
    """Algorithmic donor-vote industry overlap detection."""

    @patch("app.pipeline.analyze.cross_reference.search_bills")
    def test_match_detected(self, mock_search):
        mock_search.return_value = [
            {"billId": "HR.1", "distance": 0.5}
        ]
        donors = [
            {"name": "Pfizer Inc", "industry": "PHARMA", "type": "PAC", "total": 50000},
        ]
        votes = [
            {"billId": "HR.1", "vote": "Yea", "billName": "Drug Pricing", "policyArea": "HEALTHCARE"},
        ]
        matches = detect_lobbying_matches(donors, votes)
        assert len(matches) == 1
        assert matches[0]["lobbyistOrg"] == "Pfizer Inc"
        assert matches[0]["industry"] == "PHARMA"
        assert matches[0]["senatorVoteAligned"] is True

    @patch("app.pipeline.analyze.cross_reference.search_bills")
    def test_no_match_different_policy(self, mock_search):
        mock_search.return_value = [
            {"billId": "HR.1", "distance": 0.5}
        ]
        donors = [
            {"name": "Pfizer Inc", "industry": "PHARMA", "type": "PAC", "total": 50000},
        ]
        votes = [
            {"billId": "HR.1", "vote": "Yea", "billName": "Military Bill", "policyArea": "DEFENSE"},
        ]
        matches = detect_lobbying_matches(donors, votes)
        assert len(matches) == 0

    @patch("app.pipeline.analyze.cross_reference.search_bills")
    def test_candidate_affiliated_excluded(self, mock_search):
        donors = [
            {"name": "Friends of Cruz", "industry": "PHARMA", "type": "CandidateAffiliated", "total": 100000},
        ]
        votes = [
            {"billId": "HR.1", "vote": "Yea", "billName": "Drug Bill", "policyArea": "HEALTHCARE"},
        ]
        matches = detect_lobbying_matches(donors, votes)
        assert len(matches) == 0
        mock_search.assert_not_called()

    @patch("app.pipeline.analyze.cross_reference.search_bills")
    def test_other_industry_excluded(self, mock_search):
        donors = [
            {"name": "Random LLC", "industry": "OTHER", "type": "PAC", "total": 5000},
        ]
        votes = [
            {"billId": "HR.1", "vote": "Yea", "billName": "Some Bill", "policyArea": "HEALTHCARE"},
        ]
        matches = detect_lobbying_matches(donors, votes)
        assert len(matches) == 0
        mock_search.assert_not_called()

    def test_empty_donors(self):
        assert detect_lobbying_matches([], [{"billId": "HR.1", "vote": "Yea"}]) == []

    def test_empty_votes(self):
        donors = [{"name": "Corp", "industry": "PHARMA", "type": "PAC", "total": 5000}]
        assert detect_lobbying_matches(donors, []) == []

    @patch("app.pipeline.analyze.cross_reference.search_bills")
    def test_max_eight_matches(self, mock_search):
        mock_search.return_value = [
            {"billId": f"HR.{i}", "distance": 0.3} for i in range(10)
        ]
        donors = [
            {"name": f"Corp {i}", "industry": "PHARMA", "type": "PAC", "total": 1000 * (10 - i)}
            for i in range(10)
        ]
        votes = [
            {"billId": f"HR.{i}", "vote": "Yea", "billName": f"Bill {i}", "policyArea": "HEALTHCARE"}
            for i in range(10)
        ]
        matches = detect_lobbying_matches(donors, votes)
        assert len(matches) <= 8
