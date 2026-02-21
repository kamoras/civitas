"""Tests for the five representation sub-score calculations."""

import pytest

from app.pipeline.analyze.score_calculator import (
    _calc_accountability,
    _calc_constituent_funding,
    _calc_donor_diversity,
    _calc_independence_index,
    _calc_promise_fulfillment,
    calculate_scores,
    clamp,
)


class TestClamp:
    def test_within_range(self):
        assert clamp(50.3) == 50

    def test_below_min(self):
        assert clamp(-10.0) == 0

    def test_above_max(self):
        assert clamp(150.0) == 100

    def test_exact_boundaries(self):
        assert clamp(0.0) == 0
        assert clamp(100.0) == 100


class TestConstituentFunding:
    """Higher score = more small donors, less PAC money."""

    def test_ideal_grassroots(self):
        funding = {"totalRaised": 1_000_000, "totalFromPACs": 0, "smallDonorPercentage": 100}
        assert _calc_constituent_funding(funding) == 100

    def test_fully_pac_funded(self):
        funding = {"totalRaised": 1_000_000, "totalFromPACs": 1_000_000, "smallDonorPercentage": 0}
        assert _calc_constituent_funding(funding) == 0

    def test_balanced_funding(self):
        funding = {"totalRaised": 1_000_000, "totalFromPACs": 500_000, "smallDonorPercentage": 50}
        score = _calc_constituent_funding(funding)
        assert 40 <= score <= 60

    def test_no_funding_data(self):
        assert _calc_constituent_funding({}) == 50
        assert _calc_constituent_funding({"totalRaised": 0}) == 50


class TestIndependenceIndex:
    """Higher score = less captured by donor interests."""

    def test_no_data_returns_neutral(self):
        """No votes AND no lobbying → neutral 50, not a perfect 100."""
        record = {"donorAlignedVotes": 0, "donorOpposedVotes": 0, "_funding": {}}
        assert _calc_independence_index(record, []) == 50

    def test_fully_captured(self):
        record = {
            "donorAlignedVotes": 100,
            "donorOpposedVotes": 0,
            "_funding": {"totalRaised": 1_000_000, "totalFromPACs": 1_000_000},
        }
        assert _calc_independence_index(record, []) == 0

    def test_donor_aligned_but_no_pac_money(self):
        """Donor-aligned votes without PAC money = ideological, not captured."""
        record = {
            "donorAlignedVotes": 100,
            "donorOpposedVotes": 0,
            "_funding": {"totalRaised": 1_000_000, "totalFromPACs": 0},
        }
        assert _calc_independence_index(record, []) == 100

    def test_pac_money_but_donor_opposed(self):
        """PAC money but votes against donors = independent despite donors."""
        record = {
            "donorAlignedVotes": 0,
            "donorOpposedVotes": 100,
            "_funding": {"totalRaised": 1_000_000, "totalFromPACs": 1_000_000},
        }
        assert _calc_independence_index(record, []) == 100

    def test_moderate_capture(self):
        record = {
            "donorAlignedVotes": 50,
            "donorOpposedVotes": 50,
            "_funding": {"totalRaised": 1_000_000, "totalFromPACs": 500_000},
        }
        score = _calc_independence_index(record, [])
        assert 60 <= score <= 85

    def test_lobbying_alignment_penalizes(self):
        """Lobbying matches where senator votes with donors should reduce score."""
        record = {
            "donorAlignedVotes": 0,
            "donorOpposedVotes": 0,
            "_funding": {"totalRaised": 1_000_000, "totalFromPACs": 500_000},
        }
        all_aligned = [
            {"senatorVoteAligned": True},
            {"senatorVoteAligned": True},
            {"senatorVoteAligned": True},
        ]
        score = _calc_independence_index(record, all_aligned)
        assert score < 100  # penalty applied

    def test_lobbying_no_alignment_no_penalty(self):
        """Lobbying matches where senator votes AGAINST donors = no penalty."""
        record = {
            "donorAlignedVotes": 0,
            "donorOpposedVotes": 0,
            "_funding": {"totalRaised": 1_000_000, "totalFromPACs": 500_000},
        }
        none_aligned = [
            {"senatorVoteAligned": False},
            {"senatorVoteAligned": False},
        ]
        score = _calc_independence_index(record, none_aligned)
        assert score == 100


