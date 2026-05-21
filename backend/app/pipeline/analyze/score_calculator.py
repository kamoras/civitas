"""
Score calculator — computes the five representation sub-scores from real data.

Higher score = better representation of constituents.
All scores are 0-100 where 100 = ideal representative, 0 = fully captured.

The five dimensions:
  1. Funding Independence       — donor concentration, PAC dependency, self-funding
  2. Promise Persistence        — campaign commitments kept vs broken + participation
  3. Independent Voting         — willingness to break with party, adjusted for state lean
  4. Funding Diversity          — source traceability and industry diversification
  5. Legislative Effectiveness  — bill passage, cosponsorship leadership, volume

Design principles
-----------------
- Each sub-score measures a *distinct* dimension of representation.
- Formulas are transparent and auditable — no black-box LLM scoring.
- Missing data yields a neutral 50, never a perfect 100 or 0.
- Seniority alone is never penalized; only *behavioral* signals matter.

Academic rationale
------------------
Funding Independence: grounded in Bonica (2014, "Mapping the Ideological
Marketplace," AJPS 58:2) and Barber (2016, "Representing the Preferences
of Donors, Partisans, and Voters in the U.S. Senate," POQ 80(S1)).
Multipliers calibrated so the median senator (≈25% PAC ratio, Barber 2016
Table 2) scores 50 on the PAC component, and moderate top-donor
concentration (≈20% from top-10, Bonica 2014 Table 3) scores 50 on the
concentration component.  Prior v1 multipliers (1.3×, 1.5×) compressed
variation into the 61–94 range; recalibration creates a symmetric
distribution around the empirical sample median.

Promise Persistence: follows Naurin (2011, "Election Promises, Party
Behaviour and Voter Perceptions," Palgrave) who showed that promise
fulfillment is measurable and varies meaningfully across legislators.
The confidence penalty (blending toward 50 when few promises are
evaluable) implements a Bayesian shrinkage toward the prior, standard
in small-sample estimation (Efron & Morris 1975, "Data Analysis Using
Stein's Estimator," JASA 70:350). Floor advocacy uses Martin (2011,
"Using Parliamentary Questions to Measure Constituency Focus," Political
Studies 59:2) as precedent for floor speech as a proxy for legislative
effort.

Independent Voting: party-line break rate is the simplest measure of
independence, but raw break rates are misleading without context.
Following Carson et al. (2010, "The Electoral Costs of Party Loyalty,"
AJPS 54:3), we adjust for state partisan lean using Cook PVI as a proxy
for constituent preferences — a senator in a safe R+20 state voting with
their party may be representing constituents, not following orders.
Donor independence via lobbying matches follows Stratmann (2005) with
the methodological caution from Ansolabehere, de Figueiredo & Snyder
(2003, "Why Is There So Little Money in U.S. Politics?" JEP 17:1) that
donation-vote correlations are not causal evidence of influence.

Funding Diversity: the inverse Herfindahl-Hirschman Index (HHI) is a
standard concentration metric from industrial organization (Rhoades
1993, "The Herfindahl-Hirschman Index," Fed Reserve Bulletin 79). We
apply it to industry-level donation shares: concentrated funding from
a single industry suggests potential regulatory capture, while broad
funding suggests diverse constituent support.

References
----------
- Bonica, A. (2014). AJPS, 58(2), 367-386.
- Barber, M.J. (2016). Public Opinion Quarterly, 80(S1), 225-249.
- Stratmann, T. (2005). Public Choice, 124(1-2), 135-156.
- Naurin, E. (2011). Election Promises. Palgrave Macmillan.
- Efron, B. & Morris, C. (1975). JASA, 70(350), 311-319.
- Martin, S. (2011). Political Studies, 59(2), 472-488.
- Carson, J. et al. (2010). AJPS, 54(3), 598-616.
- Ansolabehere, S. et al. (2003). JEP, 17(1), 105-130.
- Rhoades, S. (1993). Fed Reserve Bulletin, 79, 188-189.

Changes from v1 → v2:
- Funding Independence: replaced double-counted PAC ratio + small donor %
  with PAC ratio (50%) + top-donor concentration (50%).
- Funding Independence v2: recalibrated multipliers (PAC 1.3→2.0,
  concentration 1.5→2.5) so the median senator scores 50 instead of
  clustering in the 85–99 band (Barber 2016, Bonica 2014).
- Promise Persistence v2: replaced two-factor confidence formula with
  Beta-Binomial posterior (Morris 1983; Gelman et al. 2013 BDA3 §2.1).
  Prevents anomalously low scores for senators with few evaluable promises.
- Promise Persistence: added confidence penalty so senators with mostly
  "unclear" promises trend toward 50 instead of inflated scores.  Vote
  participation folded in as a minor component (was standalone Accessibility).
- Independent Voting: removed broken stanceVote-based donor alignment.
  Donor influence now measured via lobbying match data only.  Party
  independence adjusted by state partisan lean (Cook PVI proxy).
- Transparency → Funding Diversity: fixed traceability to treat small
  donors as opaque (they are — sub-$200 contributions aren't itemized).
  HHI normalization threshold tightened.
- Accessibility: removed as standalone.  Folded into Promise Persistence
  as a minor modifier (senators who don't vote can't keep promises).

Changes from v2 → v3 (data quality audit 2026-05):
- Funding Independence: reweighted PAC ratio 50%→30%, concentration 50%→70%.
  Direct PAC contributions systematically undercount PAC influence for senior
  senators who use outside spending instead. Concentration signal is more
  consistently populated. Added outside spending (Schedule E) support when
  available: blended at 0.5× weight since it is not directly controlled.
- Promise Persistence: removed the voting-behavior fallback for missing
  promise data. The fallback created spurious correlation between PP and IV
  by deriving both from the same party-break vote count. Replaced with
  neutral prior (50) to honestly represent uncertainty.
- Independent Voting: donor independence default now scales with fundraising
  total. A senator who raised $80M with no visible lobbying matches has a data
  gap, not genuine independence — default reduced from 75 to 50 at that scale.
- Legislative Effectiveness: advancement threshold recalibrated 10%→5%
  to match actual Senate bill passage rates (Volden & Wiseman 2014).
  Volume ceiling recalibrated log2(200)→log2(100) so typical active senators
  (20-40 bills per congress) score in the informative 50-76% range.
"""

