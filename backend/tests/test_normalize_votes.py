"""Tests for vote normalization and party alignment logic."""

import pytest

from app.pipeline.transform.normalize_votes import (
    _determine_party_alignment,
    _infer_caucus_from_cosponsorship,
    _infer_caucus_from_votes,
    _infer_caucus_party,
    compute_party_split,
    extract_senator_vote,
    normalize_votes,
)


class TestPartyAlignment:
    """Party alignment determination logic."""

    @pytest.mark.parametrize(
        "party, vote, bill_leaning, expected",
        [
            pytest.param("R", "Yea", "R", True, id="republican_yea_on_republican_bill"),
            pytest.param("R", "Nay", "R", False, id="republican_nay_on_republican_bill"),
            pytest.param("R", "Yea", "D", False, id="republican_yea_on_democratic_bill"),
            pytest.param("R", "Nay", "D", True, id="republican_nay_on_democratic_bill"),
            pytest.param("D", "Yea", "D", True, id="democrat_yea_on_democratic_bill"),
            pytest.param("D", "Nay", "D", False, id="democrat_nay_on_democratic_bill"),
            pytest.param("I", "Yea", "R", None, id="independent_always_none_yea"),
            pytest.param("I", "Nay", "D", None, id="independent_always_none_nay"),
            pytest.param("R", "Not Voting", "R", None, id="not_voting_always_none"),
            pytest.param("R", "Yea", "bipartisan", None, id="bipartisan_always_none"),
            pytest.param("D", "Yea", None, None, id="no_party_leaning_is_none"),
        ],
    )
    def test_party_alignment(self, party, vote, bill_leaning, expected):
        assert _determine_party_alignment(party, vote, bill_leaning) is expected


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

    def test_multi_word_last_name(self):
        """Multi-word last names like 'Cortez Masto' and 'Van Hollen' must match."""
        data = {
            "members": [
                {"lastName": "Cortez Masto", "state": "NV", "voteCast": "Yea"},
                {"lastName": "Van Hollen", "state": "MD", "voteCast": "Nay"},
                {"lastName": "Blunt Rochester", "state": "DE", "voteCast": "Yea"},
            ]
        }
        assert extract_senator_vote(data, "", "Cortez Masto", "NV") == "Yea"
        assert extract_senator_vote(data, "", "Van Hollen", "MD") == "Nay"
        assert extract_senator_vote(data, "", "Blunt Rochester", "DE") == "Yea"

    def test_unicode_accent_normalization(self):
        """Accented characters (e.g. Luján) must match unaccented form (Lujan)."""
        data = {
            "members": [
                {"lastName": "Lujan", "state": "NM", "voteCast": "Yea"},
            ]
        }
        assert extract_senator_vote(data, "", "Luján", "NM") == "Yea"
        assert extract_senator_vote(data, "", "Lujan", "NM") == "Yea"


