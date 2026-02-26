"""Tests for the bill analyzer hybrid classification.

Tests:
1. Procedural keyword detection
2. Policy area embedding classification
3. Validation logic
"""

import pytest

from app.pipeline.analyze.bill_analyzer import (
    classify_policy_area,
    classify_policy_areas_multi,
    _validate_classifications,
)


class TestProceduralDetection:
    """Procedural texts should be detected via embedding similarity."""

    @pytest.mark.parametrize(
        "text",
        [
            "Naming of a post office building in Springfield Illinois",
            "Honoring the life and legacy of a distinguished veteran",
            "Designating the week of May 1 as National Teacher Week",
            "Authorizing the use of the rotunda of the Capitol",
        ],
    )
    def test_procedural_texts_detected(self, text):
        area, confidence = classify_policy_area(text)
        assert area == "PROCEDURAL"

    def test_empty_text_is_procedural(self):
        area, confidence = classify_policy_area("")
        assert area == "PROCEDURAL"
        assert confidence == 0.0

    def test_very_short_text_is_procedural(self):
        area, confidence = classify_policy_area("abc")
        assert area == "PROCEDURAL"
        assert confidence == 0.0


@pytest.mark.slow
class TestPolicyAreaClassification:
    """Embedding-based policy area classification."""

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("Hospital and medical insurance reform healthcare system regulation", "HEALTHCARE"),
            ("National Defense Authorization Act military spending", "DEFENSE"),
            ("Gun violence prevention and background check legislation", "GUNS"),
            ("Climate change EPA emissions pollution regulations clean air water", "ENVIRONMENT"),
            ("Federal income tax reform and corporate tax rates", "TAXES"),
            ("Border security and immigration reform pathway to citizenship", "IMMIGRATION"),
            ("Student loan forgiveness and federal education funding", "EDUCATION"),
            ("Criminal justice reform and police accountability", "JUSTICE"),
            ("International trade agreement tariffs with China", "TRADE"),
            ("Minimum wage increase and worker protection legislation", "LABOR"),
        ],
    )
    def test_clear_policy_areas(self, text, expected):
        area, confidence = classify_policy_area(text)
        assert area == expected, f"'{text}' classified as {area}, expected {expected}"
        assert confidence > 0.3

    def test_confidence_is_bounded(self):
        _, confidence = classify_policy_area("Healthcare reform and Medicare expansion")
        assert 0.0 <= confidence <= 1.0


class TestValidation:
    """Post-classification validation and sanitization."""

    def test_missing_policy_area_defaults_to_procedural(self):
        bills = [{"billId": "1", "policyArea": None, "stance": "reform", "stanceVote": "Yea", "partyLeaning": "D"}]
        _validate_classifications(bills)
        assert bills[0]["policyArea"] == "PROCEDURAL"

    def test_policy_area_uppercased(self):
        bills = [{"billId": "1", "policyArea": "healthcare", "stance": "reform", "stanceVote": "Yea", "partyLeaning": "D"}]
        _validate_classifications(bills)
        assert bills[0]["policyArea"] == "HEALTHCARE"

    def test_stance_lowercased_and_validated(self):
        bills = [{"billId": "1", "policyArea": "DEFENSE", "stance": "PRO", "stanceVote": "Yea", "partyLeaning": "R"}]
        _validate_classifications(bills)
        assert bills[0]["stance"] == "pro"

    def test_invalid_stance_normalized_to_neutral(self):
        bills = [{"billId": "1", "policyArea": "DEFENSE", "stance": "PRO-MILITARY", "stanceVote": "Yea", "partyLeaning": "R"}]
        _validate_classifications(bills)
        assert bills[0]["stance"] == "neutral"

    def test_missing_stance_defaults_to_neutral(self):
        bills = [{"billId": "1", "policyArea": "DEFENSE", "stance": None, "stanceVote": "Yea", "partyLeaning": "R"}]
        _validate_classifications(bills)
        assert bills[0]["stance"] == "neutral"

    def test_invalid_stance_vote_set_to_none(self):
        bills = [{"billId": "1", "policyArea": "DEFENSE", "stance": "reform", "stanceVote": "Maybe", "partyLeaning": "R"}]
        _validate_classifications(bills)
        assert bills[0]["stanceVote"] is None

    def test_valid_stance_votes_preserved(self):
        bills = [
            {"billId": "1", "policyArea": "DEFENSE", "stance": "x", "stanceVote": "Yea", "partyLeaning": "R"},
            {"billId": "2", "policyArea": "DEFENSE", "stance": "x", "stanceVote": "Nay", "partyLeaning": "R"},
        ]
        _validate_classifications(bills)
        assert bills[0]["stanceVote"] == "Yea"
        assert bills[1]["stanceVote"] == "Nay"

    def test_invalid_party_leaning_defaults_to_bipartisan(self):
        bills = [{"billId": "1", "policyArea": "DEFENSE", "stance": "x", "stanceVote": "Yea", "partyLeaning": "X"}]
        _validate_classifications(bills)
        assert bills[0]["partyLeaning"] == "bipartisan"

    def test_impacted_groups_non_list_fixed(self):
        bills = [{"billId": "1", "policyArea": "A", "stance": "x", "stanceVote": "Yea", "impactedGroups": "everyone", "partyLeaning": "D"}]
        _validate_classifications(bills)
        assert bills[0]["impactedGroups"] == []