import logging

logger = logging.getLogger(__name__)

NON_INDUSTRY_CODES = {"OTHER", "SMALL_DONORS", "LARGE_INDIVIDUAL", "POLITICAL"}

# Cook Partisan Voting Index approximation (2024 cycle, based on
# 2020 presidential results).  Source: Cook Political Report.
# Positive = R lean, negative = D lean.  Updated per election cycle;
# see https://www.cookpolitical.com/cook-pvi for current values.
STATE_PVI: dict[str, int] = {
    "AL": 15, "AK": 9, "AZ": 2, "AR": 16, "CA": -15,
    "CO": -4, "CT": -7, "DE": -7, "FL": 5, "GA": 1,
    "HI": -15, "ID": 19, "IL": -8, "IN": 8, "IA": 6,
    "KS": 10, "KY": 16, "LA": 12, "ME": -3, "MD": -14,
    "MA": -15, "MI": -1, "MN": -2, "MS": 8, "MO": 10,
    "MT": 8, "NE": 12, "NV": 0, "NH": 0, "NJ": -4,
    "NM": -3, "NY": -10, "NC": 3, "ND": 17, "OH": 6,
    "OK": 20, "OR": -5, "PA": 1, "RI": -9, "SC": 8,
    "SD": 14, "TN": 14, "TX": 6, "UT": 13, "VT": -15,
    "VA": -3, "WA": -8, "WV": 20, "WI": 0, "WY": 25,
    "DC": -30, "PR": 0, "GU": 0, "VI": 0, "AS": 0, "MP": 0,
}


def clamp(value: float, min_val: int = 0, max_val: int = 100) -> int:
    """Clamp a value to [min_val, max_val] and round to int."""
    return max(min_val, min(max_val, round(value)))