class TestNormalizeVotes:
    """Full vote normalization pipeline."""

    def test_basic_vote_counting(self):
        bills = [
            {"billId": "hr1", "billName": "Bill 1", "policyArea": "HEALTHCARE",
             "stance": "reform", "partyLeaning": "D", "description": ""},
            {"billId": "hr2", "billName": "Bill 2", "policyArea": "DEFENSE",
             "stance": "increase", "partyLeaning": "R", "description": ""},
        ]
        votes = {"hr1": "Yea", "hr2": "Nay"}

        result = normalize_votes("B001", bills, votes, "D")

        assert result["totalVotes"] == 2
        assert len(result["keyVotes"]) == 2
        assert result["keyVotes"][0]["vote"] == "Yea"
        assert result["keyVotes"][1]["vote"] == "Nay"

    def test_party_loyalty_calculation(self):
        bills = [
            {"billId": f"hr{i}", "billName": f"Bill {i}", "policyArea": "DEFENSE",
             "stance": "x", "partyLeaning": "R", "description": ""}
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
             "stance": "x", "partyLeaning": "R", "description": ""},
        ]
        result = normalize_votes("B001", bills, {}, "R")
        assert result["totalVotes"] == 0
        assert len(result["keyVotes"]) == 0
        assert result["partyLoyaltyPct"] == 0.0  # no votes = 0% measurable loyalty

    def test_independent_caucus_inference(self):
        """Independents who vote mostly with D should get effective party D."""
        bills = [
            {"billId": f"d{i}", "billName": f"D Bill {i}", "policyArea": "HEALTHCARE",
             "stance": "reform", "partyLeaning": "D", "description": ""}
            for i in range(8)
        ] + [
            {"billId": f"r{i}", "billName": f"R Bill {i}", "policyArea": "DEFENSE",
             "stance": "increase", "partyLeaning": "R", "description": ""}
            for i in range(3)
        ]
        # Senator votes Yea on D bills, Nay on R bills
        votes = {f"d{i}": "Yea" for i in range(8)}
        votes.update({f"r{i}": "Nay" for i in range(3)})

        result = normalize_votes("B001", bills, votes, "I")
        assert result["effectiveParty"] == "D"
        assert result["votedWithPartyCount"] > 0

    def test_independent_caucus_with_cosponsorship(self):
        """Cosponsorship data strengthens caucus inference for Independents."""
        bills = [
            {"billId": f"d{i}", "billName": f"D Bill {i}", "policyArea": "HEALTHCARE",
             "stance": "reform", "partyLeaning": "D", "description": ""}
            for i in range(6)
        ] + [
            {"billId": f"r{i}", "billName": f"R Bill {i}", "policyArea": "DEFENSE",
             "stance": "increase", "partyLeaning": "R", "description": ""}
            for i in range(3)
        ]
        votes = {f"d{i}": "Yea" for i in range(6)}
        votes.update({f"r{i}": "Nay" for i in range(3)})
        cosponsor = {"d_cosponsored": 15, "r_cosponsored": 2}

        result = normalize_votes("B001", bills, votes, "I", cosponsorship_profile=cosponsor)
        assert result["effectiveParty"] == "D"


class TestInferCaucusFromVotes:
    """Vote-based caucus inference (sub-function)."""

    def test_mostly_democratic_votes(self):
        bills = [
            {"billId": f"d{i}", "partyLeaning": "D"} for i in range(7)
        ] + [
            {"billId": f"r{i}", "partyLeaning": "R"} for i in range(3)
        ]
        votes = {f"d{i}": "Yea" for i in range(7)}
        votes.update({f"r{i}": "Nay" for i in range(3)})
        party, d, r = _infer_caucus_from_votes(bills, votes)
        assert party == "D"
        assert d > r

    def test_mostly_republican_votes(self):
        bills = [
            {"billId": f"r{i}", "partyLeaning": "R"} for i in range(7)
        ] + [
            {"billId": f"d{i}", "partyLeaning": "D"} for i in range(3)
        ]
        votes = {f"r{i}": "Yea" for i in range(7)}
        votes.update({f"d{i}": "Nay" for i in range(3)})
        party, d, r = _infer_caucus_from_votes(bills, votes)
        assert party == "R"
        assert r > d

    def test_insufficient_data(self):
        bills = [
            {"billId": "d0", "partyLeaning": "D"},
            {"billId": "r0", "partyLeaning": "R"},
        ]
        votes = {"d0": "Yea", "r0": "Nay"}
        party, _, _ = _infer_caucus_from_votes(bills, votes)
        assert party is None

    def test_bipartisan_ignored(self):
        bills = [
            {"billId": f"b{i}", "partyLeaning": "bipartisan"} for i in range(20)
        ]
        votes = {f"b{i}": "Yea" for i in range(20)}
        party, _, _ = _infer_caucus_from_votes(bills, votes)
        assert party is None


class TestInferCaucusFromCosponsorship:
    """Cosponsorship-based caucus inference."""

    @pytest.mark.parametrize(
        "d_cosponsored, r_cosponsored, expected_party",
        [
            pytest.param(20, 3, "D", id="strongly_democratic_cosponsorship"),
            pytest.param(2, 15, "R", id="strongly_republican_cosponsorship"),
            pytest.param(1, 1, None, id="insufficient_data"),
            pytest.param(0, 0, None, id="empty_profile"),
            pytest.param(10, 10, None, id="equal_cosponsorship"),
        ],
    )
    def test_infer_caucus(self, d_cosponsored, r_cosponsored, expected_party):
        profile = {"d_cosponsored": d_cosponsored, "r_cosponsored": r_cosponsored}
        party, d, r = _infer_caucus_from_cosponsorship(profile)
        assert party == expected_party
        assert d == d_cosponsored
        assert r == r_cosponsored