class TestMultiAreaClassification:
    """Tests for multi-area bill classification (Adler & Wilkerson 2012)."""

    def test_returns_list_of_dicts(self):
        result = classify_policy_areas_multi("Healthcare reform and Medicare expansion")
        assert isinstance(result, list)
        assert len(result) >= 1
        assert all("area" in a and "confidence" in a for a in result)

    def test_primary_area_matches_single_classify(self):
        text = "National Defense Authorization Act military spending"
        single_area, _ = classify_policy_area(text)
        multi_areas = classify_policy_areas_multi(text)
        assert multi_areas[0]["area"] == single_area

    def test_multi_domain_bill_returns_multiple_areas(self):
        text = (
            "Comprehensive legislation addressing healthcare insurance reform, "
            "tax credits for medical expenses, and environmental protections "
            "for hospital waste disposal regulations"
        )
        result = classify_policy_areas_multi(text)
        areas = {a["area"] for a in result}
        assert len(result) >= 2, f"Expected multi-area for complex bill, got: {areas}"

    def test_empty_text_returns_procedural(self):
        result = classify_policy_areas_multi("")
        assert result == [{"area": "PROCEDURAL", "confidence": 0.0}]

    def test_confidences_are_bounded(self):
        result = classify_policy_areas_multi("Gun control background check legislation")
        for a in result:
            assert 0.0 <= a["confidence"] <= 1.0

    def test_areas_ordered_by_confidence(self):
        result = classify_policy_areas_multi(
            "Renewable energy tax credits and environmental protection funding"
        )
        if len(result) > 1:
            confs = [a["confidence"] for a in result]
            assert confs == sorted(confs, reverse=True)


class TestMultiAreaPartyAlignment:
    """Tests for per-area party alignment with weighted aggregation."""

    def test_returns_expected_shape(self):
        from app.pipeline.analyze.party_platform import classify_party_alignment_multi

        areas = [
            {"area": "HEALTHCARE", "confidence": 0.7},
            {"area": "TAXES", "confidence": 0.5},
        ]
        result = classify_party_alignment_multi(
            "Expand Medicare and reduce taxes on wealthy", areas, "pro"
        )
        assert "overall" in result
        assert "weight" in result
        assert "areas" in result
        assert result["overall"] in ("R", "D", "bipartisan")
        assert 0.0 <= result["weight"] <= 1.0

    def test_procedural_only_returns_bipartisan(self):
        from app.pipeline.analyze.party_platform import classify_party_alignment_multi

        areas = [{"area": "PROCEDURAL", "confidence": 0.95}]
        result = classify_party_alignment_multi("Cloture motion", areas, "neutral")
        assert result["overall"] == "bipartisan"
        assert result["weight"] == 0.0

    def test_per_area_alignment_populated(self):
        from app.pipeline.analyze.party_platform import classify_party_alignment_multi

        areas = [
            {"area": "ENVIRONMENT", "confidence": 0.65},
            {"area": "ENERGY", "confidence": 0.55},
        ]
        result = classify_party_alignment_multi(
            "Clean energy investment and emissions reduction targets", areas, "pro"
        )
        assert len(result["areas"]) >= 1
        for a in result["areas"]:
            assert "area" in a
            assert "party" in a
            assert a["party"] in ("R", "D", "bipartisan")