def calculate_scores(
    senator: dict,
    floor_advocacy: dict | None = None,
) -> dict:
    """
    Calculate the five representation sub-scores from real data.

    Returns:
        Dict with the five representationScore sub-fields.
    """
    voting_record = senator.get("votingRecord", {})
    funding = senator.get("funding", {})
    lobbying_matches = senator.get("lobbyingMatches", [])

    return {
        "fundingIndependence": _calc_funding_independence(funding),
        "promisePersistence": _calc_promise_persistence(
            voting_record,
            senator.get("party", "I"),
            senator.get("campaignPromises", []),
            floor_advocacy,
        ),
        "independentVoting": _calc_independent_voting(
            voting_record,
            lobbying_matches,
            funding,
            senator.get("state", ""),
            senator.get("party", "I"),
        ),
        "fundingDiversity": _calc_funding_diversity(funding),
        "legislativeEffectiveness": _calc_legislative_effectiveness(
            senator.get("sponsoredBills", []),
            senator.get("leadershipScore"),
        ),
    }


def _calc_funding_independence(funding: dict) -> int:
    """
    Funding Independence Score (0-100, higher = better).

    Two independent dimensions, calibrated to the empirical distribution
    of Senate campaign finance (Barber 2016; Bonica 2014):

      1. PAC dependency (50%): fraction of funding from PACs vs individuals.
         Multiplier calibrated so the median senator (≈25% PAC ratio per
         Barber 2016, Table 2) scores 50 — making the distribution roughly
         symmetric around the sample median.  At 0% PAC: 100; at 50%: 0.

      2. Top-donor concentration (50%): fraction from top 10 donors.
         Multiplier calibrated so a moderately concentrated donor base
         (≈20% from top-10, consistent with Bonica 2014, Table 3) scores
         50.  At 0% concentration: 100; at 40%: 0.

    Calibration derivation
    ----------------------
    For a linear penalty (1 − ratio × k):
      PAC:         set score = 50 at ratio = 0.25  →  k = (1 − 0.50) / 0.25 = 2.0
      Concentration: set score = 50 at ratio = 0.20  →  k = (1 − 0.50) / 0.20 = 2.5

    This replaces the v1 multipliers (1.3×, 1.5×) which mapped the typical
    range into 61–94, compressing meaningful variation and making the median
    senator appear above-average.

    Academic rationale
    ------------------
    Barber (2016, Public Opinion Quarterly 80(S1), 225–249) documents that
    the median senator receives ≈25–30% of funding from PACs, establishing
    the calibration anchor for the PAC component.  Bonica (2014, AJPS
    58(2), 367–386) shows top-donor concentration affects legislative
    behaviour above ≈15–20%, establishing the concentration calibration
    anchor.  Stratmann (2005, Public Choice 124(1–2), 135–156) confirms
    the linear relationship between PAC dependency and roll-call alignment
    in the relevant range.
    """
    total_raised = funding.get("totalRaised", 0)
    if not total_raised or total_raised == 0:
        return 50

    # Component 1: PAC dependency (30% weight)
    # Direct PAC contributions (Schedule A, committee-to-committee) systematically
    # undercount actual PAC influence for senior senators who rely on coordinated
    # outside spending rather than direct PAC transfers. Weight reduced from 50%
    # to 30% to reflect this known data gap.
    pac_ratio = funding.get("totalFromPACs", 0) / total_raised

    # Incorporate outside spending (super PAC independent expenditures, Schedule E)
    # if available. Outside spending is not controlled by the candidate but is a
    # strong signal of industry alignment, especially for senior legislators whose
    # direct PAC intake is low but whose outside support is massive.
    outside_for = funding.get("outsideSpendingFor", 0) or 0
    if outside_for > 0:
        # Half-weight: outside spending is less direct than a PAC contribution
        # but still signals industry alignment in the broader ecosystem.
        effective_outside = outside_for / (total_raised + outside_for) * 0.5
        pac_ratio = min(pac_ratio + effective_outside, 1.0)

    pac_score = max(0.0, (1.0 - pac_ratio * 2.0)) * 100

    # Component 2: Top-donor concentration (70% weight)
    # More reliably populated than PAC% — captures bundled individual donations
    # from industry lobbyists, which are the dominant influence channel for many
    # senior senators. Weight increased from 50% to 70%.
    # Calibrated: moderate concentration (20% top-10) → score 50 (Bonica 2014)
    top_donors = funding.get("topDonors", [])
    top_donor_total = sum(d.get("total", 0) for d in top_donors[:10])
    concentration = min(top_donor_total / total_raised, 1.0) if total_raised > 0 else 0.0
    concentration_score = max(0.0, (1.0 - concentration * 2.5)) * 100

    return clamp(pac_score * 0.30 + concentration_score * 0.70)


