"""Tests for the five representation sub-score calculations."""



import pytest

from app.models import Senator
from app.pipeline.analyze import score_calculator
from app.pipeline.analyze.score_bounds import clamp
from app.pipeline.analyze.score_calculator import (
    _advancement_baseline,
    _calc_constituent_alignment,
    _calc_funding_diversity,
    _calc_funding_independence,
    _calc_legislative_effectiveness,
    _calc_promise_persistence,
    _funding_independence_core,
    _legislative_effectiveness_core,
    _les_bill_stage,
    _les_component_score,
    _les_normalized_credit,
    _les_stage_counts,
    _les_significance_weight,
    calculate_scores,
    compute_les_reference,
    compute_overall_score,
)


class TestClamp:
    """The one clamp, shared by scoring, the validator and the president
    scorer (each used to carry its own copy)."""

    def test_is_shared(self):
        from app.pipeline.analyze import president_scorer
        from app.pipeline.assemble import validator

        assert score_calculator.clamp is clamp
        assert validator.clamp is clamp
        assert president_scorer.clamp is clamp

    @pytest.mark.parametrize("args, expected", [
        pytest.param((50.3,), 50, id="within_range_rounds"),
        pytest.param((-10.0,), 0, id="below_min"),
        pytest.param((150.0,), 100, id="above_max"),
        pytest.param((0.0,), 0, id="exact_lower_boundary"),
        pytest.param((100.0,), 100, id="exact_upper_boundary"),
        # Python's round(), not round-half-up.
        pytest.param((50.5,), 50, id="rounds_half_to_even_down"),
        pytest.param((51.5,), 52, id="rounds_half_to_even_up"),
        pytest.param((200.0, 0, 1000), 200, id="custom_range_within"),
        pytest.param((-5.0, 0, 1000), 0, id="custom_range_below"),
    ])
    def test_clamp(self, args, expected):
        assert clamp(*args) == expected


