"""Tests for the five representation sub-score calculations."""

import pytest

from app.pipeline.analyze.score_calculator import (
    _calc_funding_diversity,
    _calc_funding_independence,
    _calc_independent_voting,
    _calc_legislative_effectiveness,
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
    """Higher score = less PAC dependency, more small donors, less
    top-donor concentration (v4: 50% PAC+outside / 25% small / 25% conc)."""

    def test_ideal_grassroots(self):
        # 0% PAC (→100), 55% small donors (→100), donor pool too small to
        # measure concentration (→ neutral 50): 50 + 25 + 12.5 = 87.5.
        funding = {
            "totalRaised": 1_000_000,
            "totalFromPACs": 0,
            "smallDonorPercentage": 55,
            "topDonors": [{"total": 100} for _ in range(10)],
        }
        score = _calc_funding_independence(funding)
        assert score >= 85

    def test_fully_pac_funded_concentrated(self):
        funding = {
            "totalRaised": 1_000_000,
            "totalFromPACs": 1_000_000,
            "topDonors": [{"total": 500_000}],
        }
        assert _calc_funding_independence(funding) < 20

    def test_balanced_funding(self):
        # Median-ish senator: 30% PAC (→40), 17% small donors (→42.5),
        # concentration pool below the $250K floor (→ neutral 50):
        # FI = 0.5*40 + 0.25*42.5 + 0.25*50 ≈ 43.
        funding = {
            "totalRaised": 1_000_000,
            "totalFromPACs": 300_000,
            "smallDonorPercentage": 17,
            "topDonors": [{"total": 20_000} for _ in range(10)],
        }
        score = _calc_funding_independence(funding)
        assert 35 <= score <= 60

    def test_no_funding_data(self):
        assert _calc_funding_independence({}) == 50
        assert _calc_funding_independence({"totalRaised": 0}) == 50

    def test_low_pac_but_concentrated(self):
        """Low PAC ratio but a top-heavy donor pool = penalized for concentration."""
        funding = {
            "totalRaised": 1_000_000,
            "totalFromPACs": 50_000,
            # Top 10 donors hold 800K of an 830K external pool (96%).
            "topDonors": (
                [{"total": 80_000} for _ in range(10)]
                + [{"total": 2_000} for _ in range(15)]
            ),
        }
        score = _calc_funding_independence(funding)
        assert score < 50

    def test_pac_volume_penalizes_diluted_pac_money(self):
        """$5M of PAC money must not vanish inside a $100M campaign.

        Share alone gave mega-fundraisers near-perfect PAC scores because
        capped PAC checks shrink as a fraction of unbounded individual
        money (audit: FI vs log(raised) r=+0.68).
        """
        small_campaign = {
            "totalRaised": 10_000_000,
            "totalFromPACs": 500_000,   # 5% share, $0.5M volume
            "smallDonorPercentage": 15,
            "topDonors": [],
        }
        mega_campaign = {
            "totalRaised": 100_000_000,
            "totalFromPACs": 5_000_000,  # same 5% share, $5M volume
            "smallDonorPercentage": 15,
            "topDonors": [],
        }
        assert _calc_funding_independence(mega_campaign) < _calc_funding_independence(small_campaign)

    def test_own_committees_excluded_from_concentration(self):
        """Transfers from the candidate's own committees are not donors.

        A senator whose 'top donors' are their own victory committees
        (routine joint fundraising) must not be scored as captured —
        the 2026-06 audit found this artifact put a reference senator
        at FI 31.
        """
        base = {
            "totalRaised": 5_000_000,
            "totalFromPACs": 250_000,
            "smallDonorPercentage": 20,
            "topDonors": [{"total": 30_000} for _ in range(30)],
        }
        with_transfers = {
            **base,
            "topDonors": [
                {"total": 2_000_000, "type": "CandidateAffiliated"},
                {"total": 1_000_000, "type": "CandidateAffiliated"},
            ] + base["topDonors"],
        }
        assert _calc_funding_independence(with_transfers) == _calc_funding_independence(base)

    def test_many_pacs_but_diversified(self):
        """High PAC ratio but spread across many small PACs.

        With the 30/70 PAC/concentration weighting (score v3), the diversified
        concentration (10% top-10 → concentration_score=75) partially offsets
        the terrible PAC ratio (70% → pac_score=0), yielding ~52.
        The score is below 60 — not good — but not zero because no single
        donor dominates the funding base.
        """
        funding = {
            "totalRaised": 1_000_000,
            "totalFromPACs": 700_000,
            "topDonors": [{"total": 10_000} for _ in range(10)],
        }
        score = _calc_funding_independence(funding)
        assert score < 60

    def test_amplified_penalties_create_spread(self):
        """Different PAC ratios should produce meaningfully different scores."""
        funding_low_pac = {
            "totalRaised": 1_000_000,
            "totalFromPACs": 50_000,
            "topDonors": [{"total": 5_000} for _ in range(10)],
        }
        funding_high_pac = {
            "totalRaised": 1_000_000,
            "totalFromPACs": 300_000,
            "topDonors": [{"total": 20_000} for _ in range(10)],
        }
        score_low = _calc_funding_independence(funding_low_pac)
        score_high = _calc_funding_independence(funding_high_pac)
        assert score_low > score_high
        assert score_low - score_high >= 10


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
            "keyVotes": self._make_votes(with_party=70, against_party=30),
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
        """A Republican in a deep red state voting with the party should score OK."""
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

    def test_nomination_votes_weighted_like_legislation(self):
        """Nominations are whipped party-line tests; they count at full weight.

        (A ×0.5 down-weighting experiment inflated IV for members whose
        loyalty concentrates on nominations — see the note in
        _calc_independent_voting.)
        """
        funding = {"totalRaised": 1_000_000, "totalFromPACs": 200_000}
        legis = {
            "keyVotes": [
                {"votedWithParty": i >= 10, "policyArea": "JUSTICE",
                 "vote": "Yea", "stance": "neutral"}
                for i in range(100)
            ],
            "recentVotes": [],
        }
        noms = {
            "keyVotes": [
                {"votedWithParty": i >= 10, "policyArea": "JUSTICE",
                 "vote": "Yea", "stance": "nomination"}
                for i in range(100)
            ],
            "recentVotes": [],
        }
        assert _calc_independent_voting(legis, [], funding) == \
            _calc_independent_voting(noms, [], funding)

    def test_two_stage_curve_creates_spread(self):
        """Different independence levels should produce meaningfully different scores."""
        funding = {"totalRaised": 1_000_000, "totalFromPACs": 200_000}
        scores = []
        for against in [0, 5, 10, 20, 40]:
            record = {
                "keyVotes": self._make_votes(with_party=100 - against, against_party=against),
                "recentVotes": [],
            }
            scores.append(_calc_independent_voting(record, [], funding))
        # Scores should be monotonically increasing
        for i in range(1, len(scores)):
            assert scores[i] >= scores[i - 1]
        # And there should be meaningful spread
        assert scores[-1] - scores[0] >= 25


class TestFundingDiversity:
    """Higher score = broader, more distributed funding base."""

    def test_fully_classified_itemized(self):
        funding = {
            "totalRaised": 1_000_000,
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
            "totalRaised": 1_000_000,
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
            "totalRaised": 1_000_000,
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
        # Beta-Binomial posterior (Morris 1983, PRIOR_PSEUDOCOUNT=10):
        # 3 kept → posterior = (3+5)/(3+10)*100 = 61.5, blended ≈ 65.
        # Range updated from v1 (70-95) to reflect Beta(5,5) shrinkage —
        # sparse samples (n=3) stay closer to the neutral prior of 50.
        promises = [{"alignment": "kept"}, {"alignment": "kept"}, {"alignment": "kept"}]
        score = _calc_promise_persistence({}, "D", promises)
        assert 58 <= score <= 85

    def test_all_broken(self):
        # Beta-Binomial posterior (Morris 1983, PRIOR_PSEUDOCOUNT=10):
        # 2 broken → posterior = (0+5)/(2+10)*100 = 41.7, blended ≈ 48.
        # Range updated from v1 (score<=45) — n=2 sparse data should not
        # anchor far from neutral; the prior dominates at this sample size.
        promises = [{"alignment": "broken"}, {"alignment": "broken"}]
        score = _calc_promise_persistence({}, "D", promises)
        assert score <= 52

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
        assert score_inflated < 70

    def test_all_unclear_returns_neutral(self):
        promises = [{"alignment": "unclear"}, {"alignment": "unclear"}]
        score = _calc_promise_persistence({}, "D", promises)
        assert 45 <= score <= 60

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
        score_with = _calc_promise_persistence({}, "D", promises, advocacy)
        score_without = _calc_promise_persistence({}, "D", promises, None)
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


class TestLegislativeEffectiveness:
    """Higher score = more bills passed, higher leadership, more active sponsorship."""

    def test_no_data_returns_neutral(self):
        score = _calc_legislative_effectiveness([], None)
        assert score == 50

    def test_no_bills_with_leadership(self):
        """Leadership alone should shift score above 50."""
        score = _calc_legislative_effectiveness([], 0.8)
        assert score > 50

    def test_prolific_but_no_passage(self):
        """Many bills introduced but none advanced — moderate score from volume only."""
        bills = [
            {"title": f"Bill {i}", "isLaw": False, "latestAction": "Introduced",
             "billType": "s", "congress": 119}
            for i in range(50)
        ]
        score = _calc_legislative_effectiveness(bills, None)
        assert 30 <= score <= 55

    def test_high_passage_rate(self):
        """Bills that became law should boost the score significantly."""
        bills = [
            {"title": "Good Bill", "isLaw": True, "latestAction": "Became public law",
             "billType": "s", "congress": 119},
            {"title": "Also Good", "isLaw": True, "latestAction": "Became public law",
             "billType": "s", "congress": 119},
            {"title": "Decent", "isLaw": False, "latestAction": "Passed Senate",
             "billType": "s", "congress": 119},
        ] + [
            {"title": f"Bill {i}", "isLaw": False, "latestAction": "Introduced",
             "billType": "s", "congress": 119}
            for i in range(10)
        ]
        score = _calc_legislative_effectiveness(bills, 0.5)
        assert score >= 60

    def test_leadership_matters(self):
        """Higher PageRank leadership should produce higher score."""
        bills = [{"title": f"B{i}", "isLaw": False, "latestAction": "Introduced"} for i in range(20)]
        score_low = _calc_legislative_effectiveness(bills, 0.1)
        score_high = _calc_legislative_effectiveness(bills, 0.9)
        assert score_high > score_low

    def test_advancement_keywords(self):
        """Committee/chamber milestones count as advanced; calendar placement doesn't.

        Senate Rule XIV places bills on the calendar without committee
        action, so "Placed on calendar" signals nothing about advancement.
        """
        introduced = [
            {"title": f"B{i}", "isLaw": False, "latestAction": "Introduced",
             "billType": "s", "congress": 119}
            for i in range(3)
        ]
        reported = [
            {"title": "B1", "isLaw": False, "latestAction": "Ordered to be reported",
             "billType": "s", "congress": 119},
        ] + introduced[:2]
        calendar_only = [
            {"title": "B1", "isLaw": False, "latestAction": "Placed on calendar",
             "billType": "s", "congress": 119},
        ] + introduced[:2]

        score_reported = _calc_legislative_effectiveness(reported, None)
        score_calendar = _calc_legislative_effectiveness(calendar_only, None)
        score_none = _calc_legislative_effectiveness(introduced, None)
        assert score_reported > score_none
        assert score_calendar == score_none

    def test_resolutions_excluded_from_advancement(self):
        """Commemorative resolutions being 'agreed to' must not inflate advancement."""
        substantive_only = [
            {"title": f"B{i}", "isLaw": False, "latestAction": "Introduced",
             "billType": "s", "congress": 119}
            for i in range(10)
        ]
        with_resolutions = substantive_only + [
            {"title": f"R{i}", "isLaw": False,
             "latestAction": "Resolution agreed to in Senate",
             "billType": "sres", "congress": 119}
            for i in range(10)
        ]
        score_plain = _calc_legislative_effectiveness(substantive_only, 0.5)
        score_res = _calc_legislative_effectiveness(with_resolutions, 0.5)
        # The resolutions add volume but must not count as advancement.
        assert score_res <= score_plain + 10


class TestCalculateScoresIntegration:
    """Full calculate_scores integration."""

    def test_returns_all_five_scores(self):
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
            "sponsoredBills": [
                {"title": "Bill 1", "isLaw": True, "latestAction": "Became law"},
                {"title": "Bill 2", "isLaw": False, "latestAction": "Introduced"},
            ],
            "yearsInOffice": 12,
            "party": "D",
            "state": "NY",
            "campaignPromises": [],
        }

        scores = calculate_scores(senator)

        assert "fundingIndependence" in scores
        assert "promisePersistence" in scores
        assert "independentVoting" in scores
        assert "fundingDiversity" in scores
        assert "legislativeEffectiveness" in scores
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
            "sponsoredBills": [],
            "yearsInOffice": 0,
            "party": "D",
            "state": "DC",
            "campaignPromises": [],
        }
        scores = calculate_scores(senator)
        for key, value in scores.items():
            assert 40 <= value <= 60, (
                f"{key} = {value}; empty data should yield neutral scores"
            )
