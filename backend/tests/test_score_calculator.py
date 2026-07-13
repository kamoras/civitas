"""Tests for the five representation sub-score calculations."""


from app.pipeline.analyze.score_calculator import (
    _calc_constituent_alignment,
    _calc_funding_diversity,
    _calc_funding_independence,
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


class TestConstituentAlignment:
    """v4.2: score is relative to what the seat's electorate expects.

    Matching the seat's expected break rate ≈ neutral 50; crossing
    beyond it earns credit; hyper-loyalty in a swing/opposed seat
    drifts below neutral but never to a failure-grade floor.
    """

    def _make_votes(self, with_party=0, against_party=0, policy="JUSTICE"):
        votes = []
        for _ in range(with_party):
            votes.append({"votedWithParty": True, "policyArea": policy, "vote": "Yea"})
        for _ in range(against_party):
            votes.append({"votedWithParty": False, "policyArea": policy, "vote": "Yea"})
        return votes

    def test_no_data_returns_neutral(self):
        record = {"keyVotes": [], "recentVotes": []}
        score = _calc_constituent_alignment(record, [], {})
        assert 40 <= score <= 60

    def test_frequent_crosser_scores_high(self):
        record = {
            "keyVotes": self._make_votes(with_party=70, against_party=30),
            "recentVotes": [],
        }
        score = _calc_constituent_alignment(
            record, [], {"totalRaised": 1_000_000, "totalFromPACs": 0}
        )
        assert score >= 70

    def test_pure_party_line_voter_below_neutral_in_swing_seat(self):
        record = {
            "keyVotes": self._make_votes(with_party=100, against_party=0),
            "recentVotes": [],
        }
        score = _calc_constituent_alignment(
            record, [], {"totalRaised": 1_000_000, "totalFromPACs": 500_000}
        )
        assert score < 50

    def test_safe_seat_loyalist_scores_near_neutral(self):
        """THE v4.2 regression test: a member of a deep-safe seat voting
        the way the seat elected them to is typical representation
        (≈50), not a failure grade. v4.1 pinned 73/100 senators at a
        floor of ~26-38 for exactly this behavior."""
        record = {
            "keyVotes": self._make_votes(with_party=97, against_party=3),
            "recentVotes": [],
        }
        funding = {"totalRaised": 1_000_000, "totalFromPACs": 200_000}
        score = _calc_constituent_alignment(
            record, [], funding, state="ID", party="R"
        )
        assert 45 <= score <= 62

    def test_district_lean_overrides_state_lean_for_house(self):
        """A loyalist Democrat in a deep-blue district of a red state is a
        safe-seat member (typical representation, ~neutral), not an
        'opposed seat' member who should be crossing 20% of the time.
        AL-7 is D+13 while Alabama is R+15."""
        record = {
            "keyVotes": self._make_votes(with_party=97, against_party=3),
            "recentVotes": [],
        }
        funding = {"totalRaised": 1_000_000, "totalFromPACs": 200_000}
        with_state_only = _calc_constituent_alignment(
            record, [], funding, state="AL", party="D"
        )
        with_district = _calc_constituent_alignment(
            record, [], funding, state="AL", party="D", district=7
        )
        assert with_district > with_state_only
        assert 45 <= with_district <= 62  # same band as any safe-seat loyalist

    def test_unknown_district_falls_back_to_state(self):
        record = {
            "keyVotes": self._make_votes(with_party=97, against_party=3),
            "recentVotes": [],
        }
        funding = {"totalRaised": 1_000_000, "totalFromPACs": 200_000}
        assert _calc_constituent_alignment(
            record, [], funding, state="ID", party="R", district=99
        ) == _calc_constituent_alignment(
            record, [], funding, state="ID", party="R"
        )

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
        score_with = _calc_constituent_alignment(
            record, all_aligned,
            {"totalRaised": 1_000_000, "totalFromPACs": 500_000},
        )
        score_without = _calc_constituent_alignment(
            record, [],
            {"totalRaised": 1_000_000, "totalFromPACs": 500_000},
        )
        assert score_with < score_without

    def test_donor_industry_votes_no_longer_exempt(self):
        """v4.1 exempted party-line votes on policy areas related to the
        member's top DONOR industries — backwards under the representation
        north star (it shielded the votes most suspect for capture).
        Those votes now count like any other party-line vote."""
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
        score_with_energy = _calc_constituent_alignment(record_with_energy, [], funding)
        score_just_other = _calc_constituent_alignment(record_just_other, [], funding)
        # 20 extra party-line votes dilute the break rate → lower score
        assert score_with_energy < score_just_other

    def test_swing_seat_loyalty_scores_below_safe_seat_loyalty(self):
        """The same 95% party-line record represents a deep-red state's
        electorate but diverges from a swing state's median voter."""
        record = {
            "keyVotes": self._make_votes(with_party=95, against_party=5),
            "recentVotes": [],
        }
        funding = {"totalRaised": 1_000_000, "totalFromPACs": 200_000}

        score_deep_red = _calc_constituent_alignment(
            record, [], funding, state="WY", party="R"
        )
        score_swing = _calc_constituent_alignment(
            record, [], funding, state="NV", party="R"
        )
        assert score_deep_red > score_swing

    def test_opposed_seat_expects_more_crossing(self):
        """A member whose party opposes the state lean is expected to
        cross more; the same loyal record scores lower there than in a
        seat aligned with the party."""
        record = {
            "keyVotes": self._make_votes(with_party=95, against_party=5),
            "recentVotes": [],
        }
        funding = {"totalRaised": 1_000_000, "totalFromPACs": 200_000}
        score_opposed = _calc_constituent_alignment(
            record, [], funding, state="MA", party="R"
        )
        score_aligned = _calc_constituent_alignment(
            record, [], funding, state="ID", party="R"
        )
        assert score_opposed < score_aligned

    def test_crossing_not_rewarded_for_its_own_sake(self):
        """Owner principle (2026-07-04): the goal is carrying out the
        will of constituents, not defection. The same 25% break rate
        earns much more in a swing seat (crossing toward the median
        voter) than in a deep aligned seat (crossing away from it).
        Safe-seat surplus crossing sits near neutral — not virtue, not
        defiance — because break direction relative to state opinion is
        unobservable. This is also the guardrail that kept a 9%-break
        party leader from scoring as an independent (2026-06 audit)."""
        record = {
            "keyVotes": self._make_votes(with_party=75, against_party=25),
            "recentVotes": [],
        }
        funding = {"totalRaised": 1_000_000, "totalFromPACs": 200_000}
        score_safe = _calc_constituent_alignment(
            record, [], funding, state="WY", party="R"
        )
        score_swing = _calc_constituent_alignment(
            record, [], funding, state="NV", party="R"
        )
        # Swing-seat crossing = toward the median voter = clearly better.
        assert score_swing - score_safe >= 10
        # Safe-seat surplus crossing hovers near neutral, never failure.
        assert 50 <= score_safe <= 70
        assert score_swing > 70

    def test_nomination_votes_weighted_like_legislation(self):
        """Nominations are whipped party-line tests; they count at full weight.

        (A ×0.5 down-weighting experiment inflated the score for members
        whose loyalty concentrates on nominations — see the note in
        _calc_constituent_alignment.)
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
        assert _calc_constituent_alignment(legis, [], funding) == \
            _calc_constituent_alignment(noms, [], funding)

    def test_break_rate_monotonic_with_spread(self):
        """More crossing (relative to the same seat) never lowers the
        score, and the range is meaningful."""
        funding = {"totalRaised": 1_000_000, "totalFromPACs": 200_000}
        scores = []
        for against in [0, 5, 10, 20, 40]:
            record = {
                "keyVotes": self._make_votes(with_party=100 - against, against_party=against),
                "recentVotes": [],
            }
            scores.append(_calc_constituent_alignment(record, [], funding))
        for i in range(1, len(scores)):
            assert scores[i] >= scores[i - 1]
        assert scores[-1] - scores[0] >= 25


class TestFundingDiversity:
    """Higher score = broader, more distributed funding base."""

    def test_fully_classified_itemized(self):
        funding = {
            "totalRaised": 1_000_000,
            "smallDonorPercentage": 10,
            "industryBreakdown": [
                {"industry": "FINANCE", "total": 200_000, "percentage": 20},
                {"industry": "TECH", "total": 200_000, "percentage": 20},
                {"industry": "HEALTHCARE", "total": 150_000, "percentage": 15},
                {"industry": "DEFENSE", "total": 150_000, "percentage": 15},
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
                {"industry": "OTHER", "total": 50_000, "percentage": 5},
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
                {"industry": "OIL_GAS", "total": 750_000, "percentage": 75},
                {"industry": "OTHER", "total": 50_000, "percentage": 5},
            ],
        }
        score = _calc_funding_diversity(funding)
        assert score < 60

    def test_empty_breakdown(self):
        score = _calc_funding_diversity({})
        assert score == 50

    def test_unclassified_excluded_from_concentration(self):
        """UNCLASSIFIED (donations the classifier couldn't attribute to any
        industry) must not itself count as a dominant 'industry' — it's an
        unknown bucket, semantically the same as OTHER/POLITICAL. A 2026-07
        audit found this bug alone made 95/100 senators look ~100%
        industry-concentrated."""
        funding = {
            "totalRaised": 1_000_000,
            "smallDonorPercentage": 20,
            "industryBreakdown": [
                {"industry": "UNCLASSIFIED", "total": 600_000, "percentage": 60},
                {"industry": "FINANCE", "total": 100_000, "percentage": 10},
                {"industry": "TECH", "total": 100_000, "percentage": 10},
                {"industry": "HEALTHCARE", "total": 100_000, "percentage": 10},
                {"industry": "DEFENSE", "total": 100_000, "percentage": 10},
            ],
        }
        score = _calc_funding_diversity(funding)
        # 4 real industries evenly split among the classified money should
        # score as diverse, not as concentrated in UNCLASSIFIED.
        assert score >= 55

    def test_small_dollar_industries_not_rounded_to_zero(self):
        """Real dollar totals must drive concentration, not the stored
        display 'percentage' (rounded to the nearest integer point, which
        zeroes out any industry under ~0.5% of a large total_raised — a
        2026-07 audit found this true for 86.5% of industry rows)."""
        total_raised = 10_000_000
        # 15 industries, each $45K (0.45% of total_raised -> rounds to 0%
        # individually), summing to a real, evenly-spread 6.75%.
        industries = [
            {"industry": f"IND_{i}", "total": 45_000, "percentage": 0}
            for i in range(15)
        ]
        funding = {
            "totalRaised": total_raised,
            "smallDonorPercentage": 20,
            "industryBreakdown": industries,
        }
        score = _calc_funding_diversity(funding)

        # Counterfactual: same rounded-to-zero percentages, with 'total'
        # also zeroed — equivalent to what the pre-fix percentage-only
        # logic actually saw, since it never read 'total' at all.
        blind_industries = [
            {"industry": f"IND_{i}", "total": 0, "percentage": 0}
            for i in range(15)
        ]
        blind_score = _calc_funding_diversity({
            "totalRaised": total_raised,
            "smallDonorPercentage": 20,
            "industryBreakdown": blind_industries,
        })
        # Dollar totals should recover a meaningfully higher score than
        # the percentage-blind path, which sees no classified signal at
        # all despite ~6.75% of funding genuinely being industry-spread.
        assert score - blind_score >= 5


class TestPromisePersistence:
    """Higher score = more kept promises + floor advocacy boost."""

    def test_all_kept(self):
        # Beta-Binomial posterior (Morris 1983, PRIOR_PSEUDOCOUNT=6):
        # 3 kept → posterior = (3+3)/(3+6)*100 = 66.7, blended ≈ 70.
        # Range covers both the pre- and post-v5.1-threshold regime —
        # sparse samples (n=3) stay closer to the neutral prior of 50.
        promises = [{"alignment": "kept"}, {"alignment": "kept"}, {"alignment": "kept"}]
        score = _calc_promise_persistence({}, "D", promises)
        assert 58 <= score <= 85

    def test_all_broken(self):
        # Beta-Binomial posterior (Morris 1983, PRIOR_PSEUDOCOUNT=6):
        # 2 broken → posterior = (0+3)/(2+6)*100 = 37.5, blended ≈ 44.
        # n=2 sparse data should not anchor far from neutral; the prior
        # dominates at this sample size.
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

    def test_population_retains_spread_at_typical_evaluable_count(self):
        """The v5.1 evidence-threshold recalibration (0.80/0.82 relevance)
        roughly halved evaluable promises per member — senators now
        average ~2-3 scoreable promises rather than ~5 (2026-07-10 audit).
        A population of members whose ACTUAL kept-fraction spans the full
        range must still show real spread at that sample size, not
        collapse toward a shared near-neutral score."""
        profiles = [
            [{"alignment": "broken"}] * 3,
            [{"alignment": "broken"}, {"alignment": "broken"}, {"alignment": "partial"}],
            [{"alignment": "partial"}] * 3,
            [{"alignment": "kept"}, {"alignment": "partial"}, {"alignment": "broken"}],
            [{"alignment": "kept"}, {"alignment": "kept"}, {"alignment": "partial"}],
            [{"alignment": "kept"}] * 3,
        ]
        scores = [_calc_promise_persistence({}, "D", p) for p in profiles]
        assert max(scores) - min(scores) >= 20
        assert scores == sorted(scores)  # monotonic in kept-fraction


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
        assert 20 <= score <= 45

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
        assert score >= 55

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

    def test_volume_ceiling_not_saturated_by_top_decile(self):
        """The 80/congress ceiling was calibrated in 2026-06 against a p90
        of ~69; a 2026-07 audit found the live distribution had drifted to
        p90=108 with 22/100 senators already saturating it, including the
        single most active sponsor at 149.5/congress — indistinguishable
        in score from someone at exactly 80. The ceiling (110) must give
        real headroom above today's p90 so a genuine outlier still scores
        higher than a merely-active senator."""
        def bills_at_rate(n_per_congress: int):
            return [
                {"title": f"B{i}", "isLaw": False, "latestAction": "Introduced",
                 "billType": "s", "congress": 119}
                for i in range(n_per_congress)
            ]

        score_at_p90 = _calc_legislative_effectiveness(bills_at_rate(108), None)
        score_at_extreme_outlier = _calc_legislative_effectiveness(bills_at_rate(150), None)
        assert score_at_extreme_outlier > score_at_p90

    def test_house_uses_own_volume_ceiling(self):
        """A shared Senate-calibrated ceiling made the volume component
        structurally uncreditable for the House: House per-congress rates
        (p90 ~29) sit far below the Senate's (p90 ~108) because 435
        members split similar institutional bandwidth, not because
        they're less effective. Chamber is inferred from bill-type prefix
        so a House member at their own p90 gets meaningfully more volume
        credit than under the Senate ceiling, without a chamber flag
        needing to be threaded through every caller."""
        def house_bills_at_rate(n_per_congress: int):
            return [
                {"title": f"B{i}", "isLaw": False, "latestAction": "Introduced",
                 "billType": "hr", "congress": 119}
                for i in range(n_per_congress)
            ]

        # 29 bills/congress is House's own measured p90 — should score
        # meaningfully better than the same rate would under the Senate's
        # 110 ceiling (29/110 = 26% raw vs 29/35 = 83% raw).
        house_score = _calc_legislative_effectiveness(house_bills_at_rate(29), None)
        senate_score = _calc_legislative_effectiveness(
            [{"title": f"B{i}", "isLaw": False, "latestAction": "Introduced",
              "billType": "s", "congress": 119} for i in range(29)],
            None,
        )
        assert house_score > senate_score


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


class TestCalculateConfidence:
    """Confidence derives ONLY from data volume — identical rules for
    every member; who they are and what they scored play no part."""

    def test_empty_data_is_low_everywhere(self):
        from app.pipeline.analyze.score_calculator import calculate_confidence
        conf = calculate_confidence({})
        assert set(conf.values()) == {"low"}

    def test_rich_data_is_high_everywhere(self):
        from app.pipeline.analyze.score_calculator import calculate_confidence
        senator = {
            "funding": {
                "totalRaised": 5_000_000,
                "topDonors": [{"name": f"d{i}"} for i in range(12)],
                "industryBreakdown": [{"industry": f"i{i}"} for i in range(7)],
            },
            "votingRecord": {
                "keyVotes": [
                    {"votedWithParty": bool(i % 2)} for i in range(50)
                ],
                "recentVotes": [],
            },
            "campaignPromises": [
                {"alignment": "kept"} for _ in range(9)
            ],
            "sponsoredBills": [{"title": f"b{i}"} for i in range(15)],
        }
        conf = calculate_confidence(senator)
        assert set(conf.values()) == {"high"}

    def test_unlabeled_votes_do_not_count(self):
        """Votes without a party label carry no alignment signal."""
        from app.pipeline.analyze.score_calculator import calculate_confidence
        senator = {
            "votingRecord": {
                "keyVotes": [{"votedWithParty": None} for _ in range(100)],
                "recentVotes": [],
            },
        }
        assert calculate_confidence(senator)["independentVoting"] == "low"

    def test_unclear_promises_do_not_count(self):
        from app.pipeline.analyze.score_calculator import calculate_confidence
        senator = {"campaignPromises": [{"alignment": "unclear"} for _ in range(20)]}
        assert calculate_confidence(senator)["promisePersistence"] == "low"


# ── v5: majority-adjusted effectiveness + coalition breadth ──────

from app.pipeline.analyze.score_calculator import (
    _advancement_baseline,
)


class TestMajorityAdjustedAdvancement:
    def _bills(self, n, advanced, congress=118, bill_type="s"):
        out = []
        for i in range(n):
            out.append({
                "billId": f"S.{i}", "billType": bill_type, "congress": congress,
                "isLaw": False,
                "latestAction": "Passed Senate" if i < advanced else "Referred to committee",
                "title": f"Bill {i}",
            })
        return out

    def test_minority_not_penalized_for_status(self):
        """Equal-quality sponsors: each matching their status baseline scores alike.

        118th Senate majority is D (baseline 3.6%), minority R (2.4%).
        A D sponsor advancing at ~3.6% and an R sponsor at ~2.4% are both
        performing exactly at expectation and must land within a few
        points of each other — the old absolute 5% threshold gave the
        majority sponsor a structurally higher score for the same skill.
        """
        d_bills = self._bills(250, 9)   # 3.6%
        r_bills = self._bills(250, 6)   # 2.4%
        d = _calc_legislative_effectiveness(d_bills, leadership_score=0.5, party="D")
        r = _calc_legislative_effectiveness(r_bills, leadership_score=0.5, party="R")
        assert abs(d - r) <= 3, (d, r)

    def test_house_majority_baseline_higher(self):
        assert _advancement_baseline("hr", 118, "R") > _advancement_baseline("hr", 118, "D")
        assert _advancement_baseline("s", 118, "D") > _advancement_baseline("s", 118, "R")

    def test_unknown_congress_neutral_baseline(self):
        assert _advancement_baseline("s", 90, "D") == 0.030


class TestCoalitionBreadth:
    def _vr(self):
        return {
            "keyVotes": [
                {"votedWithParty": True, "partyAlignmentWeight": 1.0}
                for _ in range(30)
            ],
            "recentVotes": [],
        }

    def test_breadth_moves_score(self):
        base = dict(voting_record=self._vr(), lobbying_matches=[], funding={},
                    state="CA", party="D")
        low = _calc_constituent_alignment(**base, bipartisanship=0.0)
        mid = _calc_constituent_alignment(**base, bipartisanship=0.5)
        high = _calc_constituent_alignment(**base, bipartisanship=1.0)
        assert low < mid < high
        # 20% weight over a 0-100 component: full range moves CA by ~20
        assert 15 <= high - low <= 25

    def test_missing_breadth_is_not_neutral_scored(self):
        """Absent cosponsorship data must reproduce the pre-v5 score exactly."""
        base = dict(voting_record=self._vr(), lobbying_matches=[], funding={},
                    state="CA", party="D")
        assert _calc_constituent_alignment(**base) == _calc_constituent_alignment(
            **base, bipartisanship=None
        )