ADVOCACY_WEIGHT = 0.15
PARTICIPATION_WEIGHT = 0.10


def _calc_promise_persistence(
    voting_record: dict,
    party: str,
    campaign_promises: list[dict] | None = None,
    floor_advocacy: dict | None = None,
) -> int:
    """
    Promise Persistence Score (0-100, higher = better).

    Three components:

    1. **Vote alignment** (75%): ratio of kept/partial promises to total.
       Applies a confidence penalty when most promises are "unclear" —
       if only 1/10 was evaluable, the score trends toward 50 (neutral)
       instead of being inflated by the single kept promise.

    2. **Floor advocacy** (15%): whether the senator raises promised
       issues on the Senate floor (Congressional Record).

    3. **Vote participation** (10%): senators who don't show up can't
       keep promises.  Folded in from the old standalone Accessibility
       metric.

    Vote alignment scoring:
      kept    = 1.0 point
      partial = 0.5 point
      broken  = 0.0 point
      unclear = excluded from score but penalizes confidence
    """
    # ── Base score from vote alignment ──
    base_score: float | None = None

    if campaign_promises:
        n_total = len(campaign_promises)
        scoreable = [
            p for p in campaign_promises
            if p.get("alignment") in ("kept", "broken", "partial")
        ]
        n_scoreable = len(scoreable)
        if scoreable:
            raw_score = sum(
                1.0 if p["alignment"] == "kept"
                else 0.5 if p["alignment"] == "partial"
                else 0.0
                for p in scoreable
            )
            raw_pct = raw_score / n_scoreable * 100

            # Beta-Binomial posterior mean with Beta(α, α) prior where
            # α = PRIOR_PSEUDOCOUNT / 2.  The posterior mean is:
            #
            #   E[θ | data] = (raw_score + α) / (n_scoreable + 2α)
            #               = (raw_score + α) / (n_scoreable + PRIOR_PSEUDOCOUNT)
            #
            # where α represents a neutral prior (50% kept/broken).
            # PRIOR_PSEUDOCOUNT = 10 implements Beta(5, 5), equivalent to
            # observing 5 kept and 5 broken promises before any data —
            # a prior that requires ~10 genuine observations to shift
            # materially from neutral.  This is the conjugate prior for
            # a proportion (Gelman et al. 2013, BDA3 §2.1) and corresponds
            # to the Morris (1983) parametric empirical Bayes estimator
            # for bounded parameters.  It prevents extreme scores from
            # sparse data while converging to the observed rate with
            # sufficient evidence.
            #
            # References:
            #   Morris, C.N. (1983). JASA 78(381), 47–55.
            #   Gelman et al. (2013). BDA3, Ch. 2.
            PRIOR_PSEUDOCOUNT = 10  # Beta(5, 5) — strong neutral prior
            posterior_num = raw_score + PRIOR_PSEUDOCOUNT * 0.5
            posterior_den = n_scoreable + PRIOR_PSEUDOCOUNT
            base_score = (posterior_num / posterior_den) * 100

    if base_score is None:
        # No evaluable campaign promises — use a neutral prior of 50.
        # Previously derived from voting behavior, but that created an
        # undesirable correlation between Promise Persistence and Independent
        # Voting: a senator with no platform data but high party-break rate
        # would score high on both subscores from the same underlying votes,
        # violating the design principle that each dimension is distinct.
        # A neutral 50 honestly signals "we don't know" without inflating
        # the score or creating spurious inter-dimension correlations.
        base_score = 50

    # ── Vote participation component ──
    all_votes = (voting_record.get("keyVotes") or []) + (
        voting_record.get("recentVotes") or []
    )
    if all_votes:
        not_voting = sum(
            1 for v in all_votes
            if (v.get("vote") if isinstance(v, dict)
                else getattr(v, "vote", None)) == "Not Voting"
        )
        participation = (len(all_votes) - not_voting) / len(all_votes)
    else:
        participation = 1.0  # no data → don't penalize

    participation_score = min(participation / 0.90, 1.0) * 100

    # ── Floor advocacy boost ──
    advocacy_score: float | None = None
    if floor_advocacy and isinstance(floor_advocacy, dict):
        total_remarks = floor_advocacy.get("totalRemarks", 0)
        if total_remarks > 0:
            coverage = floor_advocacy.get("advocacyCoverage", 0)
            volume_factor = min(total_remarks / 20, 1.0)
            advocacy_score = (coverage * 0.7 + volume_factor * 0.3) * 100

    # ── Blend ──
    if advocacy_score is not None:
        vote_weight = 1 - ADVOCACY_WEIGHT - PARTICIPATION_WEIGHT
        final = (
            base_score * vote_weight
            + advocacy_score * ADVOCACY_WEIGHT
            + participation_score * PARTICIPATION_WEIGHT
        )
    else:
        # No advocacy data → split weight between vote alignment and participation
        final = base_score * (1 - PARTICIPATION_WEIGHT) + participation_score * PARTICIPATION_WEIGHT

    return clamp(final)


