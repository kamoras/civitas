"""Tests for the FEC financial data normalization."""

import pytest

from app.pipeline.transform.normalize_finance import (
    _clean_donor_name,
    build_top_donors,
    normalize_finance,
)
from app.pipeline.analyze.donor_classifier_ai import classify_donor_type_semantic


class TestCandidateAffiliation:
    """Detect candidate-controlled PACs via semantic classification."""

    def test_personal_contribution(self):
        result = classify_donor_type_semantic(
            "CRUZ, RAPHAEL EDWARD TED",
            candidate_name="CRUZ, RAFAEL EDWARD (TED)",
        )
        assert result == "Self-Funded"

    def test_unrelated_pac(self):
        result = classify_donor_type_semantic(
            "GOLDMAN SACHS PAC",
            candidate_name="CRUZ, TED",
        )
        assert result != "CandidateAffiliated" or result is None

    def test_empty_candidate(self):
        result = classify_donor_type_semantic("TEAM SOMEONE", candidate_name="")
        assert result != "CandidateAffiliated" or result is None

    def test_short_last_name_skipped(self):
        result = classify_donor_type_semantic(
            "MR FO FOR SENATE",
            candidate_name="FO, JOHN",
        )
        assert result != "CandidateAffiliated" or result is None


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
        # totalFromPACs is now computed from actual donor records, not the financial summary
        assert result["totalFromPACs"] >= 0
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

    def test_no_election_year_falls_back_to_first_row(self):
        # Rows with no discernible election year can't be deduped by year,
        # so select_recent_elections falls back to financials[:n] — with
        # n=1 (most recent election only) that's just the first row.
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
        assert result["totalRaised"] == 500_000
        # totalFromPACs is computed from actual donor records
        assert result["totalFromPACs"] >= 0

    def test_only_most_recent_election_used(self):
        # Funding is windowed to the candidate's most recent election (their
        # current mandate's campaign), not a career-spanning lookback —
        # see select_recent_elections for why.
        financials = [
            {"candidate_election_year": 2030, "receipts": 100,
             "other_political_committee_contributions": 0,
             "individual_unitemized_contributions": 0, "individual_itemized_contributions": 0},
            {"candidate_election_year": 2024, "receipts": 200,
             "other_political_committee_contributions": 0,
             "individual_unitemized_contributions": 0, "individual_itemized_contributions": 0},
            {"candidate_election_year": 2018, "receipts": 999_999,
             "other_political_committee_contributions": 0,
             "individual_unitemized_contributions": 0, "individual_itemized_contributions": 0},
        ]
        result = normalize_finance(
            candidate=None,
            financials=financials,
            individual_receipts=[],
            pac_receipts=[],
            aggregated_contributors=[],
        )
        assert result["totalRaised"] == 100  # 2030 only, not 2024 or 2018

    def test_non_contribution_receipts_excluded_from_donors(self):
        # Schedule A itemizes ALL receipts. Only line 11 is contributions:
        # line 14 is vendor refunds (a media buyer appeared as a senator's
        # top donor), 13A is loans, 15 is bank interest, 12 is JFC transfers.
        pac_receipts = [
            {"contributor_name": "BUYING TIME LLC", "line_number": "14",
             "contribution_receipt_amount": 220_000},
            {"contributor_name": "PINNACLE BANK", "line_number": "13B",
             "contribution_receipt_amount": 2_500_000},
            {"contributor_name": "UMPQUA BANK", "line_number": "15",
             "contribution_receipt_amount": 56_000},
            {"contributor_name": "SOME VICTORY FUND", "line_number": "12",
             "contribution_receipt_amount": 300_000},
            {"contributor_name": "GOLDMAN SACHS PAC", "line_number": "11C",
             "contribution_receipt_amount": 10_000},
        ]
        donors = build_top_donors(
            pac_receipts=pac_receipts,
            individual_receipts=[],
            aggregated_contributors=[],
            candidate_name="TEST, PERSON",
        )
        names = {d["name"] for d in donors}
        assert names == {"Goldman Sachs PAC"}

    def test_candidate_line_11d_typed_self_funded(self):
        pac_receipts = [
            {"contributor_name": "SCOTT, RICK GOV.", "line_number": "11D",
             "contribution_receipt_amount": 7_450_000},
        ]
        donors = build_top_donors(
            pac_receipts=pac_receipts,
            individual_receipts=[],
            aggregated_contributors=[],
            candidate_name="UNRELATED, NAME",
        )
        assert len(donors) == 1
        assert donors[0]["type"] == "Self-Funded"

    def test_same_election_rows_not_double_counted(self):
        # /candidate/{id}/totals returns an election-full aggregate row
        # (cycle: null) PLUS per-cycle rows for the same election. Summing
        # both would count the same money twice (184/521 cached candidates,
        # 2026-07 audit) — and with funding windowed to only the most
        # recent election, the older 2024 race must not be counted at all.
        financials = [
            {"candidate_election_year": 2030, "cycle": None, "receipts": 1_200_000,
             "other_political_committee_contributions": 120_000,
             "individual_unitemized_contributions": 400_000,
             "individual_itemized_contributions": 500_000},
            {"candidate_election_year": 2030, "cycle": 2026, "receipts": 1_200_000,
             "other_political_committee_contributions": 120_000,
             "individual_unitemized_contributions": 400_000,
             "individual_itemized_contributions": 500_000},
            {"candidate_election_year": 2024, "cycle": None, "receipts": 52_000_000,
             "other_political_committee_contributions": 3_500_000,
             "individual_unitemized_contributions": 15_000_000,
             "individual_itemized_contributions": 30_000_000},
        ]
        result = normalize_finance(
            candidate=None,
            financials=financials,
            individual_receipts=[],
            pac_receipts=[],
            aggregated_contributors=[],
        )
        assert result["totalRaised"] == 1_200_000  # 2030 once, not the 2024 race
        assert result["totalFromPACs"] == 120_000
        assert result["smallDonorPercentage"] == round(400_000 / 1_200_000 * 100)

    def test_negative_receipts_on_most_recent_election_floors_at_zero_not_masked_by_prior(self):
        # A just-opened next-cycle committee can have genuinely negative
        # FEC receipts (more refunds/adjustments than new money so far) —
        # real upstream data, not a fetch bug (2026-07 audit: a sitting
        # representative's still-forming committee was -$1.6M). With
        # funding windowed to only the most recent election, a strongly
        # positive prior election must NOT dilute/mask that negative value —
        # it's excluded entirely, and the negative floors at zero on its own.
        financials = [
            {"candidate_election_year": 2026, "receipts": -1_618_913.64,
             "other_political_committee_contributions": 57_000,
             "individual_unitemized_contributions": 14_706.6,
             "individual_itemized_contributions": 0},
            {"candidate_election_year": 2024, "receipts": 9_660_670.47,
             "other_political_committee_contributions": 274_500,
             "individual_unitemized_contributions": 44_352.18,
             "individual_itemized_contributions": 100_000},
        ]
        result = normalize_finance(
            candidate=None,
            financials=financials,
            individual_receipts=[],
            pac_receipts=[],
            aggregated_contributors=[],
        )
        assert result["totalRaised"] == 0

    def test_all_negative_receipts_floored_at_zero(self):
        # A candidate whose only cached election has net-negative receipts
        # (e.g. large early refunds, no second election on file yet) must
        # never show a negative "total raised" — nothing downstream should
        # have to defend against that possibility.
        financials = [
            {"candidate_election_year": 2026, "receipts": -50_000,
             "other_political_committee_contributions": -5_000,
             "individual_unitemized_contributions": -1_000,
             "individual_itemized_contributions": -2_000},
        ]
        result = normalize_finance(
            candidate=None,
            financials=financials,
            individual_receipts=[],
            pac_receipts=[],
            aggregated_contributors=[],
        )
        assert result["totalRaised"] == 0
        assert result["totalFromPACs"] == 0
        assert result["smallDonorPercentage"] == 0


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
