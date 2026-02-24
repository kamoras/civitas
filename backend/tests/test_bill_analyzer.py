"""Tests for the bill analyzer hybrid classification.

Tests:
1. Procedural keyword detection
2. Policy area embedding classification
3. Validation logic
"""

import pytest

from app.pipeline.analyze.bill_analyzer import (
    PROCEDURAL_KEYWORDS,
    classify_policy_area,
    _validate_classifications,
)


class TestProceduralDetection:
    """Procedural keywords should be caught without LLM."""

    @pytest.mark.parametrize(
        "text",
        [
            "Nomination of Jane Smith to be Under Secretary of Defense",
            "Motion to proceed to consider S. 1234",
            "Cloture on the nomination of John Doe",
            "Motion to table amendment No. 456",
            "Motion to reconsider the vote",
            "Appointment of conferees",
            "Executive calendar number 789",
        ],
    )
    def test_procedural_keywords_detected(self, text):
        area, confidence = classify_policy_area(text)
        assert area == "PROCEDURAL"
        assert confidence == 1.0

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
            ("Affordable Care Act expansion of Medicare and Medicaid coverage", "HEALTHCARE"),
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