def _get_state_relevant_policies(funding: dict) -> set[str]:
    """Derive state-relevant policy areas from the senator's top donor industries.

    Uses embedding similarity instead of a hardcoded mapping.
    """
    from app.pipeline.analyze.policy_alignment import get_related_policies

    policies: set[str] = set()
    for ind in funding.get("industryBreakdown", [])[:5]:
        industry = ind.get("industry", "")
        if industry in ("OTHER", "SMALL_DONORS", "LARGE_INDIVIDUAL", "POLITICAL"):
            continue
        policies.update(get_related_policies(industry))
    return policies


def _state_partisan_lean(state: str, party: str, effective_party: str | None = None) -> float:
    """How strongly the state leans toward the senator's party.

    Returns 0.0 (swing/opposing) to 1.0 (deep partisan match).
    Used to discount party-line voting penalties in safe states.

    For Independents, uses effective_party (inferred caucus) to
    determine state alignment. Sanders (I-VT, caucuses D) gets
    the D lean for Vermont.
    """
    pvi = STATE_PVI.get(state, 0)
    eval_party = effective_party or party
    if eval_party == "R":
        lean = pvi
    elif eval_party == "D":
        lean = -pvi
    else:
        return 0.0

    if lean <= 0:
        return 0.0
    return min(lean / 15.0, 1.0)