class TestFundingIndependence:
    """Higher score = less PAC dependency, more small donors, less
    top-donor concentration (v4: 50% PAC+outside / 25% small / 25% conc)."""

    def test_ideal_grassroots(self):
        # v6.5: industryBreakdown now feeds the folded-in source-breadth/
        # industry-concentration components too — 0% PAC (→100), 55% small
        # donors (→100), donor pool too small to measure top-donor
        # concentration (→ neutral 50), and the remaining 45% spread evenly
        # across 10 industries (→ high breadth, near-zero HHI → both ~80-100).
        funding = {
            "totalRaised": 1_000_000,
            "totalFromPACs": 0,
            "smallDonorPercentage": 55,
            "topDonors": [{"total": 100} for _ in range(10)],
            "industryBreakdown": [{"industry": f"IND{i}", "total": 45_000} for i in range(10)],
        }
        score = _calc_funding_independence(funding)
        assert score >= 85

    def test_fully_pac_funded_concentrated(self):
        # v6.5: all non-PAC money concentrated in a single industry, and a
        # top-heavy external donor pool (30 donors, top 10 hold the vast
        # majority) — worst case on all five folded-in components.
        funding = {
            "totalRaised": 1_000_000,
            "totalFromPACs": 1_000_000,
            "topDonors": [{"total": 40_000} for _ in range(10)] + [{"total": 1_000} for _ in range(20)],
            "industryBreakdown": [{"industry": "DEFENSE", "total": 1_000_000}],
        }
        assert _calc_funding_independence(funding) < 20

    def test_balanced_funding(self):
        # No district given -> Senate. The pinned test reference has no
        # size fit, so the share is scored against the Senate median
        # (15.7%): 30% is nearly twice it, PAC component ~4.5. 17% small
        # donors ~46; concentration pool below the $250K floor and no
        # industry data -> neutral 50 each:
        # FI = 20/53*4.5 + 10/53*46 + 10/53*50 + 13/53*50 ~= 32.
        funding = {
            "totalRaised": 1_000_000,
            "totalFromPACs": 300_000,
            "smallDonorPercentage": 17,
            "topDonors": [{"total": 20_000} for _ in range(10)],
        }
        score = _calc_funding_independence(funding)
        assert 15 <= score <= 35

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

    def test_pac_dependency_reads_the_share_not_the_campaign_size(self):
        """v6.26: the same share of a larger campaign is the same
        dependence. v6.22 scored a $100M campaign's 5% as ten times the
        typical share because PAC dollars barely grow with size, which
        turned a share into a dollar count."""
        small_campaign = {"totalRaised": 10_000_000, "totalFromPACs": 500_000,
                          "smallDonorPercentage": 15, "topDonors": []}
        mega_campaign = {"totalRaised": 100_000_000, "totalFromPACs": 5_000_000,
                         "smallDonorPercentage": 15, "topDonors": []}
        ref = {"senate": {"pac_ratio_median": 0.15, "pac_ratio_p10": 0.03, "pac_ratio_p90": 0.4}}
        assert (
            _calc_funding_independence(mega_campaign, reference=ref)
            == _calc_funding_independence(small_campaign, reference=ref)
        )

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

    def test_concentration_is_ranked_within_the_chamber(self, pinned_funding_reference):
        """v6.26: the top 10 donors' share of all outside contributions,
        ranked within the chamber (_rank_score): no big donors scores 100,
        the chamber's median member 50, all money from ten donors 0. The
        anchors used to be hand-typed (v6.13 measured them instead), then a
        median-and-spread scale that a skewed share capped near 65."""
        ref = pinned_funding_reference["senate"]
        median = ref["top10_share_median"]

        def _concentration_score(share: float) -> float:
            pool_total = 1_000_000
            top10_total = round(share * pool_total)
            # Enough smaller donors that none outgives the top ten.
            n_others = 10_000
            funding = {
                "totalRaised": pool_total,
                "totalFromPACs": 0,
                "topDonors": (
                    [{"total": top10_total / 10} for _ in range(10)]
                    + [{"total": (pool_total - top10_total) / n_others} for _ in range(n_others)]
                ),
            }
            return _funding_independence_core(funding)["components"][2]["score"]

        assert abs(_concentration_score(median) - 50.0) < 0.5
        assert _concentration_score(0.0011) > 95  # ten donors at 0.1% of the money
        assert _concentration_score(1.0) == 0.0
        assert _concentration_score(median / 2) > _concentration_score(median) > _concentration_score(median * 2)

    def test_small_state_not_penalized_for_identical_raw_percentage(self):
        """The core regression test: WY (population 0.6M, one of the
        smallest states) and CA (39.5M, the largest) with IDENTICAL raw
        small-donor % must NOT score identically — WY's modest raw %
        beats its low state baseline, CA's identical raw % falls further
        short of its much higher baseline. Pre-fix, both scored the exact
        same min(15/40,1)*100 = 37.5 regardless of state."""
        funding = {
            "totalRaised": 5_000_000,
            "totalFromPACs": 500_000,
            "smallDonorPercentage": 15,
            "topDonors": [],
        }
        wy_breakdown = _funding_independence_core(funding, state="WY")
        ca_breakdown = _funding_independence_core(funding, state="CA")
        wy_small = wy_breakdown["components"][1]["score"]
        ca_small = ca_breakdown["components"][1]["score"]
        assert wy_small > ca_small

    def test_unknown_state_falls_back_to_national_mean(self):
        """An unresolvable state code must never itself be a penalty or a
        windfall — falls back to the same national-mean baseline as no
        state at all."""
        funding = {
            "totalRaised": 5_000_000,
            "totalFromPACs": 500_000,
            "smallDonorPercentage": 15,
            "topDonors": [],
        }
        no_state = _funding_independence_core(funding)
        unknown_state = _funding_independence_core(funding, state="XX")
        assert no_state["components"][1]["score"] == unknown_state["components"][1]["score"]

    def test_district_bypasses_state_population_adjustment(self, pinned_funding_reference):
        """House members aren't scored against their state's population
        (districts are apportioned to equal population) — a ND House seat
        scores identically to the same raw % from any other state. Since
        v6.13 they're scored against the House's own median small-donor
        share (median member = 50) instead of a flat 40% cap."""
        house = pinned_funding_reference["house"]
        funding = {
            "totalRaised": 2_000_000,
            "totalFromPACs": 400_000,
            "smallDonorPercentage": 15,
            "topDonors": [],
        }
        nd_house = _funding_independence_core(funding, state="ND", district=1)
        ca_house = _funding_independence_core(funding, state="CA", district=12)
        assert nd_house["components"][1]["score"] == ca_house["components"][1]["score"]
        at_median = {**funding, "smallDonorPercentage": house["small_donor_median"]}
        assert _funding_independence_core(at_median, state="ND", district=1)["components"][1]["score"] == 50.0

    def test_at_state_baseline_scores_neutral(self):
        """A senator whose raw % exactly matches their state's expected
        baseline lands at neutral 50 on the small-donor component."""
        # ND's fitted baseline is ~11.1%.
        funding = {
            "totalRaised": 5_000_000,
            "totalFromPACs": 500_000,
            "smallDonorPercentage": 11,
            "topDonors": [],
        }
        breakdown = _funding_independence_core(funding, state="ND")
        small_score = breakdown["components"][1]["score"]
        assert 45 <= small_score <= 55


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

    def test_unclassified_treated_neutrally_not_as_least_diverse(self):
        """UNCLASSIFIED is a pure residual (total_raised minus everything we
        could attribute — committee transfers, missing employer data, etc.),
        not donors the classifier examined and rejected. A live 2026-07
        audit found a 32% median UNCLASSIFIED share driving a strong
        negative correlation with this score (r=-0.66) purely from missing
        attribution, contradicting the "missing data defaults to neutral"
        principle used everywhere else. It must score better than a
        same-sized bucket of OTHER/POLITICAL money, which at least reflects
        a real (failed) classification attempt."""
        base = {"totalRaised": 1_000_000, "smallDonorPercentage": 0}
        unclassified_funding = {
            **base,
            "industryBreakdown": [{"industry": "UNCLASSIFIED", "total": 1_000_000, "percentage": 100}],
        }
        other_funding = {
            **base,
            "industryBreakdown": [{"industry": "OTHER", "total": 1_000_000, "percentage": 100}],
        }
        unclassified_score = _calc_funding_diversity(unclassified_funding)
        other_score = _calc_funding_diversity(other_funding)
        assert unclassified_score > other_score

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

    def test_overwhelming_small_dollar_not_capped_at_flat_neutral(self):
        """A senator whose funding is almost entirely small-dollar (a real
        2026-07 profile: 63% small-donor / 0.28% classified-industry
        money) previously scored exactly 69 — the population-wide
        maximum for this dimension — because the concentration signal's
        fallback was a flat 65 for ANY small_frac > 0.3, whether just over
        the threshold or, as here, close to total reliance. Two profiles
        that differ sharply in how grassroots-dominated they are must not
        collapse to the same score."""
        just_over_threshold = _calc_funding_diversity({
            "totalRaised": 1_000_000,
            "smallDonorPercentage": 31,
            "industryBreakdown": [{"industry": "OTHER", "total": 10_000, "percentage": 1}],
        })
        overwhelmingly_small_dollar = _calc_funding_diversity({
            "totalRaised": 1_000_000,
            "smallDonorPercentage": 90,
            "industryBreakdown": [{"industry": "OTHER", "total": 10_000, "percentage": 1}],
        })
        assert overwhelmingly_small_dollar > just_over_threshold + 15