class TestDonorDiversity:
    """Higher score = more diverse funding sources. Uses inverse HHI."""

    def test_single_industry_monopoly(self):
        breakdown = [{"industry": "FINANCE", "percentage": 100}]
        assert _calc_donor_diversity(breakdown) == 0

    def test_perfectly_even_five_industries(self):
        breakdown = [{"industry": f"IND_{i}", "percentage": 20} for i in range(5)]
        score = _calc_donor_diversity(breakdown)
        assert score == 100

    def test_empty_breakdown(self):
        assert _calc_donor_diversity([]) == 50

    def test_other_industry_excluded(self):
        breakdown = [
            {"industry": "FINANCE", "percentage": 50},
            {"industry": "OTHER", "percentage": 50},
        ]
        score = _calc_donor_diversity(breakdown)
        assert score == 0  # single known industry = monopoly

    def test_small_donors_excluded_from_hhi(self):
        """SMALL_DONORS and LARGE_INDIVIDUAL are not industries."""
        breakdown = [
            {"industry": "FINANCE", "percentage": 30},
            {"industry": "TECH", "percentage": 30},
            {"industry": "SMALL_DONORS", "percentage": 20},
            {"industry": "LARGE_INDIVIDUAL", "percentage": 20},
        ]
        score = _calc_donor_diversity(breakdown)
        # Only FINANCE and TECH count → 50/50 split → HHI=0.5
        # 2 even industries is moderate diversity, not high
        assert 50 <= score <= 70

    def test_political_excluded_from_hhi(self):
        """POLITICAL (party PACs) are not industry influence."""
        breakdown = [
            {"industry": "PHARMA", "percentage": 40},
            {"industry": "POLITICAL", "percentage": 60},
        ]
        score = _calc_donor_diversity(breakdown)
        assert score == 0  # single industry = monopoly


class TestPromiseFulfillment:
    """Higher score = more kept promises."""

    def test_all_kept(self):
        promises = [{"alignment": "kept"}, {"alignment": "kept"}, {"alignment": "kept"}]
        assert _calc_promise_fulfillment({}, "D", promises) == 100

    def test_all_broken(self):
        promises = [{"alignment": "broken"}, {"alignment": "broken"}]
        assert _calc_promise_fulfillment({}, "D", promises) == 0

    def test_mixed(self):
        promises = [
            {"alignment": "kept"},
            {"alignment": "partial"},
            {"alignment": "broken"},
        ]
        score = _calc_promise_fulfillment({}, "D", promises)
        assert score == 50  # (1.0 + 0.5 + 0.0) / 3 * 100 = 50

    def test_unclear_excluded(self):
        promises = [
            {"alignment": "kept"},
            {"alignment": "unclear"},  # excluded from scoring
        ]
        assert _calc_promise_fulfillment({}, "D", promises) == 100

    def test_no_data_returns_neutral(self):
        """No platform data and no flip-flop → neutral 50, not party loyalty."""
        assert _calc_promise_fulfillment({}, "D", None) == 50
        assert _calc_promise_fulfillment({}, "R", None) == 50
        assert _calc_promise_fulfillment({}, "I", None) == 50

    def test_flip_flop_fallback(self):
        """When no promises but flip-flop score exists, use inverted flip-flop."""
        ff = {"flipFlopScore": 30}
        assert _calc_promise_fulfillment({}, "D", None, ff) == 70

    def test_flip_flop_high_inconsistency(self):
        ff = {"flipFlopScore": 80}
        assert _calc_promise_fulfillment({}, "R", None, ff) == 20


