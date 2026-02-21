"""Tests for the FEC financial data normalization."""

import pytest

from app.pipeline.transform.normalize_finance import (
    _clean_donor_name,
    _is_candidate_affiliated,
    build_top_donors,
    normalize_finance,
)


class TestCandidateAffiliation:
    """Detect candidate-controlled PACs."""

    def test_team_pac(self):
        assert _is_candidate_affiliated("TEAM CRUZ FOR SENATE", "CRUZ, TED") is True

    def test_friends_of(self):
        assert _is_candidate_affiliated("FRIENDS OF SCHUMER", "SCHUMER, CHARLES") is True

    def test_for_senate(self):
        assert _is_candidate_affiliated("WARREN FOR SENATE", "WARREN, ELIZABETH") is True

    def test_unrelated_pac(self):
        assert _is_candidate_affiliated("GOLDMAN SACHS PAC", "CRUZ, TED") is False

    def test_empty_candidate(self):
        assert _is_candidate_affiliated("TEAM SOMEONE", "") is False

    def test_short_last_name_skipped(self):
        assert _is_candidate_affiliated("MR FO FOR SENATE", "FO, JOHN") is False


class TestCleanDonorName:
    """Title-case conversion preserving acronyms."""

    def test_all_caps_to_title(self):
        assert _clean_donor_name("GOLDMAN SACHS") == "Goldman Sachs"

    def test_acronyms_preserved(self):
        result = _clean_donor_name("SOME CORP LLC")
        assert "LLC" in result
        assert "CORP" in result

    def test_already_mixed_case_unchanged(self):
        assert _clean_donor_name("Goldman Sachs") == "Goldman Sachs"


class TestNormalizeFinance:
    """Full normalize_finance integration."""

    def test_basic_totals(self):
        financials = [
            {
                "receipts": 1_000_000,
                "other_political_committee_contributions": 200_000,
                "individual_unitemized_contributions": 100_000,
                "individual_itemized_contributions": 600_000,
            }
        ]
        result = normalize_finance(
            candidate={"name": "TEST, PERSON"},
            financials=financials,
            individual_receipts=[],
            pac_receipts=[],
            aggregated_contributors=[],
        )

        assert result["totalRaised"] == 1_000_000
        assert result["totalFromPACs"] == 200_000
        assert result["smallDonorPercentage"] == 10
        assert isinstance(result["topDonors"], list)
        assert isinstance(result["industryBreakdown"], list)

    def test_zero_raised_safe(self):
        result = normalize_finance(
            candidate=None,
            financials=[{"receipts": 0}],
            individual_receipts=[],
            pac_receipts=[],
            aggregated_contributors=[],
        )
        assert result["totalRaised"] == 0
        assert result["smallDonorPercentage"] == 0

    def test_multiple_cycles_summed(self):
        financials = [
            {"receipts": 500_000, "other_political_committee_contributions": 100_000,
             "individual_unitemized_contributions": 50_000, "individual_itemized_contributions": 300_000},
            {"receipts": 300_000, "other_political_committee_contributions": 50_000,
             "individual_unitemized_contributions": 30_000, "individual_itemized_contributions": 200_000},
        ]
        result = normalize_finance(
            candidate=None,
            financials=financials,
            individual_receipts=[],
            pac_receipts=[],
            aggregated_contributors=[],
        )
        assert result["totalRaised"] == 800_000
        assert result["totalFromPACs"] == 150_000

    def test_only_two_most_recent_cycles_used(self):
        financials = [
            {"receipts": 100, "other_political_committee_contributions": 0,
             "individual_unitemized_contributions": 0, "individual_itemized_contributions": 0},
            {"receipts": 200, "other_political_committee_contributions": 0,
             "individual_unitemized_contributions": 0, "individual_itemized_contributions": 0},
            {"receipts": 999_999, "other_political_committee_contributions": 0,
             "individual_unitemized_contributions": 0, "individual_itemized_contributions": 0},
        ]
        result = normalize_finance(
            candidate=None,
            financials=financials,
            individual_receipts=[],
            pac_receipts=[],
            aggregated_contributors=[],
        )
        assert result["totalRaised"] == 300  # only first two


