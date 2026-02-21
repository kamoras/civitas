"""Tests for the hybrid donor classifier.

Tests the tiered classification strategy:
1. FEC committee type codes (structured metadata)
2. Deterministic pattern rules (SKIP, Party/Ideological)
3. Learning store lookup
4. LLM fallback (mocked)
"""

import pytest
from unittest.mock import AsyncMock, patch

from app.models import LearnedClassification
from app.pipeline.analyze.donor_classifier_ai import (
    FEC_TYPE_MAP,
    classify_donor_type_from_fec,
    classify_donor_type_from_rules,
    classify_donors_hybrid,
)


class TestFECTypeClassification:
    """Tier 1: FEC committee type codes."""

    @pytest.mark.parametrize(
        "committee_type, expected",
        [
            ("N", "PAC"),
            ("Q", "PAC"),
            ("O", "PAC"),
            ("W", "PAC"),
            ("X", "Party/Ideological"),
            ("Y", "Party/Ideological"),
            ("Z", "Party/Ideological"),
            ("H", "CandidateAffiliated"),
            ("S", "CandidateAffiliated"),
            ("P", "CandidateAffiliated"),
            ("I", "Org/Employees"),
        ],
    )
    def test_known_committee_types(self, committee_type, expected):
        receipt = {"committee": {"committee_type": committee_type}}
        assert classify_donor_type_from_fec(receipt) == expected

    def test_unknown_committee_type_returns_none(self):
        receipt = {"committee": {"committee_type": "ZZ"}}
        assert classify_donor_type_from_fec(receipt) is None

    def test_missing_committee_returns_none(self):
        assert classify_donor_type_from_fec({}) is None
        assert classify_donor_type_from_fec({"committee": {}}) is None

    def test_fec_map_covers_all_expected_codes(self):
        assert len(FEC_TYPE_MAP) == 16


class TestRuleClassification:
    """Tier 2: Deterministic pattern rules."""

    @pytest.mark.parametrize(
        "name, expected",
        [
            ("WINRED TECHNICAL SERVICES", "SKIP"),
            ("ACTBLUE", "SKIP"),
            ("ANEDOT INC", "SKIP"),
            ("SOME VICTORY COMMITTEE", "SKIP"),
            ("JOINT FUNDRAISING COMMITTEE", "SKIP"),
        ],
    )
    def test_skip_patterns(self, name, expected):
        assert classify_donor_type_from_rules(name) == expected

    @pytest.mark.parametrize(
        "name, expected",
        [
            ("DEMOCRATIC NATIONAL COMMITTEE", "Party/Ideological"),
            ("REPUBLICAN NATIONAL COMMITTEE", "Party/Ideological"),
            ("DSCC", "Party/Ideological"),
            ("NRSC", "Party/Ideological"),
            ("EMILY'S LIST", "Party/Ideological"),
            ("CLUB FOR GROWTH", "Party/Ideological"),
            ("SENATE MAJORITY PAC", "Party/Ideological"),
        ],
    )
    def test_party_patterns(self, name, expected):
        assert classify_donor_type_from_rules(name) == expected

    def test_unknown_name_returns_none(self):
        assert classify_donor_type_from_rules("PFIZER INC") is None
        assert classify_donor_type_from_rules("GOLDMAN SACHS") is None


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
                "fec_receipt": {"committee": {"committee_type": "Q"}},
            }
        ]
        result = await classify_donors_hybrid(donors, db_session=db_session)
        assert "TEST PAC" in result
        assert result["TEST PAC"]["type"] == "PAC"

    @pytest.mark.asyncio
    async def test_rules_tier(self, db_session):
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
            {"name": "Test Corp", "amount": 1000, "fec_receipt": {"committee": {"committee_type": "Q"}}},
            {"name": "TEST CORP", "amount": 2000, "fec_receipt": {"committee": {"committee_type": "Q"}}},
            {"name": "test corp", "amount": 500, "fec_receipt": {"committee": {"committee_type": "Q"}}},
        ]
        result = await classify_donors_hybrid(donors, db_session=db_session)
        assert len(result) == 1
        assert "TEST CORP" in result

    @pytest.mark.asyncio
    async def test_unknown_donors_skipped_without_llm(self, db_session):
        """Donors with unknown type AND industry should be queued for LLM."""
        donors = [{"name": "Completely Unknown Entity XYZ", "amount": 500}]
        with patch(
            "app.pipeline.analyze.donor_classifier_ai._classify_remaining_via_llm",
            new_callable=AsyncMock,
            return_value={"COMPLETELY UNKNOWN ENTITY XYZ": {"type": "Org/Employees", "industry": "OTHER"}},
        ) as mock_llm:
            result = await classify_donors_hybrid(donors, db_session=db_session)
            mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_unknown_and_empty_names(self, db_session):
        donors = [
            {"name": "UNKNOWN", "amount": 100},
            {"name": "", "amount": 200},
            {"name": "  ", "amount": 300},
        ]
        result = await classify_donors_hybrid(donors, db_session=db_session)
        assert result == {}