class TestAccountability:
    """Higher score = more accountable. No tenure penalty."""

    def test_fully_present_no_lobbying(self):
        """Perfect attendance + no lobbying alignment + no PAC money = high score."""
        senator = {
            "votingRecord": {
                "keyVotes": [
                    {"vote": "Yea"}, {"vote": "Nay"}, {"vote": "Yea"},
                ],
            },
            "funding": {"totalRaised": 1_000_000, "totalFromPACs": 0},
            "yearsInOffice": 30,  # tenure should NOT penalize
        }
        score = _calc_accountability(senator, [])
        assert score >= 90

    def test_tenure_not_penalized(self):
        """Same senator data with 0 and 30 years should score the same."""
        base = {
            "votingRecord": {
                "keyVotes": [{"vote": "Yea"}, {"vote": "Nay"}],
            },
            "funding": {"totalRaised": 1_000_000, "totalFromPACs": 200_000},
        }
        new_senator = {**base, "yearsInOffice": 0}
        vet_senator = {**base, "yearsInOffice": 30}
        assert _calc_accountability(new_senator, []) == _calc_accountability(vet_senator, [])

    def test_missed_votes_penalize(self):
        """Senators who miss votes should score lower."""
        senator = {
            "votingRecord": {
                "keyVotes": [
                    {"vote": "Not Voting"}, {"vote": "Not Voting"},
                    {"vote": "Not Voting"}, {"vote": "Yea"},
                ],
            },
            "funding": {"totalRaised": 1_000_000, "totalFromPACs": 0},
            "yearsInOffice": 5,
        }
        score = _calc_accountability(senator, [])
        assert score < 80  # 75% missed votes should hurt

    def test_lobbying_alignment_penalizes(self):
        """High lobbying alignment rate should lower accountability."""
        senator = {
            "votingRecord": {"keyVotes": [{"vote": "Yea"}]},
            "funding": {"totalRaised": 1_000_000, "totalFromPACs": 500_000},
            "yearsInOffice": 10,
        }
        all_aligned = [
            {"senatorVoteAligned": True},
            {"senatorVoteAligned": True},
            {"senatorVoteAligned": True},
        ]
        score = _calc_accountability(senator, all_aligned)
        assert score < 60

    def test_no_data_returns_neutral(self):
        senator = {"yearsInOffice": 10, "funding": {}, "votingRecord": {}}
        score = _calc_accountability(senator, [])
        assert score == 50


class TestCalculateScoresIntegration:
    """Full calculate_scores integration."""

    def test_returns_all_five_scores(self):
        senator = {
            "funding": {
                "totalRaised": 1_000_000,
                "totalFromPACs": 200_000,
                "smallDonorPercentage": 30,
                "industryBreakdown": [
                    {"industry": "FINANCE", "percentage": 30},
                    {"industry": "TECH", "percentage": 20},
                    {"industry": "HEALTHCARE", "percentage": 15},
                    {"industry": "DEFENSE", "percentage": 10},
                    {"industry": "OTHER", "percentage": 25},
                ],
            },
            "votingRecord": {
                "donorAlignedVotes": 30,
                "donorOpposedVotes": 70,
                "votedWithPartyCount": 85,
                "votedAgainstPartyCount": 15,
                "partyLoyaltyPct": 85.0,
                "keyVotes": [
                    {"vote": "Yea"}, {"vote": "Nay"}, {"vote": "Yea"},
                ],
            },
            "lobbyingMatches": [
                {"senatorVoteAligned": True},
                {"senatorVoteAligned": False},
            ],
            "yearsInOffice": 12,
            "party": "D",
            "campaignPromises": [],
        }

        scores = calculate_scores(senator, None)

        assert "constituentFunding" in scores
        assert "independenceIndex" in scores
        assert "donorDiversity" in scores
        assert "promiseFulfillment" in scores
        assert "accountability" in scores

        for key, value in scores.items():
            assert 0 <= value <= 100, f"{key} = {value} out of bounds"

    def test_empty_senator_returns_neutral_scores(self):
        """A senator with no data should get neutral scores, not inflated ones."""
        senator = {
            "funding": {},
            "votingRecord": {},
            "lobbyingMatches": [],
            "yearsInOffice": 0,
            "party": "D",
            "campaignPromises": [],
        }
        scores = calculate_scores(senator, None)
        for key, value in scores.items():
            assert 40 <= value <= 60, (
                f"{key} = {value}; empty data should yield neutral scores"
            )