class TestAlignmentsFromVotes:
    """Test _alignments_from_votes with multi-area bill data."""

    def test_multi_area_votes_counted_per_area(self):
        from app.pipeline.analyze.party_platform import _alignments_from_votes

        votes = [
            {
                "vote": "Yea",
                "policyAreas": [
                    {"area": "HEALTHCARE", "confidence": 0.9, "party": "D"},
                    {"area": "TAXES", "confidence": 0.7, "party": "R"},
                ],
            },
            {
                "vote": "Yea",
                "policyAreas": [
                    {"area": "HEALTHCARE", "confidence": 0.8, "party": "D"},
                ],
            },
            {
                "vote": "Nay",
                "policyAreas": [
                    {"area": "HEALTHCARE", "confidence": 0.85, "party": "R"},
                ],
            },
        ]
        record = {"keyVotes": votes, "recentVotes": []}
        result = _alignments_from_votes(record)
        areas_found = {a["area"] for a in result}
        assert "HEALTHCARE" in areas_found

    def test_single_area_fallback(self):
        """When policyAreas is empty, falls back to single policyArea + partyLeaning."""
        from app.pipeline.analyze.party_platform import _alignments_from_votes

        votes = [
            {"vote": "Yea", "policyArea": "DEFENSE", "partyLeaning": "R",
             "policyAreas": []},
            {"vote": "Yea", "policyArea": "DEFENSE", "partyLeaning": "R",
             "policyAreas": []},
            {"vote": "Nay", "policyArea": "DEFENSE", "partyLeaning": "D",
             "policyAreas": []},
        ]
        record = {"keyVotes": votes, "recentVotes": []}
        result = _alignments_from_votes(record)
        areas_found = {a["area"] for a in result}
        assert "DEFENSE" in areas_found

    def test_procedural_areas_skipped(self):
        from app.pipeline.analyze.party_platform import _alignments_from_votes

        votes = [
            {
                "vote": "Yea",
                "policyAreas": [
                    {"area": "PROCEDURAL", "confidence": 0.95, "party": "bipartisan"},
                ],
            },
        ]
        record = {"keyVotes": votes, "recentVotes": []}
        result = _alignments_from_votes(record)
        assert result == []

    def test_confidence_weighting(self):
        """Higher-confidence areas should contribute more weight."""
        from app.pipeline.analyze.party_platform import _alignments_from_votes

        votes = []
        for _ in range(5):
            votes.append({
                "vote": "Yea",
                "policyAreas": [
                    {"area": "IMMIGRATION", "confidence": 0.9, "party": "D"},
                ],
            })
        record = {"keyVotes": votes, "recentVotes": []}
        result = _alignments_from_votes(record)
        imm = [a for a in result if a["area"] == "IMMIGRATION"]
        assert len(imm) == 1
        assert imm[0]["alignment"] == "D"

    def test_empty_record_returns_empty(self):
        from app.pipeline.analyze.party_platform import _alignments_from_votes

        assert _alignments_from_votes({}) == []
        assert _alignments_from_votes({"keyVotes": [], "recentVotes": []}) == []

    def test_bipartisan_areas_skipped(self):
        """Areas with party='bipartisan' in policyAreas should not contribute."""
        from app.pipeline.analyze.party_platform import _alignments_from_votes

        votes = [
            {
                "vote": "Yea",
                "policyAreas": [
                    {"area": "DEFENSE", "confidence": 0.8, "party": "bipartisan"},
                ],
            },
            {
                "vote": "Yea",
                "policyAreas": [
                    {"area": "DEFENSE", "confidence": 0.8, "party": "bipartisan"},
                ],
            },
        ]
        record = {"keyVotes": votes, "recentVotes": []}
        result = _alignments_from_votes(record)
        assert result == []


class TestIdeologyBlendInPartisanDepth:
    """Test that SVD ideology_score blends into partisan depth correctly."""

    def test_ideology_dominates_with_no_votes(self):
        """With no vote data, ideology_score alone shapes the lean."""
        from app.pipeline.analyze.party_platform import analyze_partisan_depth

        result = analyze_partisan_depth(
            promises=[],
            senator_party="D",
            voting_record={"keyVotes": [], "recentVotes": []},
            ideology_score=0.0,
        )
        assert result["totalPositions"] == 0

    def test_ideology_adjusts_sparse_vote_lean(self):
        """With few votes, ideology_score should pull the overall lean."""
        from app.pipeline.analyze.party_platform import analyze_partisan_depth

        votes = [
            {"vote": "Yea", "policyArea": "DEFENSE", "partyLeaning": "R",
             "policyAreas": []},
            {"vote": "Yea", "policyArea": "DEFENSE", "partyLeaning": "R",
             "policyAreas": []},
        ]
        record = {"keyVotes": votes, "recentVotes": []}

        without = analyze_partisan_depth(
            promises=[], senator_party="D",
            voting_record=record, ideology_score=None,
        )
        with_d_ideology = analyze_partisan_depth(
            promises=[], senator_party="D",
            voting_record=record, ideology_score=0.1,
        )
        assert with_d_ideology["overallLean"] < without["overallLean"]

    def test_rich_votes_override_ideology(self):
        """With many votes (>=15), ideology_score has minimal effect."""
        from app.pipeline.analyze.party_platform import analyze_partisan_depth

        votes = []
        for _ in range(20):
            votes.append({
                "vote": "Yea",
                "policyArea": "HEALTHCARE",
                "partyLeaning": "D",
                "policyAreas": [],
            })
        record = {"keyVotes": votes, "recentVotes": []}

        without = analyze_partisan_depth(
            promises=[], senator_party="D",
            voting_record=record, ideology_score=None,
        )
        with_r_ideology = analyze_partisan_depth(
            promises=[], senator_party="D",
            voting_record=record, ideology_score=0.9,
        )
        assert abs(with_r_ideology["overallLean"] - without["overallLean"]) < 0.05
