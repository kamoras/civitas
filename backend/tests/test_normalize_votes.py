"""Tests for vote normalization and party alignment logic."""

import pytest

from app.pipeline.transform.normalize_votes import (
    _determine_party_alignment,
    compute_party_split,
    extract_senator_vote,
    normalize_votes,
)


class TestPartyAlignment:
    """Party alignment determination logic."""

    def test_republican_yea_on_republican_bill(self):
        assert _determine_party_alignment("R", "Yea", "R") is True

    def test_republican_nay_on_republican_bill(self):
        assert _determine_party_alignment("R", "Nay", "R") is False

    def test_republican_yea_on_democratic_bill(self):
        assert _determine_party_alignment("R", "Yea", "D") is False

    def test_republican_nay_on_democratic_bill(self):
        assert _determine_party_alignment("R", "Nay", "D") is True

    def test_democrat_yea_on_democratic_bill(self):
        assert _determine_party_alignment("D", "Yea", "D") is True

    def test_democrat_nay_on_democratic_bill(self):
        assert _determine_party_alignment("D", "Nay", "D") is False

    def test_independent_always_none(self):
        assert _determine_party_alignment("I", "Yea", "R") is None
        assert _determine_party_alignment("I", "Nay", "D") is None

    def test_not_voting_always_none(self):
        assert _determine_party_alignment("R", "Not Voting", "R") is None

    def test_bipartisan_always_none(self):
        assert _determine_party_alignment("R", "Yea", "bipartisan") is None

    def test_no_party_leaning_is_none(self):
        assert _determine_party_alignment("D", "Yea", None) is None


class TestComputePartySplit:
    """Roll call party split computation."""

    def _make_members(self, r_yea, r_nay, d_yea, d_nay):
        members = []
        for _ in range(r_yea):
            members.append({"party": "R", "voteCast": "Yea"})
        for _ in range(r_nay):
            members.append({"party": "R", "voteCast": "Nay"})
        for _ in range(d_yea):
            members.append({"party": "D", "voteCast": "Yea"})
        for _ in range(d_nay):
            members.append({"party": "D", "voteCast": "Nay"})
        return {"members": members}

    def test_republican_bill(self):
        data = self._make_members(r_yea=40, r_nay=5, d_yea=3, d_nay=42)
        assert compute_party_split(data) == "R"

    def test_democratic_bill(self):
        data = self._make_members(r_yea=2, r_nay=43, d_yea=40, d_nay=5)
        assert compute_party_split(data) == "D"

    def test_bipartisan_bill(self):
        data = self._make_members(r_yea=30, r_nay=15, d_yea=35, d_nay=10)
        assert compute_party_split(data) == "bipartisan"

    def test_insufficient_data_returns_none(self):
        data = self._make_members(r_yea=2, r_nay=0, d_yea=1, d_nay=0)
        assert compute_party_split(data) is None


class TestExtractSenatorVote:
    """Senator vote extraction from roll call data."""

    def test_match_by_name_and_state(self):
        data = {
            "members": [
                {"lastName": "CRUZ", "state": "TX", "voteCast": "Yea"},
                {"lastName": "WARREN", "state": "MA", "voteCast": "Nay"},
            ]
        }
        assert extract_senator_vote(data, "", "Cruz", "TX") == "Yea"
        assert extract_senator_vote(data, "", "Warren", "MA") == "Nay"

    def test_case_insensitive(self):
        data = {"members": [{"lastName": "cruz", "state": "tx", "voteCast": "Nay"}]}
        assert extract_senator_vote(data, "", "CRUZ", "TX") == "Nay"

    def test_not_found_returns_none(self):
        data = {"members": [{"lastName": "SMITH", "state": "OH", "voteCast": "Yea"}]}
        assert extract_senator_vote(data, "", "Jones", "CA") is None

    def test_empty_data_returns_none(self):
        assert extract_senator_vote(None, "") is None
        assert extract_senator_vote({}, "") is None
        assert extract_senator_vote({"members": []}, "") is None


