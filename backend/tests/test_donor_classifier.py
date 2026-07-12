"""Tests for the hybrid donor classifier.

Tests the tiered classification strategy:
1. FEC committee type codes (structured metadata)
2. Semantic embedding-based classification
3. Learning store lookup
4. kNN fallback
"""

import pytest
from unittest.mock import patch

from app.models import LearnedClassification
from app.pipeline.analyze.donor_classifier_ai import (
    FEC_ENTITY_TYPE_MAP,
    classify_donor_type_from_fec,
    classify_donor_type_semantic,
    is_skip_entity,
    classify_donors_hybrid,
)


class TestFECTypeClassification:
    """Tier 1: FEC entity type and receipt type codes."""

    @pytest.mark.parametrize(
        "entity_type, expected",
        [
            ("PAC", "PAC"),
            ("ORG", "Org/Employees"),
            ("IND", "Org/Employees"),
            ("CCM", "CandidateAffiliated"),
            ("CAN", "Self-Funded"),
            ("PTY", "Party/Ideological"),
        ],
    )
    def test_known_entity_types(self, entity_type, expected):
        receipt = {"entity_type": entity_type}
        assert classify_donor_type_from_fec(receipt) == expected

    def test_com_defers_to_semantic_classifier(self):
        """COM (generic committee) is ambiguous — returns None to defer to
        embedding-based classification which can distinguish corporate
        employee PACs from purely political PACs."""
        receipt = {"entity_type": "COM"}
        assert classify_donor_type_from_fec(receipt) is None

    def test_affiliated_receipt_types(self):
        for rt in ("18G", "18H", "18K", "18J", "22G", "22H"):
            receipt = {"receipt_type": rt}
            assert classify_donor_type_from_fec(receipt) == "CandidateAffiliated"

    def test_unknown_entity_type_returns_none(self):
        receipt = {"entity_type": "ZZZ"}
        assert classify_donor_type_from_fec(receipt) is None

    def test_missing_fields_returns_none(self):
        assert classify_donor_type_from_fec({}) is None

    def test_fec_entity_type_map_covers_expected_codes(self):
        assert len(FEC_ENTITY_TYPE_MAP) == 6

class TestSkipDetection:
    """Tier 2: Payment processor skip detection."""

    @pytest.mark.parametrize(
        "name",
        ["WINRED TECHNICAL SERVICES", "ACTBLUE", "ANEDOT INC"],
    )
    def test_skip_entities(self, name):
        assert is_skip_entity(name) is True

    def test_non_skip_entities(self):
        assert is_skip_entity("PFIZER INC") is False
        assert is_skip_entity("GOLDMAN SACHS") is False


class TestSemanticClassification:
    """Tier 2: Embedding-based semantic donor type classification."""

    def test_candidate_self_funded_personal_contribution(self):
        """When donor name matches the candidate's name, it's a self-funded contribution."""
        result = classify_donor_type_semantic(
            "CRUZ, RAPHAEL EDWARD TED",
            candidate_name="CRUZ, RAFAEL EDWARD (TED)",
        )
        assert result == "Self-Funded"

    def test_returns_none_for_empty_name(self):
        assert classify_donor_type_semantic("") is None
        assert classify_donor_type_semantic("AB") is None


@pytest.mark.slow
class TestHybridClassification:
    """Integration: full tiered classification via classify_donors_hybrid."""

    @pytest.mark.asyncio
    async def test_empty_input(self):
        result = await classify_donors_hybrid([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_fec_tier(self, db_session):
        donors = [
            {
                "name": "Test PAC",
                "amount": 5000,
                "fec_receipt": {"entity_type": "PAC"},
            }
        ]
        result = await classify_donors_hybrid(donors, db_session=db_session)
        assert "TEST PAC" in result
        assert result["TEST PAC"]["type"] == "PAC"

    @pytest.mark.asyncio
    async def test_skip_tier(self, db_session):
        donors = [{"name": "ACTBLUE", "amount": 1000}]
        result = await classify_donors_hybrid(donors, db_session=db_session)
        assert "ACTBLUE" in result
        assert result["ACTBLUE"]["type"] == "SKIP"
        assert result["ACTBLUE"]["skip"] is True

    @pytest.mark.asyncio
    async def test_learning_store_tier(self, db_session):
        db_session.add(LearnedClassification(
            entity_name="MYSTERY DONOR",
            entity_type="donor_type",
            value="Org/Employees",
            confidence=0.9,
            source="llm",
        ))
        db_session.add(LearnedClassification(
            entity_name="MYSTERY DONOR",
            entity_type="industry",
            value="TECH",
            confidence=0.9,
            source="llm",
        ))
        db_session.flush()

        donors = [{"name": "Mystery Donor", "amount": 2000}]
        result = await classify_donors_hybrid(donors, db_session=db_session)
        assert "MYSTERY DONOR" in result
        assert result["MYSTERY DONOR"]["type"] == "Org/Employees"
        assert result["MYSTERY DONOR"]["industry"] == "TECH"

    @pytest.mark.asyncio
    async def test_deduplication(self, db_session):
        donors = [
            {"name": "Test Corp", "amount": 1000, "fec_receipt": {"entity_type": "PAC"}},
            {"name": "TEST CORP", "amount": 2000, "fec_receipt": {"entity_type": "PAC"}},
            {"name": "test corp", "amount": 500, "fec_receipt": {"entity_type": "PAC"}},
        ]
        result = await classify_donors_hybrid(donors, db_session=db_session)
        assert len(result) == 1
        assert "TEST CORP" in result

    @pytest.mark.asyncio
    async def test_unknown_donors_classified_via_nn(self, db_session):
        """Donors with unknown type AND industry should be queued for kNN."""
        donors = [{"name": "Completely Unknown Entity XYZ", "amount": 500}]
        # Patch both upstream embedding tiers so the donor stays unclassifiable
        # and truly falls through to the NN step (embedding similarity scores
        # from newer sentence-transformers versions may classify it otherwise).
        with patch(
            "app.pipeline.analyze.donor_classifier_ai.classify_industries_batch_scored",
            return_value={},
        ), patch(
            "app.pipeline.analyze.donor_classifier_ai.classify_donor_type_semantic",
            return_value=None,
        ), patch(
            "app.pipeline.analyze.donor_classifier_ai._classify_remaining_via_nn",
            return_value={"COMPLETELY UNKNOWN ENTITY XYZ": {"type": "Org/Employees", "industry": "OTHER"}},
        ) as mock_nn:
            result = await classify_donors_hybrid(donors, db_session=db_session)
            mock_nn.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_unknown_and_empty_names(self, db_session):
        donors = [
            {"name": "UNKNOWN", "amount": 100},
            {"name": "", "amount": 200},
            {"name": "  ", "amount": 300},
        ]
        result = await classify_donors_hybrid(donors, db_session=db_session)
        assert result == {}