class TestPromisePersistence:
    """Higher score = more kept promises + floor advocacy boost."""

    def test_all_kept(self):
        # Beta-Binomial posterior (Morris 1983, PRIOR_PSEUDOCOUNT=3):
        # 3 kept → posterior = (3+1.5)/(3+3)*100 = 75, blended ≈ 77.5.
        # Range covers both the pre- and post-2026-07 pseudocount regime —
        # sparse samples (n=3) stay closer to the neutral prior of 50.
        promises = [{"alignment": "kept"}, {"alignment": "kept"}, {"alignment": "kept"}]
        score = _calc_promise_persistence({}, "D", promises)
        assert 58 <= score <= 85

    def test_all_broken(self):
        # Beta-Binomial posterior (Morris 1983, PRIOR_PSEUDOCOUNT=3):
        # 2 broken → posterior = (0+1.5)/(2+3)*100 = 30, blended ≈ 37.
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

    def test_population_retains_spread_at_real_evaluable_count(self):
        """2026-07-13 finding: the ~2-3avg the v5.1/v5.3 recalibration
        assumed didn't hold up in production — the real figure is ~0.5
        evaluable promises/senator (59/100 have zero), a genuine data-
        scarcity floor, not a threshold bug (see the ceremonial-resolution
        fix in cross_reference.py for the one real bug found in this
        pass). PRIOR_PSEUDOCOUNT resized 6->3 so that even at this much
        harsher sample size — most members with 0-1 evaluable promises,
        a few with 2 — the population doesn't collapse to a single
        indistinguishable near-50 band."""
        profiles = (
            [[]] * 10  # zero evaluable — ties at exactly 50, the honest floor
            + [[{"alignment": "broken"}]] * 3
            + [[{"alignment": "kept"}]] * 3
            + [[{"alignment": "kept"}, {"alignment": "kept"}]] * 2
            + [[{"alignment": "broken"}, {"alignment": "broken"}]] * 2
        )
        scores = [_calc_promise_persistence({}, "D", p) for p in profiles]
        assert max(scores) - min(scores) >= 15


