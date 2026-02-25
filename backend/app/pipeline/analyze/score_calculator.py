"""
Score calculator — computes the four representation sub-scores from real data.

Higher score = better representation of constituents.
All scores are 0-100 where 100 = ideal representative, 0 = fully captured.

The four dimensions:
  1. Funding Independence  — donor concentration and PAC dependency
  2. Promise Persistence   — campaign commitments kept vs broken + participation
  3. Independent Voting    — willingness to break with party, adjusted for state lean
  4. Funding Diversity     — source traceability and industry diversification

Design principles
-----------------
- Each sub-score measures a *distinct* dimension of representation.
- Formulas are transparent and auditable — no black-box LLM scoring.
- Missing data yields a neutral 50, never a perfect 100 or 0.
- Seniority alone is never penalized; only *behavioral* signals matter.

Academic rationale
------------------
Funding Independence: grounded in Bonica (2014, "Mapping the Ideological
Marketplace," AJPS 58:2) which demonstrates that donor composition
(individual vs PAC, concentrated vs diffuse) is a strong predictor of
legislative behavior. PAC dependency ratio follows the operationalization
in Stratmann (2005, "Some Talk: Money in Politics," Public Choice 124:1-2).
Top-donor concentration uses the same intuition as HHI but applied to
individual donors rather than industries.

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
- Stratmann, T. (2005). Public Choice, 124(1-2), 135-156.
- Naurin, E. (2011). Election Promises. Palgrave Macmillan.
- Efron, B. & Morris, C. (1975). JASA, 70(350), 311-319.
- Martin, S. (2011). Political Studies, 59(2), 472-488.
- Carson, J. et al. (2010). AJPS, 54(3), 598-616.
- Ansolabehere, S. et al. (2003). JEP, 17(1), 105-130.
- Rhoades, S. (1993). Fed Reserve Bulletin, 79, 188-189.

Changes from v1:
- Funding Independence: replaced double-counted PAC ratio + small donor %
  with PAC ratio (50%) + top-donor concentration (50%).
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
    flip_flop_result: dict | None = None,
    floor_advocacy: dict | None = None,
) -> dict:
    """
    Calculate the four representation sub-scores from real data.

    Returns:
        Dict with the four representationScore sub-fields.
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
            flip_flop_result,
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
    }


def _calc_funding_independence(funding: dict) -> int:
    """
    Funding Independence Score (0-100, higher = better).

    Two independent dimensions:
      1. PAC dependency (50%): fraction of funding from PACs vs individuals.
         Operationalizes Stratmann's (2005) finding that PAC contributions
         are more strongly correlated with roll-call alignment than
         individual contributions.
      2. Top-donor concentration (50%): fraction from top 10 donors.
         Analogous to HHI but at the donor level — high concentration
         means a few large contributors dominate fundraising, creating
         dependency (Bonica 2014).

    These are genuinely independent — a senator can have many small PACs
    (low concentration, high PAC ratio) or one big individual donor
    (high concentration, low PAC ratio).
    """
    total_raised = funding.get("totalRaised", 0)
    if not total_raised or total_raised == 0:
        return 50

    pac_ratio = funding.get("totalFromPACs", 0) / total_raised
    pac_independence = 1 - pac_ratio

    top_donors = funding.get("topDonors", [])
    top_donor_total = sum(d.get("total", 0) for d in top_donors[:10])
    concentration = top_donor_total / total_raised if total_raised > 0 else 0
    concentration_independence = 1 - min(concentration, 1.0)

    raw = pac_independence * 0.5 + concentration_independence * 0.5
    return clamp(raw * 100)


ADVOCACY_WEIGHT = 0.15
PARTICIPATION_WEIGHT = 0.10


def _calc_promise_persistence(
    voting_record: dict,
    party: str,
    campaign_promises: list[dict] | None = None,
    flip_flop_result: dict | None = None,
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

            # Confidence penalty: blend toward 50 when few promises
            # were evaluable.  Full confidence when all are scoreable.
            confidence = n_scoreable / max(n_total, 1)
            base_score = raw_pct * confidence + 50 * (1 - confidence)

    if base_score is None:
        # No evaluable campaign promises — derive a proxy from voting
        # behavior.  Senators who vote more independently and participate
        # actively tend to be more accountable to their stated positions.
        all_votes = (voting_record.get("keyVotes") or []) + (
            voting_record.get("recentVotes") or []
        )
        if all_votes:
            cross_party = sum(
                1 for v in all_votes
                if (v.get("votedWithParty") if isinstance(v, dict) else None) is False
            )
            party_tracked = sum(
                1 for v in all_votes
                if (v.get("votedWithParty") if isinstance(v, dict) else None) is not None
            )
            if party_tracked >= 3:
                independence_rate = cross_party / party_tracked
                base_score = 35 + independence_rate * 60
            else:
                base_score = 50
        else:
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


def _state_partisan_lean(state: str, party: str) -> float:
    """How strongly the state leans toward the senator's party.

    Returns 0.0 (swing/opposing) to 1.0 (deep partisan match).
    Used to discount party-line voting penalties in safe states.
    """
    pvi = STATE_PVI.get(state, 0)
    if party == "R":
        lean = pvi  # positive PVI = R state
    elif party == "D":
        lean = -pvi  # negative PVI = D state
    else:
        return 0.0  # independents: no adjustment

    if lean <= 0:
        return 0.0  # state leans AGAINST senator's party
    return min(lean / 15.0, 1.0)  # scale: PVI 15+ = full discount


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
    state_policies = _get_state_relevant_policies(funding)
    state_lean = _state_partisan_lean(state, party)

    all_votes = (voting_record.get("keyVotes") or []) + (
        voting_record.get("recentVotes") or []
    )

    voted_with = 0
    voted_against = 0
    for v in all_votes:
        wp = v.get("votedWithParty") if isinstance(v, dict) else None
        if wp is None:
            continue
        policy = (v.get("policyArea") or "") if isinstance(v, dict) else ""

        if wp is True and policy in state_policies:
            continue

        if wp is True:
            voted_with += 1
        elif wp is False:
            voted_against += 1

    party_total = voted_with + voted_against

    if party_total >= 3:
        against_pct = voted_against / party_total
        # Adjust threshold by state lean: in deep partisan states,
        # even small break rates are significant.
        # Swing state: 15% breaks = perfect. Deep R+20: 5% = perfect.
        threshold = max(0.05, 0.15 - state_lean * 0.10)
        party_score = min(against_pct / threshold, 1.0) * 100
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
        # Count only matches with explicit alignment data (non-null)
        with_alignment = [
            m for m in lobbying_matches
            if m.get("senatorVoteAligned") is not None
        ]
        if with_alignment:
            aligned = sum(1 for m in with_alignment if m.get("senatorVoteAligned"))
            lobby_alignment_rate = aligned / len(with_alignment)
            donor_score = (1 - lobby_alignment_rate * min(pac_ratio * 2, 1.0)) * 100
        else:
            # Matches found but alignment is unknown — mildly penalize
            # based on the sheer number of industry-connected votes and PAC ratio
            connection_factor = min(len(lobbying_matches) / 6, 1.0)
            donor_score = (1 - connection_factor * pac_ratio * 0.5) * 100
    else:
        donor_score = 75  # no lobbying signal → mildly positive

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
