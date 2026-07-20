"""Tests for the five representation sub-score calculations."""


from app.models import Senator
from app.pipeline.analyze import score_calculator
from app.pipeline.analyze.score_calculator import (
    _advancement_baseline,
    _calc_constituent_alignment,
    _calc_funding_diversity,
    _calc_funding_independence,
    _calc_legislative_effectiveness,
    _calc_promise_persistence,
    _funding_independence_core,
    _les_bill_stage,
    _les_cumulative_credit,
    _les_significance_weight,
    calculate_scores,
    clamp,
    compute_overall_score,
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
        # No district given -> Senate multiplier (x3.2, 2026-07 re-audit:
        # live Senate median PAC ratio is 15.7%, not the old ~28%
        # assumption). 30% PAC is now well above that real median, so the
        # PAC component scores low (~4 raw, ~3.9 after the no-committee-
        # type utilization fallback), not near-neutral: 17% small donors
        # (~46.6, just under the ~18.5% national mean), concentration pool
        # below the $250K floor (-> neutral 50):
        # FI = 0.5*3.9 + 0.25*46.6 + 0.25*50 ~= 26.
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

    def test_pac_utilization_signal_maxed_out_scores_lower(self):
        """PACs uniformly maxing out their legal per-election cap should
        score worse than PACs giving only a token amount, holding the
        overall PAC ratio and dollar total identical — the whole point of
        replacing the old absolute-dollar penalty."""
        base = {
            "totalRaised": 1_000_000,
            "totalFromPACs": 100_000,
            "smallDonorPercentage": 20,
        }
        maxed_out = {
            **base,
            "topDonors": [
                {"total": 5_000, "committeeType": "Q"} for _ in range(20)
            ],
        }
        token_amounts = {
            **base,
            "topDonors": [
                {"total": 500, "committeeType": "Q"} for _ in range(200)
            ],
        }
        score_maxed = _calc_funding_independence(maxed_out)
        score_token = _calc_funding_independence(token_amounts)
        assert score_token > score_maxed

    def test_pac_utilization_respects_committee_type_cap(self):
        """A nonmulticandidate PAC (lower cap) giving the same dollar amount
        as a multicandidate PAC should register as MORE utilized (closer to
        its smaller cap), and therefore score worse."""
        base = {"totalRaised": 1_000_000, "totalFromPACs": 100_000, "smallDonorPercentage": 20}
        multicandidate = {**base, "topDonors": [{"total": 3_500, "committeeType": "Q"}]}
        nonmulticandidate = {**base, "topDonors": [{"total": 3_500, "committeeType": "N"}]}
        score_multi = _calc_funding_independence(multicandidate)
        score_non = _calc_funding_independence(nonmulticandidate)
        assert score_non < score_multi

    def test_pac_utilization_excludes_non_pac_committee_types(self):
        """A large joint-fundraising-committee transfer (committee_type
        e.g. "Y" for party, or any code that isn't "Q"/"N") is not a PAC
        subject to the $5,000/$3,500 caps — it must not be swept into the
        nonqualified bucket, where a $110K JFC transfer would misleadingly
        register as "maxed out." With only a non-PAC committee type
        present, this should behave identically to having no PAC data at
        all (the dollar-based fallback)."""
        funding_with_jfc_only = {
            "totalRaised": 5_000_000,
            "totalFromPACs": 2_000_000,
            "topDonors": [{"total": 110_000, "committeeType": "Y"}],
        }
        funding_with_no_committee_data = {
            "totalRaised": 5_000_000,
            "totalFromPACs": 2_000_000,
            "topDonors": [{"total": 50_000} for _ in range(10)],  # sums differently but both hit the fallback
        }
        jfc_breakdown = _funding_independence_core(funding_with_jfc_only)
        no_data_breakdown = _funding_independence_core(funding_with_no_committee_data)
        # Both fall back to the identical dollar-based volume_factor
        # (driven only by totalFromPACs, which is the same in both cases).
        assert "no PAC committee-type data" in jfc_breakdown["components"][0]["detail"]
        assert "no PAC committee-type data" in no_data_breakdown["components"][0]["detail"]

    def test_pac_utilization_falls_back_without_committee_type_data(self):
        """No contributing PAC has a resolved committee type — degrades to
        the original dollar-based penalty rather than skipping the
        correction (same score as before this feature existed)."""
        funding = {
            "totalRaised": 5_000_000,
            "totalFromPACs": 2_000_000,
            "topDonors": [{"total": 50_000} for _ in range(10)],  # no committeeType key
        }
        breakdown = _funding_independence_core(funding)
        detail = breakdown["components"][0]["detail"]
        assert "no PAC committee-type data" in detail

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

    def test_district_bypasses_state_population_adjustment(self):
        """House members (district is not None) keep the original flat
        40%-cap behavior — the state-population fix is Senate-only until a
        real district-population audit justifies extending it. A ND House
        seat must score identically to the same raw % from any other
        state once district is given."""
        funding = {
            "totalRaised": 2_000_000,
            "totalFromPACs": 400_000,
            "smallDonorPercentage": 15,
            "topDonors": [],
        }
        nd_house = _funding_independence_core(funding, state="ND", district=1)
        ca_house = _funding_independence_core(funding, state="CA", district=12)
        assert nd_house["components"][1]["score"] == ca_house["components"][1]["score"] == 37.5

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


class TestConstituentAlignment:
    """v4.2: score is relative to what the seat's electorate expects.

    Matching the seat's expected break rate ≈ neutral 50; crossing
    beyond it earns credit; hyper-loyalty in a swing/opposed seat
    drifts below neutral but never to a failure-grade floor.
    """

    def _make_votes(self, with_party=0, against_party=0, policy="JUSTICE", crossing_unity=None):
        votes = []
        for _ in range(with_party):
            votes.append({"votedWithParty": True, "policyArea": policy, "vote": "Yea"})
        for _ in range(against_party):
            vote = {"votedWithParty": False, "policyArea": policy, "vote": "Yea"}
            if crossing_unity is not None:
                vote["opposingPartyUnityPct"] = crossing_unity
            votes.append(vote)
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

    def test_low_unity_crossings_score_at_least_as_high_as_high_unity(self):
        """2026-07 crossing-quality fix: at the same crossing rate, a
        member whose crossings landed on barely-partisan votes (opposing
        party's own majority near the 65% labeling floor — reads as
        consensus-building) must score at least as high as a member whose
        crossings landed on votes where the opposing party voted in near
        lockstep (reads as adopting the opposition's own party line, not
        building consensus)."""
        funding = {"totalRaised": 1_000_000, "totalFromPACs": 0}
        record_consensus = {
            "keyVotes": self._make_votes(with_party=70, against_party=30, crossing_unity=0.65),
            "recentVotes": [],
        }
        record_lockstep = {
            "keyVotes": self._make_votes(with_party=70, against_party=30, crossing_unity=1.0),
            "recentVotes": [],
        }
        score_consensus = _calc_constituent_alignment(record_consensus, [], funding)
        score_lockstep = _calc_constituent_alignment(record_lockstep, [], funding)
        assert score_consensus >= score_lockstep

    def test_crossing_quality_discount_mechanism_at_a_nonzero_value(self, monkeypatch):
        """CROSSING_QUALITY_DISCOUNT ships at 0.0 (see its docstring — no
        historical opposing_party_unity_pct data exists yet), which makes
        the test above pass trivially (both sides land on an identical
        score, since a 0.0 discount is a no-op regardless of unity).
        Patch in a real discount so the actual mechanism — not just its
        inert default — gets exercised before it's ever turned on in
        production."""
        monkeypatch.setattr(score_calculator, "CROSSING_QUALITY_DISCOUNT", 0.5)
        funding = {"totalRaised": 1_000_000, "totalFromPACs": 0}
        record_consensus = {
            "keyVotes": self._make_votes(with_party=70, against_party=30, crossing_unity=0.65),
            "recentVotes": [],
        }
        record_lockstep = {
            "keyVotes": self._make_votes(with_party=70, against_party=30, crossing_unity=1.0),
            "recentVotes": [],
        }
        score_consensus = _calc_constituent_alignment(record_consensus, [], funding)
        score_lockstep = _calc_constituent_alignment(record_lockstep, [], funding)
        assert score_consensus > score_lockstep

    def test_missing_crossing_unity_signal_unchanged_from_no_discount(self):
        """A crossing vote with no opposingPartyUnityPct at all (older
        data, or insufficient roll-call member data) must never be
        penalized for the missing signal — same score as a crossing at
        the minimum (no-discount) unity value."""
        funding = {"totalRaised": 1_000_000, "totalFromPACs": 0}
        record_no_signal = {
            "keyVotes": self._make_votes(with_party=70, against_party=30),
            "recentVotes": [],
        }
        record_min_unity = {
            "keyVotes": self._make_votes(with_party=70, against_party=30, crossing_unity=0.65),
            "recentVotes": [],
        }
        score_no_signal = _calc_constituent_alignment(record_no_signal, [], funding)
        score_min_unity = _calc_constituent_alignment(record_min_unity, [], funding)
        assert score_no_signal == score_min_unity

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

    def test_lobbying_matches_no_longer_affect_score(self):
        """v6.5: Donor independence removed from Constituent Alignment
        (folded into/duplicative of Funding Independence — see
        config_definitions.SCORE_WEIGHTS's r=0.72 rationale). lobbying_matches
        is still accepted for call-site compatibility but no longer changes
        the score."""
        record = {
            "keyVotes": self._make_votes(with_party=90, against_party=10),
            "recentVotes": [],
        }
        matches = [
            {"donationToSenator": 200_000, "isConsensusVote": False},
            {"donationToSenator": 150_000, "isConsensusVote": False},
            {"donationToSenator": 100_000, "isConsensusVote": False},
        ]
        score_with = _calc_constituent_alignment(
            record, matches,
            {"totalRaised": 1_000_000, "totalFromPACs": 500_000},
        )
        score_without = _calc_constituent_alignment(
            record, [],
            {"totalRaised": 1_000_000, "totalFromPACs": 500_000},
        )
        assert score_with == score_without

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
        """A senator whose funding is almost entirely small-dollar (real
        example: Bernie Sanders, 63% small-donor / 0.28% classified-
        industry money) previously scored exactly 69 — the population-wide
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

    def test_no_data_returns_neutral(self):
        score = _calc_legislative_effectiveness([], None)
        assert score == 50

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
            one_bill_zero_advanced, None, years_in_office=2,
        )
        score_confirmed_zero = _calc_legislative_effectiveness(
            [], None, years_in_office=2,
        )
        assert score_confirmed_zero <= score_tried_and_failed

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

    def test_low_bill_count_volume_is_shrunk_not_raw(self):
        """A single sponsored bill's expected-vs-actual credit gap must be
        shrunk toward neutral 50 by the same n_sub-based confidence curve
        every other low-n component in this file uses, not treated as
        fully-confident data — one real attempt should never score far
        below a member who sponsored nothing."""
        one_bill = [
            {"title": "B1", "isLaw": False, "latestAction": "Introduced",
             "billType": "s", "congress": 119},
        ]
        score = _calc_legislative_effectiveness(one_bill, None)
        assert score > 40

    def test_prolific_but_no_passage(self):
        """Many bills introduced but none advanced past stage 1 still
        earns real (if modest) credit under the cumulative-stage sum —
        raw volume counts for something in V&W's real methodology, unlike
        the old percentage-based advancement rate which this replaced."""
        bills = [
            {"title": f"Bill {i}", "isLaw": False, "latestAction": "Introduced",
             "billType": "s", "congress": 119}
            for i in range(50)
        ]
        score = _calc_legislative_effectiveness(bills, None)
        assert 45 <= score <= 75

    def test_high_passage_rate(self):
        """Bills that became law contribute credit at every earlier stage
        too (V&W's real cumulative design), so swapping some introduced-
        only bills for ones that became law must raise the score at the
        same total bill count — a relative comparison, not an absolute
        threshold, since the absolute score also depends on the
        population-average baseline this component is compared against."""
        n = 13
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
        assert _les_bill_stage({"latestAction": "Passed Senate", "billType": s}) == 3
        assert _les_bill_stage({"latestAction": "Agreed to", "billType": s}) == 3
        assert _les_bill_stage({"latestAction": "Anything", "billType": s, "isLaw": True}) == 4
        # Real `stage` classification always wins over the text fallback.
        assert _les_bill_stage({"stage": "IN_COMMITTEE", "latestAction": "Introduced"}) == 2
        assert _les_bill_stage({"stage": "ENACTED", "latestAction": "Introduced"}) == 4

    def test_les_significance_weight(self):
        """Commemorative resolutions weight 1x, substantive bills 5x —
        V&W's real 2-tier split this platform implements (their 3rd tier,
        "substantive and significant," is not implemented — see the
        module comment above _LES_STAGE_ORDER)."""
        assert _les_significance_weight("sres") == 1.0
        assert _les_significance_weight("hres") == 1.0
        assert _les_significance_weight("s") == 5.0
        assert _les_significance_weight("hr") == 5.0

    def test_les_cumulative_credit_scales_with_both_significance_and_stage(self):
        """A bill that became law contributes MORE cumulative credit than
        one that only reached committee, at the same significance — V&W's
        real design credits every stage a bill passed through, not just
        its final one, so a law is worth 4x a merely-introduced bill of
        the same type, not the same 1x an absolute pass/fail rate would
        give it."""
        introduced = {"latestAction": "Introduced", "billType": "s"}
        became_law = {"latestAction": "Introduced", "billType": "s", "isLaw": True}
        commemorative_law = {"latestAction": "Introduced", "billType": "sres", "isLaw": True}
        assert _les_cumulative_credit(became_law) == 4 * _les_cumulative_credit(introduced)
        # Significance and stage both matter independently: a commemorative
        # bill that became law still earns less than a substantive bill at
        # the same stage — significance weight isn't overridden by outcome.
        assert _les_cumulative_credit(commemorative_law) < _les_cumulative_credit(became_law)

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
        expected-vs-actual gap saturates at _LES_CREDIT_SATURATION (same
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
        they're less effective (2026-07 audit: Senate population-average
        significance-weighted credit is 285/congress vs House's 122).
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
            score_independent_voting=70, score_funding_diversity=65,
            score_legislative_effectiveness=82,
        ))
        db_session.commit()

        row = db_session.query(
            Senator.id, Senator.name, Senator.state, Senator.party,
            Senator.score_funding_independence, Senator.score_promise_persistence,
            Senator.score_independent_voting, Senator.score_funding_diversity,
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
            score_independent_voting=70, score_funding_diversity=65,
            score_legislative_effectiveness=82,
        ))
        db_session.commit()
        full = db_session.query(Senator).filter(Senator.id == "S002").first()

        as_dict = {
            "fundingIndependence": 60,
            "promisePersistence": 999,
            "independentVoting": 70,
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

    # A few hand-verified Cook Political Report 2022 published PVIs. The
    # computed values must land within +/-1 (Cook's exact formula applies
    # undisclosed recency weighting we deliberately don't replicate).
    COOK_ANCHORS = {
        "WY": 25, "WV": 22, "MA": -15, "CA": -13, "MI": 1, "PA": 2,
        "GA": 3, "TX": 5, "DC": -43,
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

        # National two-party D share = 50% both cycles.
        counts = {
            "2016": {"national": {"D": 100, "R": 100},
                     "XX": {"D": 50, "R": 50},    # exactly national -> EVEN
                     "YY": {"D": 40, "R": 60}},   # 10pts more R -> R+10
            "2020": {"national": {"D": 100, "R": 100},
                     "XX": {"D": 50, "R": 50},
                     "YY": {"D": 40, "R": 60}},
        }
        out = mod.compute_pvi(counts)
        assert out["XX"] == 0
        assert out["YY"] == 10  # positive = R lean