class TestLegislativeEffectiveness:
    """Higher score = more bills passed, higher leadership, more active sponsorship."""

    def test_no_bills_with_leadership(self):
        """Leadership alone should shift score above 50, at full tenure
        confidence (leadership is itself tenure-shrunk toward neutral for
        freshmen/unknown tenure — see test_leadership_shrunk_toward_
        neutral_for_freshmen — so years_in_office must be passed here for
        the leadership signal to show through at all)."""
        score = _calc_legislative_effectiveness([], 0.8, years_in_office=6)
        assert score > 50

    def test_confirmed_zero_scores_at_or_below_a_real_low_n_attempt(self):
        """2026-07 fix: politicians were being rewarded for not trying to
        advance bills, as opposed to trying and failing. A senator with
        real tenure and zero sponsored bills must not outscore a senator
        who sponsored one substantive bill that didn't advance — inaction
        must never beat a genuine (if unsuccessful) attempt."""
        one_bill_zero_advanced = [
            {"title": "B1", "isLaw": False, "latestAction": "Introduced",
             "billType": "s", "congress": 119},
        ]
        score_tried_and_failed = _calc_legislative_effectiveness(
            one_bill_zero_advanced, None, years_in_office=2, chamber="senate",
        )
        score_confirmed_zero = _calc_legislative_effectiveness(
            [], None, years_in_office=2, chamber="senate",
        )
        assert score_confirmed_zero <= score_tried_and_failed

    def test_more_output_never_scores_lower(self):
        """Credit is monotone in bills sponsored and stages reached, from
        zero bills up — the property the removed bill-count shrinkage used
        to be needed for."""
        def bills(n, law=0):
            return [{"title": f"B{i}", "isLaw": i < law, "latestAction": "Introduced",
                     "billType": "s", "congress": 119} for i in range(n)]
        records = [bills(0), bills(1), bills(3), bills(3, law=1), bills(10, law=1), bills(10, law=3)]
        scores = [_calc_legislative_effectiveness(b, None, party="R", years_in_office=4, chamber="senate")
                  for b in records]
        assert scores == sorted(scores)

    def test_unfetched_bills_are_neutral_not_zero(self):
        """A failed sponsored-legislation request is not a record of zero
        bills: the V&W component stays neutral instead of scoring a
        credit of 0."""
        score, detail = _les_component_score([], "R", 6.0, None, chamber="senate", bills_known=False)
        assert score == 50.0 and "could not be fetched" in detail
        zero, _ = _les_component_score([], "R", 6.0, None, chamber="senate")
        assert zero < 50.0

    def test_freshman_zero_bills_stays_neutral(self):
        """Below the tenure floor, zero bills is indistinguishable from no
        data yet — a freshman must still score a flat neutral 50, not be
        penalized for not having had a real chance to sponsor bills."""
        score = _calc_legislative_effectiveness([], None, years_in_office=0.1)
        assert score == 50

    def test_unknown_tenure_zero_bills_stays_neutral(self):
        """No years_in_office info at all (None) must behave exactly like
        today's default — confirmed-zero shrinkage only applies when
        tenure is actually known and meets the floor."""
        score = _calc_legislative_effectiveness([], None)
        assert score == 50

    def test_confirmed_zero_with_leadership_blends_correctly(self):
        """A confirmed-zero senator with real cosponsorship leadership
        should still get credit for that leadership — the zero-bills
        penalty applies only to the two bill-based components."""
        score_with_leadership = _calc_legislative_effectiveness(
            [], 0.9, years_in_office=5,
        )
        score_without_leadership = _calc_legislative_effectiveness(
            [], None, years_in_office=5,
        )
        assert score_with_leadership > score_without_leadership

    def test_low_bill_count_is_not_shrunk_toward_neutral(self):
        """v6.14: no shrinkage by bill count. A focused sponsor with two
        bills, one enacted, outscores a member with twenty bills that
        never advanced — V&W's ordering, which the old min(bills/10, 1)
        shrink reversed by pulling the two-bill record toward 50."""
        focused = [
            {"title": "A", "isLaw": True, "latestAction": "Became Public Law", "billType": "s", "congress": 119},
            {"title": "B", "isLaw": False, "latestAction": "Introduced", "billType": "s", "congress": 119},
        ]
        prolific = [
            {"title": f"B{i}", "isLaw": False, "latestAction": "Introduced", "billType": "s", "congress": 119}
            for i in range(20)
        ]
        assert (_les_component_score(focused, "R", 4.0)[0]
                > _les_component_score(prolific, "R", 4.0)[0])

    def test_prolific_but_no_passage(self):
        """Many bills introduced but none advanced past stage 1 still
        earns real (if modest) credit under the cumulative-stage sum —
        raw volume counts for something in V&W's real methodology, unlike
        the old percentage-based advancement rate which this replaced.
        Lower bound trimmed from 45 to 40 (2026-07-21): party=None here
        maps to _advancement_baseline's flat unknowable-status rate
        (0.030), which sits almost exactly at the Senate's own real
        average (the Senate reference's avg_baseline, 0.0305) — so
        this scenario is now correctly compared against close to the full
        Senate population-average bar rather than the old pooled
        cross-chamber constant's easier one."""
        bills = [
            {"title": f"Bill {i}", "isLaw": False, "latestAction": "Introduced",
             "billType": "s", "congress": 119}
            for i in range(50)
        ]
        score = _calc_legislative_effectiveness(bills, None)
        assert 40 <= score <= 75

    def test_high_passage_rate(self):
        """Bills that became law contribute credit at every earlier stage
        too (V&W's real cumulative design), so swapping some introduced-
        only bills for ones that became law must raise the score at the
        same total bill count — a relative comparison, not an absolute
        threshold, since the absolute score also depends on the
        population-average baseline this component is compared against.
        Bill count raised from 13 to 50 (2026-07-21): the chamber-specific
        _LES_AVG_BASELINE_SENATE fix correctly raised the real Senate bar,
        and at only 13 bills both variants saturated at the same score
        floor (neither could show credit for the 2 became-law bills) —
        50 bills gives both scenarios room above the floor to actually
        differ."""
        n = 50
        all_introduced = [
            {"title": f"Bill {i}", "isLaw": False, "latestAction": "Introduced",
             "billType": "s", "congress": 119}
            for i in range(n)
        ]
        two_became_law = [
            {"title": "Good Bill", "isLaw": True, "latestAction": "Became public law",
             "billType": "s", "congress": 119},
            {"title": "Also Good", "isLaw": True, "latestAction": "Became public law",
             "billType": "s", "congress": 119},
        ] + all_introduced[:n - 2]
        score_plain = _calc_legislative_effectiveness(all_introduced, 0.5)
        score_with_laws = _calc_legislative_effectiveness(two_became_law, 0.5)
        assert score_with_laws > score_plain

    def test_leadership_matters(self):
        """Higher PageRank leadership should produce higher score — at full
        tenure confidence (6+ years), where the raw percentile counts in
        full rather than being shrunk toward neutral."""
        bills = [{"title": f"B{i}", "isLaw": False, "latestAction": "Introduced"} for i in range(20)]
        score_low = _calc_legislative_effectiveness(bills, 0.1, years_in_office=6)
        score_high = _calc_legislative_effectiveness(bills, 0.9, years_in_office=6)
        assert score_high > score_low

    def test_leadership_shrunk_toward_neutral_for_freshmen(self):
        """A freshman's raw PageRank percentile is near-zero not because
        they're ineffective but because they haven't had years to build a
        cosponsorship network — the same senator's leadership component
        must sit close to neutral 50 as a freshman, and only reflect the
        full raw percentile once they've had a real term's worth of time
        (2026-07 fix: this was previously a flat, unshrunk percentile,
        producing a real tenure-vs-LE correlation of r=+0.24 and a 24.6
        point mean gap between freshmen and veterans)."""
        bills = [{"title": f"B{i}", "isLaw": False, "latestAction": "Introduced"} for i in range(20)]
        score_freshman = _calc_legislative_effectiveness(bills, 0.9, years_in_office=1)
        score_veteran = _calc_legislative_effectiveness(bills, 0.9, years_in_office=6)
        assert score_freshman < score_veteran

    def test_missing_leadership_data_is_neutral_not_punitive(self):
        """Missing leadership data (None) must default to the same neutral
        50 raw value that an explicit 0.5 PageRank score would produce —
        never a below-50 punitive value like the old flat 40 default."""
        bills = [{"title": f"B{i}", "isLaw": False, "latestAction": "Introduced"} for i in range(20)]
        score_missing = _calc_legislative_effectiveness(bills, None, years_in_office=6)
        score_neutral_raw = _calc_legislative_effectiveness(bills, 0.5, years_in_office=6)
        assert score_missing == score_neutral_raw

    def test_les_bill_stage_from_keywords(self):
        """Direct unit test of the stage-inference fallback used when a
        bill's real `stage` classification is unset (the common case
        today — stage is a brand-new field with no historical backfill
        yet). Committee/chamber milestones map to their real V&W stage;
        calendar placement doesn't (Senate Rule XIV places bills on the
        calendar without committee action, so "Placed on calendar"
        signals nothing about advancement) — tested directly rather than
        through the full score pipeline, which heavily dampens small
        stage differences at low bill counts relative to the population-
        average baseline it's compared against."""
        s = "s"
        assert _les_bill_stage({"latestAction": "Introduced", "billType": s}) == 1
        assert _les_bill_stage({"latestAction": "Placed on calendar", "billType": s}) == 1
        assert _les_bill_stage({"latestAction": "Ordered to be reported", "billType": s}) == 2
        assert _les_bill_stage({"latestAction": "Passed Senate", "billType": s}) == 4
        assert _les_bill_stage({"latestAction": "Agreed to", "billType": s}) == 4
        # A chamber declining the bill is not passage.
        assert _les_bill_stage({"latestAction": "Motion to proceed to consideration of measure not agreed to in Senate", "billType": s}) == 1
        assert _les_bill_stage({"latestAction": "On motion to suspend the rules and pass the bill Failed by the Yeas and Nays", "billType": s}) == 1
        assert _les_bill_stage({"latestAction": "Anything", "billType": s, "isLaw": True}) == 5
        # Real `stage` classification always wins over the text fallback.
        assert _les_bill_stage({"stage": "IN_COMMITTEE", "latestAction": "Introduced"}) == 2
        assert _les_bill_stage({"stage": "REPORTED", "latestAction": "Introduced"}) == 3
        assert _les_bill_stage({"stage": "ENACTED", "latestAction": "Introduced"}) == 5

    def test_les_bill_stage_covers_every_stage_string(self):
        """Every BillStage the max-over-history classifier can now store
        must map to a rank — including IN_OTHER_CHAMBER / TO_PRESIDENT /
        VETOED, which the 2026-07 max-over-history change made reachable
        for the first time. A typo or reorder in _LES_STAGE_ORDER that
        pushed, say, VETOED below committee would silently change cumulative
        credit for every vetoed/cross-chamber bill, and no prior test
        exercised these three. The text fallback is disabled here (a real
        `stage` is always present) so this pins the table, not the keywords."""
        from app.config_definitions import BillStage

        expected = {
            BillStage.INTRODUCED.value: 1,
            BillStage.REFERRED.value: 1,
            BillStage.IN_COMMITTEE.value: 2,
            BillStage.REPORTED.value: 3,
            BillStage.ON_FLOOR.value: 3,
            BillStage.PASSED_CHAMBER.value: 4,
            BillStage.IN_OTHER_CHAMBER.value: 4,
            BillStage.TO_PRESIDENT.value: 4,
            BillStage.ENACTED.value: 5,
            BillStage.VETOED.value: 4,
        }
        # Guard against a stage being added to the enum without a rank here.
        assert {s.value for s in BillStage} == set(expected)
        for stage_value, rank in expected.items():
            assert _les_bill_stage({"stage": stage_value}) == rank, stage_value
        # Cumulative credit tracks rank x significance weight, so a rank
        # regression on any of these flows straight through.
        assert _les_bill_stage({"stage": BillStage.ENACTED.value}) > \
            _les_bill_stage({"stage": BillStage.IN_COMMITTEE.value})

    def test_les_significance_weight(self):
        """Commemorative resolutions weight 1x, substantive bills 5x —
        V&W's real 2-tier split this platform implements (their 3rd tier,
        "substantive and significant," is not implemented — see the
        module comment above _LES_STAGE_ORDER)."""
        assert _les_significance_weight("sres") == 1.0
        assert _les_significance_weight("hres") == 1.0
        assert _les_significance_weight("s") == 5.0
        assert _les_significance_weight("hr") == 5.0

    def test_stage_counts_are_cumulative_and_significance_weighted(self):
        """A bill counts, by its significance weight, at every stage it
        reaches — a law at all five — as in V&W's LES."""
        introduced = {"latestAction": "Introduced", "billType": "s"}
        became_law = {"latestAction": "Introduced", "billType": "s", "isLaw": True}
        commemorative_law = {"latestAction": "Introduced", "billType": "sres", "isLaw": True}
        assert _les_stage_counts([introduced]) == [5.0, 0.0, 0.0, 0.0, 0.0]
        assert _les_stage_counts([became_law]) == [5.0] * 5
        assert _les_stage_counts([commemorative_law]) == [1.0] * 5

    def test_advancing_a_bill_is_worth_far_more_than_introducing_one(self):
        """V&W divide each stage's count by the chamber's total there, so a
        law — one of few — outweighs many introductions. Under v6.13's
        weight x stages it was worth exactly four."""
        totals = [51325.0, 7055.0, 6155.0, 3250.0, 870.0]  # 118th House, weighted
        one_law = _les_normalized_credit([5.0] * 5, totals, 448)
        one_intro = _les_normalized_credit([5.0, 0.0, 0.0, 0.0, 0.0], totals, 448)
        assert one_law / one_intro > 40

    def test_a_chamber_averages_one(self):
        members = [[5.0 * n, n, n / 2, n / 4] for n in range(1, 41)]
        totals = [sum(m[k] for m in members) for k in range(4)]
        credits = [_les_normalized_credit(m, totals, len(members)) for m in members]
        assert abs(sum(credits) / len(credits) - 1.0) < 1e-9

    def test_resolutions_excluded_from_volume(self):
        """The original v5.9 "Mushroom Day" bug let a commemorative-only
        record (SRES, agreed to without debate by unanimous consent)
        inflate this score even though it required no real legislative
        effort. Carried forward into the 2026-07 V&W-based rewrite: the
        confidence gate is keyed on *substantive* bill count (n_sub), so
        a commemorative-only record still routes through the same
        confirmed-zero-or-neutral path a truly empty record does —
        sponsoring only ceremonial resolutions must score identically to
        sponsoring nothing at all."""
        mushroom_day = {
            "title": "A resolution recognizing and honoring National Mushroom "
                     "Day and the contributions of Chester and Berks Counties "
                     "to the national mushroom industry and to healthy diets.",
            "isLaw": False,
            "latestAction": "Resolution agreed to in Senate without amendment "
                             "and with a preamble by Unanimous Consent.",
            "billType": "sres",
            "congress": 119,
        }
        score_no_bills = _calc_legislative_effectiveness([], 0.5)
        score_only_resolutions = _calc_legislative_effectiveness(
            [mushroom_day] * 20, 0.5,
        )
        assert score_only_resolutions == score_no_bills

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

    def test_credit_increases_with_bill_count_until_saturation(self):
        """More bills (same stage/significance) means more cumulative
        credit, so the score should rise with bill count — but the
        expected-vs-actual gap saturates at 1.5 population stdevs (same
        "never a runaway score from one outlier" shape as every other
        saturation constant in this file), so two counts that are BOTH
        already past saturation score identically, same as e.g.
        Constituent Alignment's surplus credit saturating past a point."""
        def bills_at_rate(n_per_congress: int):
            return [
                {"title": f"B{i}", "isLaw": False, "latestAction": "Introduced",
                 "billType": "s", "congress": 119}
                for i in range(n_per_congress)
            ]

        score_low = _calc_legislative_effectiveness(bills_at_rate(15), None)
        score_mid = _calc_legislative_effectiveness(bills_at_rate(40), None)
        assert score_mid > score_low

    def test_house_uses_own_population_baseline(self):
        """A shared Senate-calibrated expected baseline would make this
        component structurally uncreditable for the House: House
        per-congress bill totals sit far below the Senate's because 435
        members split similar institutional bandwidth, not because
        they're less effective (2026-07-23 post-reclassification audit:
        Senate population-median significance-weighted credit is 289/congress
        vs House's 129).
        Chamber is inferred from bill-type prefix, same pattern the old
        volume-ceiling component used, so a House member at the same
        RAW bill count as a senator is compared against the House's own,
        much lower, real norm — and should score meaningfully better for
        it, not worse."""
        def house_bills_at_rate(n_per_congress: int):
            return [
                {"title": f"B{i}", "isLaw": False, "latestAction": "Introduced",
                 "billType": "hr", "congress": 119}
                for i in range(n_per_congress)
            ]

        house_score = _calc_legislative_effectiveness(house_bills_at_rate(36), None)
        senate_score = _calc_legislative_effectiveness(
            [{"title": f"B{i}", "isLaw": False, "latestAction": "Introduced",
              "billType": "s", "congress": 119} for i in range(36)],
            None,
        )
        assert house_score > senate_score

    def test_advancement_baseline_is_chamber_specific(self, pinned_les_reference):
        """2026-07-21 fix: a single pooled average baseline compared every
        member's own majority/minority advancement rate against ONE
        cross-chamber average, even though the two chambers' real rates
        genuinely differ (live audit: House mean ~0.044, Senate mean
        ~0.031). That silently inflated House members' expected-credit bar
        and deflated the Senate's (live population: House 61% below neutral
        vs Senate 38% before the fix). Guards that a House member is
        measured against the HOUSE reference only: changing the Senate
        entry must not move a House member's score, and vice versa."""
        import copy

        house_bills = [
            {"title": f"B{i}", "isLaw": False, "latestAction": "Introduced",
             "billType": "hr", "congress": 119}
            for i in range(30)
        ]
        ref = copy.deepcopy(pinned_les_reference)
        base = _calc_legislative_effectiveness(house_bills, None, les_reference=ref)
        ref["senate"]["avg_baseline"] *= 2
        ref["senate"]["median_credit"] *= 2
        assert _calc_legislative_effectiveness(house_bills, None, les_reference=ref) == base
        ref["house"]["median_credit"] *= 2
        assert _calc_legislative_effectiveness(house_bills, None, les_reference=ref) < base

    def test_median_member_scores_near_neutral(self, pinned_les_reference):
        """v6.10 (2026-07-23): the V&W component's reference point is each
        chamber's population MEDIAN, not its mean. The per-congress credit
        distribution is right-skewed (a minority of highly prolific sponsors
        pull the mean above the typical member), so scoring against the mean
        put slightly more than half of EVERY chamber below neutral by
        construction — the residual imbalance v6.9 flagged and left open.
        Centering on the median makes a member whose per-congress credit
        sits at the chamber median score ~50 instead of below it.

        Senate case: a member with credit ≈ the Senate median (289) and
        party=None (which maps _advancement_baseline to 0.030, ≈ the Senate
        average 0.0305, so status_ratio ≈ 1 and doesn't confound the
        reference-point comparison) must land at neutral, not below it.
        Under a mean reference this same member would score below neutral."""
        # Introduced-only substantive "s" bills each earn weight(5)*stage(1)
        # = 5 cumulative credit; ~58 of them in one congress ≈ the Senate
        # median of 289 per-congress credit.
        n = round(pinned_les_reference["senate"]["median_credit"] / 5)
        median_credit_bills = [
            {"title": f"B{i}", "isLaw": False, "latestAction": "Introduced",
             "billType": "s", "congress": 119}
            for i in range(n)
        ]
        score = _calc_legislative_effectiveness(median_credit_bills, None)
        # The whole point of the mean->median switch: the typical member is
        # neutral, not below it. A comfortable band around 50 keeps this from
        # being brittle to small recalibrations while still failing loudly if
        # the reference point ever regresses back to the (higher) mean, which
        # would drag this member back down into the low-40s.
        assert 46 <= score <= 56

    def test_population_reference_is_median_not_mean(self):
        """Guard the mean->median switch itself (v6.10): the reference point
        is the chamber's MEDIAN per-congress credit, which for a right-skewed
        population sits strictly below the mean. A reference computed from
        the mean would silently re-open the below-neutral imbalance v6.10
        closed. Since v6.13 the reference is measured every pipeline run
        (compute_les_reference), so this pins the computation, not a
        constant."""
        def member(n_bills):
            return ([
                {"title": f"B{i}", "isLaw": False, "latestAction": "Introduced",
                 "billType": "s", "congress": 119}
                for i in range(n_bills)
            ], "D")
        # 40 typical members at 10 bills, 5 prolific ones at 200.
        members = [member(10) for _ in range(40)] + [member(200) for _ in range(5)]
        ref = compute_les_reference(members, congress=119, majority="R")
        # 10 introduced bills (weight 5) of 7,000 weighted introductions,
        # x N/5 = 45/5.
        assert ref["median_credit"] == round(50 / 7000 * 45 / 5, 4)
        assert ref["median_credit"] < ref["mean_credit"]
        assert ref["n"] == 45


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
        assert "constituentAlignment" in scores
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
        status = conf.pop("constituentAlignmentVotePart")
        assert set(conf.values()) == {"low"}
        assert status == "neutral:few-votes"

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
        # The vote-part status rides along; this member has no party, so no
        # party norm to score the vote part against.
        assert conf.pop("constituentAlignmentVotePart") == "neutral:no-expectation"
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
        assert calculate_confidence(senator)["constituentAlignment"] == "low"

    def test_unclear_promises_do_not_count(self):
        from app.pipeline.analyze.score_calculator import calculate_confidence
        senator = {"campaignPromises": [{"alignment": "unclear"} for _ in range(20)]}
        assert calculate_confidence(senator)["promisePersistence"] == "low"