class TestInferCaucusPartyCombined:
    """Combined caucus inference using votes + cosponsorship."""

    def _make_party_bills(self, d_count, r_count):
        bills = [
            {"billId": f"d{i}", "partyLeaning": "D"} for i in range(d_count)
        ] + [
            {"billId": f"r{i}", "partyLeaning": "R"} for i in range(r_count)
        ]
        return bills

    def test_votes_only_backward_compat(self):
        """Without cosponsorship data, behaves like the old function."""
        bills = self._make_party_bills(7, 3)
        votes = {f"d{i}": "Yea" for i in range(7)}
        votes.update({f"r{i}": "Nay" for i in range(3)})
        assert _infer_caucus_party(bills, votes) == "D"

    def test_cosponsorship_reinforces_votes(self):
        """When both signals agree, result is the agreed party."""
        bills = self._make_party_bills(7, 3)
        votes = {f"d{i}": "Yea" for i in range(7)}
        votes.update({f"r{i}": "Nay" for i in range(3)})
        cosponsor = {"d_cosponsored": 15, "r_cosponsored": 2}
        assert _infer_caucus_party(bills, votes, cosponsor) == "D"

    def test_cosponsorship_alone_sufficient(self):
        """With strong cosponsorship but weak voting data, cosponsorship wins."""
        bills = self._make_party_bills(2, 2)
        votes = {f"d{i}": "Yea" for i in range(2)}
        votes.update({f"r{i}": "Nay" for i in range(2)})
        cosponsor = {"d_cosponsored": 20, "r_cosponsored": 1}
        assert _infer_caucus_party(bills, votes, cosponsor) == "D"

    def test_disagreement_strong_margin_picks_winner(self):
        """When signals disagree but one is much stronger, it wins."""
        bills = self._make_party_bills(3, 7)
        votes = {f"d{i}": "Nay" for i in range(3)}
        votes.update({f"r{i}": "Yea" for i in range(7)})
        # Votes say R, but cosponsorship strongly says D
        cosponsor = {"d_cosponsored": 25, "r_cosponsored": 2}
        # Combined: D = 3 + 25*1.5 = 40.5, R = 7 + 2*1.5 = 10
        assert _infer_caucus_party(bills, votes, cosponsor) == "D"

    def test_disagreement_weak_margin_returns_none(self):
        """When signals disagree with similar strength, returns None."""
        # Yea on D bills → d_support, Yea on R bills → r_support
        # So votes: d_support=4, r_support=6 → vote_party=R
        bills = self._make_party_bills(4, 6)
        votes = {f"d{i}": "Yea" for i in range(4)}
        votes.update({f"r{i}": "Yea" for i in range(6)})
        # Cosponsorship leans D → cosponsor_party=D (disagrees with votes)
        cosponsor = {"d_cosponsored": 6, "r_cosponsored": 4}
        # Combined: D = 4 + 6*1.5 = 13, R = 6 + 4*1.5 = 12
        # Margin = 1/25 = 0.04 < 0.2 threshold → None
        result = _infer_caucus_party(bills, votes, cosponsor)
        assert result is None

    def test_no_cosponsorship_data(self):
        """None cosponsorship profile falls back to votes only."""
        bills = self._make_party_bills(7, 3)
        votes = {f"d{i}": "Yea" for i in range(7)}
        votes.update({f"r{i}": "Nay" for i in range(3)})
        assert _infer_caucus_party(bills, votes, None) == "D"

    def test_sanders_like_pattern(self):
        """Simulates an Independent who caucuses with Democrats."""
        bills = self._make_party_bills(8, 5)
        votes = {f"d{i}": "Yea" for i in range(8)}
        votes.update({f"r{i}": "Nay" for i in range(5)})
        cosponsor = {"d_cosponsored": 18, "r_cosponsored": 1}
        assert _infer_caucus_party(bills, votes, cosponsor) == "D"