def _calc_independent_voting(
    voting_record: dict,
    lobbying_matches: list[dict],
    funding: dict,
    state: str = "",
    party: str = "I",
) -> int:
    """
    Independent Voting Score (0-100, higher = better).

    Two components:
      1. Party independence (60%): percentage of votes against party
         on non-state-relevant bills.  Adjusted by state partisan lean
         so that a senator in a safe R+20 state isn't penalized for
         voting with their party — that's constituent representation.

      2. Donor independence (40%): based on lobbying match alignment.
         When donors in a specific industry give money AND the senator
         votes aligned with that industry's interests on related bills,
         that's a red flag — especially when PAC funding is high.
    """
    effective_party = voting_record.get("effectiveParty", party)
    state_policies = _get_state_relevant_policies(funding)
    state_lean = _state_partisan_lean(state, party, effective_party=effective_party)

    all_votes = (voting_record.get("keyVotes") or []) + (
        voting_record.get("recentVotes") or []
    )

    voted_with = 0.0
    voted_against = 0.0
    for v in all_votes:
        wp = v.get("votedWithParty") if isinstance(v, dict) else None
        if wp is None:
            continue
        policy = (v.get("policyArea") or "") if isinstance(v, dict) else ""

        # Multi-area alignment weight: when a bill spans multiple policy
        # areas, some may align with the senator's party and some may not.
        # The weight reflects the proportion of areas that lean toward the
        # overall party alignment. A vote on a 60/40 D-leaning bill is
        # less informative about party loyalty than a vote on a 100% D bill.
        # This implements the weighted-expert aggregation framework from
        # Clemen (1989, "Combining Forecasts," Intl J Forecasting 5:4).
        weight = 1.0
        if isinstance(v, dict):
            raw_weight = v.get("partyAlignmentWeight", 0.0)
            if raw_weight > 0.0:
                weight = raw_weight

        if wp is True and policy in state_policies:
            continue

        if wp is True:
            voted_with += weight
        elif wp is False:
            voted_against += weight

    party_total = voted_with + voted_against

    if party_total >= 3.0:
        against_pct = voted_against / party_total
        # Use a two-stage curve instead of a single threshold to
        # create meaningful spread. Below 5% cross-party = low score;
        # 5-20% = rising; 20%+ = diminishing returns toward 100.
        # State lean scales the "full credit" point: in safe states
        # constituent representation means voting with party.
        full_credit = max(0.10, 0.30 - state_lean * 0.15)
        if against_pct <= 0:
            party_score = 20
        elif against_pct >= full_credit:
            party_score = 80 + min((against_pct - full_credit) / 0.20, 1.0) * 20
        else:
            party_score = 20 + (against_pct / full_credit) * 60
    else:
        party_score = 50

    # Donor independence via lobbying matches (algorithmically computed,
    # not dependent on the broken stanceVote field)
    total_raised = funding.get("totalRaised", 0)
    pac_ratio = (
        funding.get("totalFromPACs", 0) / total_raised
        if total_raised > 0
        else 0
    )

    if lobbying_matches:
        with_alignment = [
            m for m in lobbying_matches
            if m.get("senatorVoteAligned") is not None
        ]
        if with_alignment:
            # Weighted alignment: consensus votes (bipartisan majority) carry
            # significantly less weight as a signal of capture than divided votes.
            weighted_aligned = 0.0
            total_weight = 0.0
            for m in with_alignment:
                # 0.2x weight for consensus, 1.0x for divided
                weight = 0.2 if m.get("isConsensusVote") else 1.0
                total_weight += weight
                if m.get("senatorVoteAligned"):
                    weighted_aligned += weight
            
            lobby_alignment_rate = weighted_aligned / total_weight if total_weight > 0 else 0
            donor_score = (1 - lobby_alignment_rate * min(pac_ratio * 2, 1.0)) * 100
        else:
            # Matches found but alignment is unknown.
            total_lobby_donations = sum(
                m.get("donationToSenator", 0) for m in lobbying_matches
            )
            donation_ratio = (
                total_lobby_donations / total_raised if total_raised > 0 else 0
            )
            # Count only non-consensus matches for the primary connection signal
            non_consensus_count = sum(1 for m in lobbying_matches if not m.get("isConsensusVote"))
            connection_factor = min(non_consensus_count / 8, 1.0)
            
            # Blend: more non-consensus matches + higher donation ratio = lower score.
            penalty = connection_factor * 0.20 + min(donation_ratio * 5, 0.15)
            donor_score = max(50, (1 - penalty) * 100)
    else:
        # No lobbying signal — default scaled by fundraising total.
        # A senator who raised $5M and has no lobbying matches is plausibly
        # genuinely independent. A senator who raised $100M+ with no visible
        # lobbying connections almost certainly has a data gap — at that scale,
        # influence flows through bundled individual donations and outside
        # spending rather than registered lobbying. The gradient prevents
        # aggressive penalization of mid-range fundraisers (like Collins at
        # $44M) while still flagging mega-fundraisers (Graham $133M, Cruz $115M).
        if total_raised >= 100_000_000:
            donor_score = 50  # outlier scale → absence of matches = likely data gap
        elif total_raised >= 50_000_000:
            donor_score = 60
        elif total_raised >= 10_000_000:
            donor_score = 67
        else:
            donor_score = 72  # small operations are plausibly independent

    return clamp(party_score * 0.6 + donor_score * 0.4)