# ── v5: majority-adjusted effectiveness + coalition breadth ──────


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

    def test_house_commemorative_bill_types_use_house_baseline(self):
        """hres/hconres are House bill types too — _les_component_score
        averages _advancement_baseline over a member's FULL sponsored-bill
        list (not substantive-filtered), so a commemorative resolution
        misclassified as a Senate bill would silently pull a House
        majority sponsor's baseline down toward the Senate rate."""
        assert _advancement_baseline("hres", 118, "R") == _advancement_baseline("hr", 118, "R")
        assert _advancement_baseline("hconres", 118, "R") == _advancement_baseline("hr", 118, "R")
        assert _advancement_baseline("hres", 118, "R") != _advancement_baseline("s", 118, "R")


class TestBipartisanCoalitionAttraction:
    """v6.11: coalition breadth moved OUT of Constituent Alignment into
    Legislative Effectiveness as "Bipartisan coalition attraction"
    (Harbridge-Yong, Volden & Wiseman 2023 — attracting cross-party
    cosponsors to one's own bills predicts lawmaking success; it is not a
    constituent-alignment construct, Harbridge & Malhotra 2011)."""

    def _bills(self, n=250, advanced=9):
        return [
            {
                "billId": f"S.{i}", "billType": "s", "congress": 118,
                "isLaw": False,
                "latestAction": "Passed Senate" if i < advanced else "Referred to committee",
                "title": f"Bill {i}",
            }
            for i in range(n)
        ]

    def test_attraction_moves_le_score(self):
        base = dict(sponsored_bills=self._bills(), leadership_score=0.5,
                    party="D", years_in_office=10.0)
        low = _calc_legislative_effectiveness(**base, attracted_bipartisanship=0.0)
        mid = _calc_legislative_effectiveness(**base, attracted_bipartisanship=0.5)
        high = _calc_legislative_effectiveness(**base, attracted_bipartisanship=1.0)
        assert low < mid < high
        # 15% weight over a 0-100 component: full range moves the score by ~15.
        # Unlike the old Constituent Alignment breadth component there is no
        # seat-safety discount: the LE signature takes no seat/state input at
        # all (HVW 2023 find the effect for both majority and minority members).
        assert 12 <= high - low <= 18

    def test_missing_attraction_reverts_to_pre_v6_11_weights(self):
        """Absent cosponsorship data must reproduce the pre-v6.11 70/30
        LES/leadership split exactly — never scored neutral."""
        base = dict(sponsored_bills=self._bills(), leadership_score=0.5,
                    party="D", years_in_office=10.0)
        assert _calc_legislative_effectiveness(**base) == _calc_legislative_effectiveness(
            **base, attracted_bipartisanship=None
        )

    def test_median_attractor_scores_component_neutral(self):
        """Cohort-median attraction (0.5 on the normalized scale) maps to a
        50 component — swapping it in for missing data must not move a
        member whose other components also sit at 50."""
        core = _legislative_effectiveness_core(
            [], leadership_score=None, party="D", years_in_office=None,
            attracted_bipartisanship=0.5,
        )
        assert core["score"] == 50
        labels = [c["label"] for c in core["components"]]
        assert "Bipartisan coalition attraction" in labels

    def test_breadth_no_longer_a_constituent_alignment_input(self):
        """The old bipartisanship parameter is gone from Constituent
        Alignment entirely — passing it must fail loudly, not be silently
        accepted."""
        base = dict(
            voting_record={"keyVotes": [], "recentVotes": []},
            lobbying_matches=[], funding={}, state="CA", party="D",
        )
        with pytest.raises(TypeError):
            _calc_constituent_alignment(**base, bipartisanship=0.5)


