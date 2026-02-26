"""Tests for the four representation sub-score calculations."""

import pytest

from app.pipeline.analyze.score_calculator import (
    _calc_funding_diversity,
    _calc_funding_independence,
    _calc_independent_voting,
    _calc_promise_persistence,
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


class TestFundingIndependence:
    """Higher score = less PAC dependency, less top-donor concentration."""

    def test_ideal_grassroots(self):
        funding = {
            "totalRaised": 1_000_000,
            "totalFromPACs": 0,
            "topDonors": [{"total": 100} for _ in range(10)],
        }
        score = _calc_funding_independence(funding)
        assert score >= 90

    def test_fully_pac_funded_concentrated(self):
        funding = {
            "totalRaised": 1_000_000,
            "totalFromPACs": 1_000_000,
            "topDonors": [{"total": 500_000}],
        }
        assert _calc_funding_independence(funding) < 30

    def test_balanced_funding(self):
        funding = {
            "totalRaised": 1_000_000,
            "totalFromPACs": 300_000,
            "topDonors": [{"total": 20_000} for _ in range(10)],
        }
        score = _calc_funding_independence(funding)
        assert 50 <= score <= 80

    def test_no_funding_data(self):
        assert _calc_funding_independence({}) == 50
        assert _calc_funding_independence({"totalRaised": 0}) == 50

    def test_low_pac_but_concentrated(self):
        """Low PAC ratio but one big individual donor = penalized for concentration."""
        funding = {
            "totalRaised": 1_000_000,
            "totalFromPACs": 50_000,
            "topDonors": [{"total": 800_000}],
        }
        score = _calc_funding_independence(funding)
        assert score < 70

    def test_many_pacs_but_diversified(self):
        """High PAC ratio but spread across many small PACs."""
        funding = {
            "totalRaised": 1_000_000,
            "totalFromPACs": 700_000,
            "topDonors": [{"total": 10_000} for _ in range(10)],
        }
        score = _calc_funding_independence(funding)
        assert score <= 60  # penalized for PAC dependency


class TestIndependentVoting:
    """Higher score = more independent from party and donors."""

    def _make_votes(self, with_party=0, against_party=0, policy="JUSTICE"):
        votes = []
        for _ in range(with_party):
            votes.append({"votedWithParty": True, "policyArea": policy, "vote": "Yea"})
        for _ in range(against_party):
            votes.append({"votedWithParty": False, "policyArea": policy, "vote": "Yea"})
        return votes

    def test_no_data_returns_neutral(self):
        record = {"keyVotes": [], "recentVotes": []}
        score = _calc_independent_voting(record, [], {})
        assert 40 <= score <= 60

    def test_high_party_independence(self):
        record = {
            "keyVotes": self._make_votes(with_party=80, against_party=20),
            "recentVotes": [],
        }
        score = _calc_independent_voting(
            record, [], {"totalRaised": 1_000_000, "totalFromPACs": 0}
        )
        assert score >= 70

    def test_pure_party_line_voter(self):
        record = {
            "keyVotes": self._make_votes(with_party=100, against_party=0),
            "recentVotes": [],
        }
        score = _calc_independent_voting(
            record, [], {"totalRaised": 1_000_000, "totalFromPACs": 500_000}
        )
        assert score < 50

    def test_lobbying_alignment_penalizes(self):
        record = {
            "keyVotes": self._make_votes(with_party=90, against_party=10),
            "recentVotes": [],
        }
        all_aligned = [
            {"senatorVoteAligned": True},
            {"senatorVoteAligned": True},
            {"senatorVoteAligned": True},
        ]
        score_with = _calc_independent_voting(
            record, all_aligned,
            {"totalRaised": 1_000_000, "totalFromPACs": 500_000},
        )
        score_without = _calc_independent_voting(
            record, [],
            {"totalRaised": 1_000_000, "totalFromPACs": 500_000},
        )
        assert score_with < score_without

    def test_state_relevant_party_votes_excluded(self):
        """Party-line votes on state-relevant bills shouldn't count against independence."""
        funding = {
            "totalRaised": 1_000_000,
            "totalFromPACs": 200_000,
            "industryBreakdown": [{"industry": "OIL_GAS", "percentage": 40}],
        }
        energy_votes = self._make_votes(with_party=20, against_party=0, policy="ENERGY")
        other_votes = self._make_votes(with_party=8, against_party=2, policy="JUSTICE")

        record_with_energy = {
            "keyVotes": energy_votes + other_votes,
            "recentVotes": [],
        }
        record_just_other = {
            "keyVotes": other_votes,
            "recentVotes": [],
        }
        score_with_energy = _calc_independent_voting(record_with_energy, [], funding)
        score_just_other = _calc_independent_voting(record_just_other, [], funding)
        assert abs(score_with_energy - score_just_other) <= 5

    def test_deep_red_state_senator_not_penalized(self):
        """A Republican in a deep red state voting with the party should score OK.

        The state lean adjustment lowers the independence threshold, so
        even small break rates in safe states are considered independent.
        """
        record = {
            "keyVotes": self._make_votes(with_party=95, against_party=5),
            "recentVotes": [],
        }
        funding = {"totalRaised": 1_000_000, "totalFromPACs": 200_000}

        score_deep_red = _calc_independent_voting(
            record, [], funding, state="WY", party="R"
        )
        score_swing = _calc_independent_voting(
            record, [], funding, state="NV", party="R"
        )
        assert score_deep_red > score_swing


class TestFundingDiversity:
    """Higher score = broader, more distributed funding base."""

    def test_fully_classified_itemized(self):
        funding = {
            "smallDonorPercentage": 10,
            "industryBreakdown": [
                {"industry": "FINANCE", "percentage": 20},
                {"industry": "TECH", "percentage": 20},
                {"industry": "HEALTHCARE", "percentage": 15},
                {"industry": "DEFENSE", "percentage": 15},
            ],
        }
        score = _calc_funding_diversity(funding)
        assert score >= 65

    def test_grassroots_small_donors_scores_high(self):
        """High small-donor percentage = broad grassroots base = high diversity."""
        funding = {
            "smallDonorPercentage": 90,
            "industryBreakdown": [
                {"industry": "OTHER", "percentage": 5},
            ],
        }
        score = _calc_funding_diversity(funding)
        assert score >= 70

    def test_single_industry_dominated(self):
        """Concentrated in one industry = low diversity score."""
        funding = {
            "smallDonorPercentage": 20,
            "industryBreakdown": [
                {"industry": "OIL_GAS", "percentage": 75},
                {"industry": "OTHER", "percentage": 5},
            ],
        }
        score = _calc_funding_diversity(funding)
        assert score < 60

    def test_empty_breakdown(self):
        score = _calc_funding_diversity({})
        assert score == 50


class TestPromisePersistence:
    """Higher score = more kept promises + floor advocacy boost."""

    def test_all_kept(self):
        promises = [{"alignment": "kept"}, {"alignment": "kept"}, {"alignment": "kept"}]
        score = _calc_promise_persistence({}, "D", promises)
        # With 3 promises, count_conf = 3/5 = 0.6 causes Bayesian shrinkage
        # toward 50, so score won't reach 100
        assert 70 <= score <= 95

    def test_all_broken(self):
        promises = [{"alignment": "broken"}, {"alignment": "broken"}]
        score = _calc_promise_persistence({}, "D", promises)
        # With 2 promises, count_conf = 2/5 = 0.4 causes heavy shrinkage
        assert score <= 45

    def test_mixed(self):
        promises = [
            {"alignment": "kept"},
            {"alignment": "partial"},
            {"alignment": "broken"},
        ]
        score = _calc_promise_persistence({}, "D", promises)
        assert 40 <= score <= 60

    def test_unclear_penalizes_confidence(self):
        """1 kept + 9 unclear should NOT score 100 — low confidence."""
        promises_inflated = [{"alignment": "kept"}] + [
            {"alignment": "unclear"} for _ in range(9)
        ]
        promises_genuine = [{"alignment": "kept"}] * 3

        score_inflated = _calc_promise_persistence({}, "D", promises_inflated)
        score_genuine = _calc_promise_persistence({}, "D", promises_genuine)
        assert score_inflated < score_genuine
        assert score_inflated < 70  # should be pulled toward 50

    def test_all_unclear_returns_neutral(self):
        promises = [{"alignment": "unclear"}, {"alignment": "unclear"}]
        score = _calc_promise_persistence({}, "D", promises)
        assert 45 <= score <= 60  # near neutral, participation component may shift slightly

    def test_no_data_returns_neutral(self):
        score = _calc_promise_persistence({}, "D", None)
        assert 45 <= score <= 60

    def test_vote_independence_fallback(self):
        """When no promises are evaluable, voting independence is used as proxy."""
        voting_record = {
            "keyVotes": [
                {"votedWithParty": False, "vote": "Yea"},
                {"votedWithParty": True, "vote": "Nay"},
                {"votedWithParty": True, "vote": "Yea"},
                {"votedWithParty": False, "vote": "Nay"},
            ],
        }
        score = _calc_promise_persistence(voting_record, "D", None)
        assert 55 <= score <= 75

    def test_floor_advocacy_boosts_score(self):
        promises = [{"alignment": "kept"}, {"alignment": "broken"}]
        advocacy = {
            "advocacyCoverage": 1.0,
            "totalRemarks": 25,
            "advocatedCategories": ["healthcare", "economy"],
            "remarksByCategory": {"healthcare": 15, "economy": 10},
        }
        score_with = _calc_promise_persistence({}, "D", promises, None, advocacy)
        score_without = _calc_promise_persistence({}, "D", promises, None, None)
        assert score_with > score_without

    def test_participation_folded_in(self):
        """Low vote participation should reduce promise persistence score."""
        promises = [{"alignment": "kept"}, {"alignment": "kept"}]
        record_active = {
            "keyVotes": [{"vote": "Yea"} for _ in range(10)],
            "recentVotes": [],
        }
        record_absent = {
            "keyVotes": [{"vote": "Not Voting"} for _ in range(8)]
                + [{"vote": "Yea"} for _ in range(2)],
            "recentVotes": [],
        }
        score_active = _calc_promise_persistence(record_active, "D", promises)
        score_absent = _calc_promise_persistence(record_absent, "D", promises)
        assert score_active > score_absent


class TestCalculateScoresIntegration:
    """Full calculate_scores integration."""

    def test_returns_all_four_scores(self):
        senator = {
            "funding": {
                "totalRaised": 1_000_000,
                "totalFromPACs": 200_000,
                "smallDonorPercentage": 30,
                "topDonors": [{"total": 20_000} for _ in range(10)],
                "industryBreakdown": [
                    {"industry": "FINANCE", "percentage": 30},
                    {"industry": "TECH", "percentage": 20},
                    {"industry": "HEALTHCARE", "percentage": 15},
                    {"industry": "DEFENSE", "percentage": 10},
                    {"industry": "OTHER", "percentage": 25},
                ],
            },
            "votingRecord": {
                "keyVotes": [
                    {"vote": "Yea", "votedWithParty": True, "policyArea": "HEALTHCARE"},
                    {"vote": "Nay", "votedWithParty": False, "policyArea": "DEFENSE"},
                    {"vote": "Yea", "votedWithParty": True, "policyArea": "JUSTICE"},
                ],
                "recentVotes": [],
            },
            "lobbyingMatches": [
                {"senatorVoteAligned": True},
                {"senatorVoteAligned": False},
            ],
            "yearsInOffice": 12,
            "party": "D",
            "state": "NY",
            "campaignPromises": [],
        }

        scores = calculate_scores(senator, None)

        assert "fundingIndependence" in scores
        assert "promisePersistence" in scores
        assert "independentVoting" in scores
        assert "fundingDiversity" in scores
        assert "transparency" not in scores
        assert "accessibility" not in scores

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
            "state": "DC",
            "campaignPromises": [],
        }
        scores = calculate_scores(senator, None)
        for key, value in scores.items():
            assert 40 <= value <= 60, (
                f"{key} = {value}; empty data should yield neutral scores"
            )