class TestNormalizeVotes:
    """Full vote normalization pipeline."""

    def test_basic_vote_counting(self):
        bills = [
            {"billId": "hr1", "billName": "Bill 1", "policyArea": "HEALTHCARE",
             "stance": "reform", "stanceVote": "Yea", "partyLeaning": "D",
             "affectedIndustries": ["PHARMA"], "corporateInterest": "pharma profits",
             "impactedGroups": [], "description": "", "publicImpact": ""},
            {"billId": "hr2", "billName": "Bill 2", "policyArea": "DEFENSE",
             "stance": "increase", "stanceVote": "Nay", "partyLeaning": "R",
             "affectedIndustries": ["DEFENSE"], "corporateInterest": "defense contracts",
             "impactedGroups": [], "description": "", "publicImpact": ""},
        ]
        votes = {"hr1": "Yea", "hr2": "Nay"}

        result = normalize_votes("B001", bills, votes, "D")

        assert result["totalVotes"] == 2
        assert len(result["keyVotes"]) == 2
        assert result["keyVotes"][0]["vote"] == "Yea"
        assert result["keyVotes"][1]["vote"] == "Nay"

    def test_donor_aligned_counting(self):
        """Senator votes WITH stanceVote on a bill with industry signal = donor-aligned."""
        bills = [
            {"billId": "hr1", "billName": "Tax Cuts", "policyArea": "TAXES",
             "stance": "cut corporate taxes", "stanceVote": "Yea", "partyLeaning": "R",
             "affectedIndustries": ["FINANCE"], "corporateInterest": "lower corporate tax rate",
             "impactedGroups": [], "description": "", "publicImpact": ""},
        ]
        votes = {"hr1": "Yea"}

        result = normalize_votes("B001", bills, votes, "R")
        assert result["donorAlignedVotes"] == 1
        assert result["donorOpposedVotes"] == 0
        assert result["scoreableVotes"] == 1

    def test_donor_opposed_counting(self):
        """Senator votes AGAINST stanceVote on a bill with industry signal = donor-opposed."""
        bills = [
            {"billId": "hr1", "billName": "Pharma Bill", "policyArea": "HEALTHCARE",
             "stance": "deregulate pharma", "stanceVote": "Yea", "partyLeaning": "D",
             "affectedIndustries": ["PHARMA"], "corporateInterest": "pharma profits",
             "impactedGroups": [], "description": "", "publicImpact": ""},
        ]
        votes = {"hr1": "Nay"}

        result = normalize_votes("B001", bills, votes, "D")
        assert result["donorAlignedVotes"] == 0
        assert result["donorOpposedVotes"] == 1
        assert result["scoreableVotes"] == 1

    def test_no_industry_signal_not_counted(self):
        """Bills without affectedIndustries or corporateInterest are not scoreable."""
        bills = [
            {"billId": "hr1", "billName": "Bill 1", "policyArea": "DEFENSE",
             "stance": "increase", "stanceVote": "Yea", "partyLeaning": "R",
             "affectedIndustries": [], "corporateInterest": "",
             "impactedGroups": [], "description": "", "publicImpact": ""},
        ]
        votes = {"hr1": "Yea"}
        result = normalize_votes("B001", bills, votes, "R")
        assert result["donorAlignedVotes"] == 0
        assert result["donorOpposedVotes"] == 0
        assert result["scoreableVotes"] == 0

    def test_procedural_excluded_from_scoring(self):
        """PROCEDURAL bills are not counted even with stanceVote."""
        bills = [
            {"billId": "hr1", "billName": "Nomination", "policyArea": "PROCEDURAL",
             "stance": "procedural", "stanceVote": "Yea", "partyLeaning": "bipartisan",
             "affectedIndustries": ["DEFENSE"], "corporateInterest": "some interest",
             "impactedGroups": [], "description": "", "publicImpact": ""},
        ]
        votes = {"hr1": "Yea"}
        result = normalize_votes("B001", bills, votes, "R")
        assert result["donorAlignedVotes"] == 0
        assert result["donorOpposedVotes"] == 0

    def test_policy_breakdown_generated(self):
        """Policy area breakdown should be generated for non-procedural votes."""
        bills = [
            {"billId": "hr1", "billName": "Health Bill", "policyArea": "HEALTHCARE",
             "stance": "reform", "stanceVote": "Yea", "partyLeaning": "D",
             "affectedIndustries": ["PHARMA"], "corporateInterest": "pharma",
             "impactedGroups": [], "description": "", "publicImpact": ""},
            {"billId": "hr2", "billName": "Tax Bill", "policyArea": "TAXES",
             "stance": "cut", "stanceVote": "Yea", "partyLeaning": "R",
             "affectedIndustries": ["FINANCE"], "corporateInterest": "banking",
             "impactedGroups": [], "description": "", "publicImpact": ""},
            {"billId": "hr3", "billName": "Health Bill 2", "policyArea": "HEALTHCARE",
             "stance": "expand", "stanceVote": "Yea", "partyLeaning": "D",
             "affectedIndustries": [], "corporateInterest": "",
             "impactedGroups": ["patients"], "description": "", "publicImpact": ""},
        ]
        # hr1: Yea matches stanceVote Yea → withStance
        # hr3: Nay does NOT match stanceVote Yea → againstStance
        votes = {"hr1": "Yea", "hr2": "Nay", "hr3": "Nay"}

        result = normalize_votes("B001", bills, votes, "D")
        breakdown = result["policyBreakdown"]

        assert len(breakdown) == 2
        healthcare = next(b for b in breakdown if b["policyArea"] == "HEALTHCARE")
        assert healthcare["totalVotes"] == 2
        assert healthcare["withStance"] == 1
        assert healthcare["againstStance"] == 1

    def test_party_loyalty_calculation(self):
        bills = [
            {"billId": f"hr{i}", "billName": f"Bill {i}", "policyArea": "DEFENSE",
             "stance": "x", "stanceVote": "Yea", "partyLeaning": "R",
             "affectedIndustries": [], "corporateInterest": "",
             "impactedGroups": [], "description": "", "publicImpact": ""}
            for i in range(10)
        ]
        votes = {f"hr{i}": "Yea" if i < 8 else "Nay" for i in range(10)}

        result = normalize_votes("B001", bills, votes, "R")
        assert result["votedWithPartyCount"] == 8
        assert result["votedAgainstPartyCount"] == 2
        assert result["partyLoyaltyPct"] == 80.0

    def test_no_votes_on_bills(self):
        bills = [
            {"billId": "hr1", "billName": "Bill 1", "policyArea": "DEFENSE",
             "stance": "x", "stanceVote": "Yea", "partyLeaning": "R",
             "affectedIndustries": [], "corporateInterest": "",
             "impactedGroups": [], "description": "", "publicImpact": ""},
        ]
        result = normalize_votes("B001", bills, {}, "R")
        assert result["totalVotes"] == 0
        assert len(result["keyVotes"]) == 0
        assert result["partyLoyaltyPct"] == 0.0  # no votes = 0% measurable loyalty

    def test_no_stance_vote_not_scored(self):
        """Bills without stanceVote (None) are not donor-scored."""
        bills = [
            {"billId": "hr1", "billName": "Bill 1", "policyArea": "DEFENSE",
             "stance": "x", "stanceVote": None, "partyLeaning": "R",
             "affectedIndustries": ["DEFENSE"], "corporateInterest": "defense spending",
             "impactedGroups": [], "description": "", "publicImpact": ""},
        ]
        votes = {"hr1": "Yea"}
        result = normalize_votes("B001", bills, votes, "R")
        assert result["donorAlignedVotes"] == 0
        assert result["donorOpposedVotes"] == 0

    def test_affected_industries_passed_through(self):
        """Affected industries from bill classifications are passed to key votes."""
        bills = [
            {"billId": "hr1", "billName": "Bill 1", "policyArea": "HEALTHCARE",
             "stance": "reform", "stanceVote": "Yea", "partyLeaning": "D",
             "affectedIndustries": ["PHARMA", "INSURANCE"],
             "corporateInterest": "pharma profits",
             "impactedGroups": ["patients"], "description": "", "publicImpact": ""},
        ]
        votes = {"hr1": "Yea"}
        result = normalize_votes("B001", bills, votes, "D")
        assert result["keyVotes"][0]["affectedIndustries"] == ["PHARMA", "INSURANCE"]