class TestComputeOverallScoreOnPartialColumnRows:
    """compute_overall_score is called against two different SQLAlchemy
    shapes in production: full ORM objects (e.g. senate_pipeline.py's
    ScoreSnapshot recorder) and Row objects from a partial-column
    db.query(Senator.col1, Senator.col2, ...) select (e.g. app/api/action.py
    and action_center.py's _find_related_senators, both consolidated onto
    this function in the promisePersistence-removal pass). No prior test
    covered the Row-object shape specifically."""

    def test_matches_on_a_partial_column_query_row(self, db_session):
        db_session.add(Senator(
            id="S001", name="Test", state="CA", party="D",
            score_funding_independence=60, score_promise_persistence=999,
            score_constituent_alignment=70, score_funding_diversity=65,
            score_legislative_effectiveness=82,
        ))
        db_session.commit()

        row = db_session.query(
            Senator.id, Senator.name, Senator.state, Senator.party,
            Senator.score_funding_independence, Senator.score_promise_persistence,
            Senator.score_constituent_alignment, Senator.score_funding_diversity,
            Senator.score_legislative_effectiveness,
        ).first()

        full = db_session.query(Senator).filter(Senator.id == "S001").first()

        assert compute_overall_score(row) == compute_overall_score(full)


class TestComputeOverallScoreOnDict:
    """compute_overall_score also accepts a plain representationScore-shaped
    dict (camelCase keys matching SCORE_WEIGHTS directly) — api/public.py's
    serialized API responses, consolidated onto this function rather than
    keeping a second, independent weighted-sum implementation there."""

    def test_dict_matches_equivalent_orm_object(self, db_session):
        db_session.add(Senator(
            id="S002", name="Test2", state="TX", party="R",
            score_funding_independence=60, score_promise_persistence=999,
            score_constituent_alignment=70, score_funding_diversity=65,
            score_legislative_effectiveness=82,
        ))
        db_session.commit()
        full = db_session.query(Senator).filter(Senator.id == "S002").first()

        as_dict = {
            "fundingIndependence": 60,
            "promisePersistence": 999,
            "constituentAlignment": 70,
            "fundingDiversity": 65,
            "legislativeEffectiveness": 82,
        }
        assert compute_overall_score(as_dict) == compute_overall_score(full)

    def test_missing_keys_default_to_zero(self):
        assert compute_overall_score({}) == 0.0