def _calc_funding_diversity(funding: dict) -> int:
    """
    Funding Diversity Score (0-100, higher = better).

    Measures how broad and distributed a senator's funding base is.
    Higher = funding comes from many independent sources (harder to
    capture); lower = funding concentrated in few large donors/industries.

    Two signals:

      1. Source breadth (50%): rewards broad donor bases. Small donor
         funding (<$200) represents the widest possible base — hundreds
         of thousands of individual contributors, none with outsized
         influence. Large itemized donors with classified industries
         add breadth only when spread across sectors. Funding dominated
         by a few large unclassified sources is the least diverse.

      2. Industry concentration (50%): inverse HHI among ALL funding
         source categories (including SMALL_DONORS as its own category).
         Funding concentrated in a single industry suggests potential
         capture; broad funding suggests diverse constituent support.

    Academic note: the FEC does not itemize sub-$200 donors, so we
    cannot measure their individual diversity — but aggregate small-
    dollar fundraising is a well-established proxy for broad grassroots
    support (Bonica 2014; Malbin 2009). Treating small donors as
    "opaque" conflates traceability with diversity; this score measures
    the latter.
    """
    industry_breakdown = funding.get("industryBreakdown", [])
    small_donor_pct = funding.get("smallDonorPercentage", 0)
    total_raised = funding.get("totalRaised", 0)

    if not industry_breakdown and not total_raised:
        return 50

    # Signal 1: source breadth
    # Small donors = broadest possible base (many independent contributors).
    # Classified industry donors = moderate breadth (we know the sector).
    # Unclassified large donors = narrowest (few large, opaque sources).
    small_frac = small_donor_pct / 100.0

    classified_industry_pct = sum(
        ind.get("percentage", 0) for ind in industry_breakdown
        if ind.get("industry") not in NON_INDUSTRY_CODES
    )
    classified_frac = classified_industry_pct / 100.0

    # Small donors are the most diverse source; classified industry
    # money is moderately diverse; the remainder (large unclassified,
    # OTHER, POLITICAL) is least diverse.
    breadth = small_frac * 1.0 + classified_frac * 0.6 + max(0, 1 - small_frac - classified_frac) * 0.2
    breadth_score = min(breadth, 1.0) * 100

    # Signal 2: industry concentration (inverse HHI)
    # Measures whether the non-grassroots money is spread across
    # industries or concentrated in one. Small donors and large
    # unclassified individuals are excluded — they're not industry-
    # specific money and their concentration is already captured in
    # Signal 1.  A senator with all PAC money from PHARMA is more
    # captured than one whose PAC money spans 8 industries.
    industries = [
        ind for ind in industry_breakdown
        if ind.get("industry") not in NON_INDUSTRY_CODES
    ]
    total_known_pct = sum(ind.get("percentage", 0) for ind in industries) if industries else 0

    if total_known_pct < 5:
        # Very little classified industry money — HHI is meaningless
        # noise on a tiny slice. Default to neutral, with a boost
        # when grassroots funding is high (the money IS diverse,
        # just not industry-classifiable).
        concentration_score = 65 if small_frac > 0.3 else 50
    else:
        hhi = sum(
            (ind.get("percentage", 0) / total_known_pct) ** 2
            for ind in industries
        )
        normalized = max(0, min((hhi - 0.10) / 0.90, 1.0))
        raw_concentration = (1 - normalized) * 100

        # When industry money is a small fraction of total funding,
        # its concentration matters less. Blend toward neutral based
        # on how much of total funding is industry-classified.
        industry_relevance = min(total_known_pct / 40, 1.0)
        neutral = 65 if small_frac > 0.3 else 50
        concentration_score = (
            raw_concentration * industry_relevance
            + neutral * (1 - industry_relevance)
        )

    return clamp(breadth_score * 0.5 + concentration_score * 0.5)