@pytest.mark.slow
class TestBuildTopDonors:
    """PAC + individual donor building with AI classification integration."""

    def test_pac_receipts_aggregated(self):
        pac_receipts = [
            {"contributor_name": "BIG PAC", "contribution_receipt_amount": 5000, "memo_text": ""},
            {"contributor_name": "BIG PAC", "contribution_receipt_amount": 3000, "memo_text": ""},
        ]
        donors = build_top_donors(pac_receipts, [], [], "")
        assert len(donors) == 1
        assert donors[0]["total"] == 8000

    def test_skip_patterns_filtered(self):
        pac_receipts = [
            {"contributor_name": "WINRED TECHNICAL", "contribution_receipt_amount": 50000, "memo_text": ""},
            {"contributor_name": "ACTBLUE", "contribution_receipt_amount": 30000, "memo_text": ""},
            {"contributor_name": "REAL PAC", "contribution_receipt_amount": 5000, "memo_text": ""},
        ]
        donors = build_top_donors(pac_receipts, [], [], "")
        names = [d["name"] for d in donors]
        assert len(donors) == 1
        assert "Real PAC" in names[0] or "REAL PAC" in names[0].upper()

    def test_transfers_filtered(self):
        pac_receipts = [
            {"contributor_name": "SOME PAC", "contribution_receipt_amount": 5000, "memo_text": "TRANSFER FROM ACCOUNT"},
        ]
        donors = build_top_donors(pac_receipts, [], [], "")
        assert len(donors) == 0

    def test_ai_classification_used(self):
        pac_receipts = [
            {"contributor_name": "MYSTERY PAC", "contribution_receipt_amount": 5000, "memo_text": ""},
        ]
        ai = {"MYSTERY PAC": {"type": "PAC", "industry": "PHARMA", "skip": False}}
        donors = build_top_donors(pac_receipts, [], [], "", ai_classifications=ai)
        assert donors[0]["type"] == "PAC"
        assert donors[0]["industry"] == "PHARMA"

    def test_ai_skip_flag_filters(self):
        pac_receipts = [
            {"contributor_name": "PAYMENT PROCESSOR", "contribution_receipt_amount": 50000, "memo_text": ""},
        ]
        ai = {"PAYMENT PROCESSOR": {"type": "SKIP", "industry": "OTHER", "skip": True}}
        donors = build_top_donors(pac_receipts, [], [], "", ai_classifications=ai)
        assert len(donors) == 0

    def test_individual_contributions_grouped_by_employer(self):
        individual = [
            {"contributor_employer": "Goldman Sachs", "contribution_receipt_amount": 2800},
            {"contributor_employer": "Goldman Sachs", "contribution_receipt_amount": 2800},
            {"contributor_employer": "Google", "contribution_receipt_amount": 1000},
        ]
        donors = build_top_donors([], individual, [], "")
        gs = next((d for d in donors if "Goldman" in d["name"] or "GOLDMAN" in d["name"].upper()), None)
        assert gs is not None
        assert gs["total"] == 5600

    def test_skip_employers_filtered(self):
        individual = [
            {"contributor_employer": "RETIRED", "contribution_receipt_amount": 500},
            {"contributor_employer": "SELF-EMPLOYED", "contribution_receipt_amount": 1000},
            {"contributor_employer": "Real Company", "contribution_receipt_amount": 2000},
        ]
        donors = build_top_donors([], individual, [], "")
        assert len(donors) == 1

    def test_top_100_limit(self):
        pac_receipts = [
            {"contributor_name": f"PAC {i}", "contribution_receipt_amount": i, "memo_text": ""}
            for i in range(150)
        ]
        donors = build_top_donors(pac_receipts, [], [], "")
        assert len(donors) <= 100

    def test_sorted_by_total_descending(self):
        pac_receipts = [
            {"contributor_name": "SMALL PAC", "contribution_receipt_amount": 100, "memo_text": ""},
            {"contributor_name": "BIG PAC", "contribution_receipt_amount": 10000, "memo_text": ""},
            {"contributor_name": "MEDIUM PAC", "contribution_receipt_amount": 1000, "memo_text": ""},
        ]
        donors = build_top_donors(pac_receipts, [], [], "")
        assert donors[0]["total"] == 10000
        assert donors[-1]["total"] == 100