class TestStatePviData:
    """Guards the generated state PVI data (app/data/state_pvi.json) and the
    generator's compute logic. STATE_PVI used to be a hand-typed inline dict;
    it is now COMPUTED from presidential returns by scripts/fetch_state_pvi.py
    and read via _state_pvi(). These tests lock in both the shipped data's
    sanity and the formula, so a bad regeneration (swapped D/R column, wrong
    baseline, sign flip) fails here instead of silently skewing every
    senator's seat expectation."""

    # Hand-computed anchors for the 2020+2024 window (the same window
    # Cook's current 2025 PVIs use — the 2026-07 regeneration moved the
    # shipped data off the old 2016+2020 window): mean deviation of the
    # state's two-party D share from the national two-party D share
    # (52.27% in 2020, 49.25% in 2024), from official statewide returns —
    # worked by hand from the published vote totals, independent of the
    # generator script. Shipped values must land within +/-1 (Cook's own
    # published numbers may differ by a point via their undisclosed
    # recency weighting, which we deliberately don't replicate).
    COOK_ANCHORS = {
        "WY": 24, "WV": 21, "MA": -14, "CA": -12, "MI": 0, "PA": 1,
        "GA": 1, "TX": 6, "DC": -43,
    }

    def test_shipped_json_is_sane(self):
        pvi = score_calculator._state_pvi()
        assert len(pvi) == 51  # 50 states + DC
        assert all(-50 <= v <= 50 for v in pvi.values())
        r_lean = sum(1 for v in pvi.values() if v > 0)
        d_lean = sum(1 for v in pvi.values() if v < 0)
        assert 18 <= r_lean <= 32 and 18 <= d_lean <= 32

    def test_shipped_json_matches_cook_within_one(self):
        pvi = score_calculator._state_pvi()
        for st, expected in self.COOK_ANCHORS.items():
            assert st in pvi, f"{st} missing from state_pvi.json"
            assert abs(pvi[st] - expected) <= 1, (
                f"{st}: shipped {pvi[st]:+d} vs Cook {expected:+d} (>1 off)"
            )

    def test_generator_compute_pvi_formula(self):
        """The generator's compute_pvi implements Cook's formula: a state
        that ran exactly at the national two-party split is EVEN; running
        more Republican than the nation yields a positive (R) PVI."""
        import importlib.util
        import pathlib

        script = (
            pathlib.Path(__file__).resolve().parent.parent
            / "scripts" / "fetch_state_pvi.py"
        )
        spec = importlib.util.spec_from_file_location("fetch_state_pvi", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # compute_pvi derives the national two-party split by summing the
        # states it is given (no separate "national" key since the 2026-07
        # rewrite), so ZZ is a huge 50/50 anchor state pinning the national
        # D share to ~50%. Keyed by the generator's own CYCLES (2020+2024
        # window) so a future window bump fails loudly here.
        counts = {
            cycle: {"ZZ": {"D": 1_000_000, "R": 1_000_000},
                    "XX": {"D": 50, "R": 50},    # exactly national -> EVEN
                    "YY": {"D": 40, "R": 60}}    # 10pts more R -> R+10
            for cycle in mod.CYCLES
        }
        out = mod.compute_pvi(counts)
        assert out["XX"] == 0
        assert out["YY"] == 10  # positive = R lean


class TestLeadershipZeroIsAScore:
    """compute_leadership_scores rescales a chamber's PageRank to [0, 1], so
    the chamber's lowest member gets exactly 0.0. That used to be read as
    "no data" and scored a neutral 50, while the next member up scored ~0."""

    def test_zero_scores_below_the_next_member_up(self):
        lowest = _legislative_effectiveness_core([], 0.0, years_in_office=8)
        next_up = _legislative_effectiveness_core([], 0.001, years_in_office=8)
        lead = {c["label"]: c for c in lowest["components"]}["Legislative leadership"]
        assert lead["score"] == 0.0
        assert lowest["score"] <= next_up["score"]

    def test_missing_is_still_neutral(self):
        core = _legislative_effectiveness_core([], None, years_in_office=8, chamber="senate")
        lead = {c["label"]: c for c in core["components"]}["Legislative leadership"]
        assert lead["score"] == 50.0
        assert "no cosponsorship-network data" in lead["detail"]

    def test_detail_does_not_call_it_a_percentile(self):
        core = _legislative_effectiveness_core([], 0.3, years_in_office=8)
        detail = {c["label"]: c for c in core["components"]}["Legislative leadership"]["detail"]
        assert "percentile" not in detail
        assert "within the chamber" in detail