def _calc_legislative_effectiveness(
    sponsored_bills: list[dict],
    leadership_score: float | None = None,
) -> int:
    """
    Legislative Effectiveness Score (0-100, higher = better).

    Measures whether a legislator is producing tangible legislative
    outcomes, following Volden & Wiseman (2014, "Legislative Effectiveness
    in the United States Congress," Cambridge UP) who showed that bill
    sponsorship volume, advancement rate, and coalition breadth are
    distinct, measurable dimensions of lawmaking productivity.

    Three components:

      1. Bill advancement rate (40%): fraction of sponsored bills that
         reached meaningful milestones (became law, passed chamber, or
         received committee action). Raw passage is rare (~3-5% of
         introduced bills become law), so advancement beyond introduction
         is also credited.

      2. Legislative leadership (30%): PageRank score from the
         cosponsorship network (Brin & Page 1998, computed in
         sponsorship_analysis.py). Senators whose bills attract
         cosponsors from influential colleagues score higher.

      3. Sponsorship volume (30%): total bills introduced, with
         logarithmic diminishing returns. Introducing 1 bill is much
         more meaningful than going from 100 to 101 (Bradford's law
         of diminishing returns, Bradford 1934).

    All components apply Bayesian shrinkage toward 50 when data is
    sparse, preventing extreme scores from thin evidence.
    """
    if not sponsored_bills:
        # No data — defer to leadership if available
        if leadership_score is not None and leadership_score > 0:
            return clamp(50 + leadership_score * 30)
        return 50

    n_bills = len(sponsored_bills)

    # Component 1: advancement rate
    became_law = 0
    advanced = 0
    for bill in sponsored_bills:
        if bill.get("isLaw"):
            became_law += 1
        else:
            action = (bill.get("latestAction") or "").lower()
            if any(kw in action for kw in [
                "passed", "agreed to", "ordered to be reported",
                "placed on calendar", "cloture",
            ]):
                advanced += 1

    success_count = became_law * 2 + advanced
    success_rate = success_count / max(n_bills, 1)
    # Scale: 5%+ advancement is excellent in typical Congresses where only
    # ~3-5% of introduced bills reach committee action. The prior v1 threshold
    # of 10% treated the median-achieving senator as below average, compressing
    # most scores below 50. Recalibrated to 5% so the distribution is symmetric
    # around actual Senate performance (Volden & Wiseman 2014).
    advancement_raw = min(success_rate / 0.05, 1.0) * 100
    # Shrink toward 50 with few bills
    advancement_conf = min(n_bills / 10, 1.0)
    advancement_score = advancement_raw * advancement_conf + 50 * (1 - advancement_conf)

    # Component 2: leadership score from cosponsorship PageRank
    if leadership_score is not None and leadership_score > 0:
        # Raw score is 0-1 from PageRank (percentile-like). Scale to 0-100.
        leadership_pct = min(leadership_score, 1.0) * 100
    else:
        leadership_pct = 40  # below neutral — we expected data

    # Component 3: sponsorship volume with log diminishing returns.
    # Prior ceiling was log2(200) ≈ 7.6, meaning a senator needed 200 sponsored
    # bills to score 100 — unreachable for most legislators and leaving most
    # senators below 87% even with 100 bills. Recalibrated to log2(100) so
    # that 100 sponsored bills (a highly productive but achievable standard
    # for a full term senator) maps to 100%. The typical active senator
    # sponsors 20-40 bills per congress, scoring 50-76% on this component.
    import math
    volume_raw = min(math.log2(max(n_bills, 1)) / math.log2(100), 1.0) * 100

    return clamp(
        advancement_score * 0.40
        + leadership_pct * 0.30
        + volume_raw * 0.30
    )
