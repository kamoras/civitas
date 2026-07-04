"""
Score calculator — computes the five representation sub-scores from real data.

Higher score = better representation of constituents.
All scores are 0-100 where 100 = ideal representative, 0 = fully captured.

The five dimensions:
  1. Funding Independence       — donor concentration, PAC dependency, self-funding
  2. Promise Persistence        — campaign commitments kept vs broken + participation
  3. Constituent Alignment      — voting behavior vs what the seat's electorate
                                  expects (PVI-relative; stored/keyed as
                                  independentVoting for compatibility)
  4. Funding Diversity          — source traceability and industry diversification
  5. Legislative Effectiveness  — bill passage, cosponsorship leadership, volume

North star (owner, 2026-07): scores measure how well members REPRESENT
their constituents — not independence as an intrinsic virtue. Party-line
voting in a seat that elected that platform is representation.

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

Constituent Alignment: raw party-line break rates are misleading without
context. Following Carson et al. (2010, "The Electoral Costs of Party
Loyalty," AJPS 54:3), we use Cook PVI as a proxy for constituent
preferences and score each member against a seat-specific EXPECTED break
rate — a senator in a safe R+20 state voting with their party is
representing constituents, not failing at independence, while the same
loyalty in a swing state diverges from the median voter. This is the
delegate model of representation (Miller & Stokes 1963, "Constituency
Influence in Congress," APSR 57:1), with state partisan lean standing in
for issue-level constituent opinion. Donor independence via lobbying
matches follows Stratmann (2005) with the methodological caution from
Ansolabehere, de Figueiredo & Snyder (2003, "Why Is There So Little
Money in U.S. Politics?" JEP 17:1) that donation-vote correlations are
not causal evidence of influence.

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

Changes from v3 → v4 (score audit 2026-06):
- Funding Independence: rebuilt as PAC+outside (50%) / small-donor share
  (25%) / relative top-donor concentration (25%). The v3 concentration
  metric (top-10 / total raised) was structurally ≈0 for $50M+ fundraisers,
  so missing PAC data plus scale produced FI 97 for senators the ground
  truth expected ≤65. Candidate-affiliated transfers and self-funding are
  excluded from concentration (upstream, normalize_finance also stops
  listing transfers as donors — they made JFC users look captured by
  their own committees). Data fixes landed alongside: totalFromPACs now
  prefers FEC cycle totals over classifier-typed donor sums, and
  outside spending uses complete per-cycle Schedule E totals instead of
  a single 50-row page.
- Independent Voting: donor component reweighted 40%→25% when alignment
  is unknown (senatorVoteAligned has never been computed — every stored
  match is None), and the unknown-branch heuristic no longer divides the
  upstream-capped match count by 8 (a constant 1.0 that scored everyone
  80). Party-independence curve gains a 2% base rate and the state-lean
  discount is capped at a 20% full-credit break rate so safe-state party
  leaders with single-digit break rates no longer score as independents.
- Legislative Effectiveness: advancement counts substantive bills only
  (no simple/concurrent resolutions), stops double-counting laws, and
  drops "placed on calendar"/"cloture" credit; volume is per-congress
  (total/80) instead of career-log2. v3 saturated: LE mean was 81.6 with
  everyone maxing advancement and volume.
"""

import logging

logger = logging.getLogger(__name__)

# Bump when scoring formulas or their data inputs change in a way that
# shifts scores. Recorded on every ScoreSnapshot so trend charts can
# annotate methodology changes; keep frontend/src/lib/scoreVersions.ts
# in sync (it holds the human-readable changelog).
ALGORITHM_VERSION = "v4.3"

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
        # Key kept as "independentVoting" for storage/API compatibility;
        # since v4.2 this dimension is Constituent Alignment (see
        # _calc_constituent_alignment).
        "independentVoting": _calc_constituent_alignment(
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


def calculate_confidence(senator: dict) -> dict[str, str]:
    """Data-sufficiency confidence per dimension: "high" | "medium" | "low".

    Derived ONLY from how much source data backs each dimension — never
    from who the member is or what they scored. Published alongside the
    scores so sparse-data results aren't read with false precision: a
    Promise Persistence of 58 backed by two evaluable promises is a
    shrunk-to-prior guess, while the same number over twenty promises is
    a measurement. Thresholds are volume counts, identical for every
    member and both parties.
    """
    funding = senator.get("funding", {})
    voting_record = senator.get("votingRecord", {})
    promises = senator.get("campaignPromises") or []
    bills = senator.get("sponsoredBills") or []

    def grade(n: int, medium_at: int, high_at: int) -> str:
        if n >= high_at:
            return "high"
        if n >= medium_at:
            return "medium"
        return "low"

    has_funding = (funding.get("totalRaised", 0) or 0) > 0
    n_donors = len(funding.get("topDonors") or [])
    n_industries = len(funding.get("industryBreakdown") or [])
    all_votes = (voting_record.get("keyVotes") or []) + (
        voting_record.get("recentVotes") or []
    )
    n_party_votes = sum(
        1 for v in all_votes
        if isinstance(v, dict) and v.get("votedWithParty") is not None
    )
    n_evaluable = sum(
        1 for p in promises
        if isinstance(p, dict) and p.get("alignment") in ("kept", "partial", "broken")
    )

    return {
        "fundingIndependence": grade(n_donors, 3, 10) if has_funding else "low",
        "promisePersistence": grade(n_evaluable, 3, 8),
        "independentVoting": grade(n_party_votes, 10, 40),
        "fundingDiversity": grade(n_industries, 3, 6) if has_funding else "low",
        "legislativeEffectiveness": grade(len(bills), 3, 10),
    }


def _calc_funding_independence(funding: dict) -> int:
    """
    Funding Independence Score (0-100, higher = better).

    Three components, calibrated so the median senator scores ≈50 on each
    (empirical distributions from the 2026-06 audit of FEC cycle totals):

      1. PAC dependency (50%): PAC *share*, scaled by a penalty-only
         absolute-*dollar* factor.  Share: fraction of funding from PACs
         (FEC cycle totals, Schedule A) plus half-weighted outside
         spending (Schedule E independent expenditures supporting the
         candidate); median true PAC ratio is ≈28% (audit; consistent
         with Barber 2016, Table 2 ≈25–30%), so the ×2.0 multiplier puts
         the median near 50.  Dollar factor: ×1.0 at $0 down to ×0.5 at
         $4M+ (audit median $2.0M → ×0.75).  This corrects the mechanical
         scale bias of share alone (PAC checks are capped, individual
         money is not, so mega-campaigns dilute PAC money to
         invisibility) without letting modest absolute dollars rescue a
         fully PAC-funded small campaign.

      2. Small-donor share (25%): unitemized (<$200) contributions as a
         share of total receipts — the broadest-possible funding base and
         a well-established grassroots proxy (Bonica 2014; Malbin 2009).
         Full credit at 40% (audit p90 ≈43%); median ≈17% scores ≈43.

      3. Relative top-donor concentration (25%): top-10 external donors as
         a share of the full external donor pool (candidate-affiliated
         transfers and self-funding excluded — they are the candidate's
         own money, not donor influence).  The v3 metric divided top-10 by
         *total raised*, which is structurally near-zero for $50M+
         fundraisers (itemized employer-aggregated donations are always a
         tiny share of a mega-campaign), so large fundraisers scored
         90–100 regardless of how concentrated their donor base was.
         The relative pool ratio discriminates at any scale: audit median
         0.60 maps to 50, p10 (0.34) to ≈82, p90 (0.93) to ≈9.

    Academic rationale
    ------------------
    Barber (2016, Public Opinion Quarterly 80(S1), 225–249) anchors the
    PAC component.  Bonica (2014, AJPS 58(2), 367–386) supports both the
    small-donor proxy and the concentration signal.  Stratmann (2005,
    Public Choice 124(1–2), 135–156) confirms the linear PAC-dependency
    relationship in the relevant range.
    """
    total_raised = funding.get("totalRaised", 0)
    if not total_raised or total_raised == 0:
        return 50

    # Component 1: PAC dependency incl. outside spending (50% weight)
    pac_total = funding.get("totalFromPACs", 0)
    pac_ratio = pac_total / total_raised

    # Outside spending is not controlled by the candidate but signals
    # aligned-industry investment in the seat. Half-weight because it is
    # less direct than a contribution.
    outside_for = funding.get("outsideSpendingFor", 0) or 0
    if outside_for > 0:
        effective_outside = outside_for / (total_raised + outside_for) * 0.5
        pac_ratio = min(pac_ratio + effective_outside, 1.0)

    ratio_score = max(0.0, (1.0 - pac_ratio * 2.0)) * 100

    # Scale the share-based score by an absolute-dollar factor. Share
    # alone has a mechanical scale bias: PAC checks are capped (~$10K per
    # PAC per cycle) while individual money scales without bound, so a
    # $100M campaign dilutes millions of PAC dollars to a near-zero share
    # and scores as "independent" — the 2026-07 audit measured FI vs
    # log(total raised) at r=+0.68. The factor is penalty-only (it can
    # halve the share score but never raise it, so a small fully-PAC-
    # funded campaign still scores ~0). Calibrated to the audit
    # distribution of two-election-window PAC receipts: $0 → no penalty,
    # median $2.0M → ×0.75, $4M+ (p90 $4.4M) → ×0.5.
    volume_factor = 0.5 + 0.5 * max(0.0, 1.0 - pac_total / 4_000_000)
    pac_score = ratio_score * volume_factor

    # Component 2: small-donor share (25% weight)
    small_pct = funding.get("smallDonorPercentage", 0) or 0
    small_score = min(small_pct / 40.0, 1.0) * 100

    # Component 3: relative top-donor concentration (25% weight)
    external = sorted(
        (
            d for d in funding.get("topDonors", [])
            if d.get("type") not in ("CandidateAffiliated", "Self-Funded")
        ),
        key=lambda d: d.get("total", 0),
        reverse=True,
    )
    pool = sum(d.get("total", 0) for d in external)
    if len(external) >= 20 and pool >= 250_000:
        concentration = sum(d.get("total", 0) for d in external[:10]) / pool
        # Linear map: 0.20 → 100, 1.00 → 0 (median 0.60 → 50)
        concentration_score = max(0.0, min(1.0, (1.0 - concentration) / 0.8)) * 100
    else:
        # Too few itemized external donors to measure concentration.
        concentration_score = 50.0

    return clamp(
        pac_score * 0.50 + small_score * 0.25 + concentration_score * 0.25
    )


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


def _signed_state_alignment(state: str, party: str, effective_party: str | None = None) -> float:
    """How the senator's party aligns with their state's partisan lean.

    Returns -1.0 to +1.0, normalized at ±15 PVI points:
      +1.0 = deep safe seat for the senator's party,
       0.0 = swing state (or unknown party/state),
      -1.0 = state strongly leans toward the opposing party.

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
    return max(-1.0, min(lean / 15.0, 1.0))


def _calc_constituent_alignment(
    voting_record: dict,
    lobbying_matches: list[dict],
    funding: dict,
    state: str = "",
    party: str = "I",
) -> int:
    """
    Constituent Alignment Score (0-100, higher = better). v4.2 rebuild of
    the former Independent Voting dimension (stored under the same key).

    Purpose shift (2026-07): the dimension measures how a member's voting
    compares to what their state elected them to do — NOT raw defection.
    The v4.1 curve treated any break rate ≤3% as a hard floor of 20,
    which pinned 73/100 senators into a 26–38 band (live mean 34): it
    called the median elected official a failure for party-line voting
    that, in a safe seat, IS constituent representation.

    Two components:
      1. Seat-relative vote alignment (75%, or 60% when real
         donor-alignment data exists): the member's contested-vote break
         rate compared to an EXPECTED break rate derived from state
         partisan lean (Cook PVI):
           aligned safe seat → ~3% expected (base-rate dissent),
           swing seat        → ~8%,
           opposed seat      → up to ~20% (a member whose party opposes
                               the state median should cross more often).
         Matching expectation scores ~50 ("typical partisan for this
         seat"). Crossing is NOT rewarded for its own sake: surplus
         crossing earns credit only where it plausibly moves toward the
         state's median voter — full credit in opposed/swing seats,
         shrinking to a near-neutral 0.25 in deep aligned seats, where
         surplus defection is ideology rather than constituent
         representation (an undiscounted credit once let a 9%-break
         party leader score ≈72; see 2026-06 audit). Hyper-loyalty in a
         swing or opposed seat drifts down to at most 25 — below
         neutral, but never the old failure-grade floor.

      2. Donor independence (25%/40%): unchanged from v4.1 — based on
         donor-vote connection matches, with the visibility-scaled
         baseline. senatorVoteAligned is only populated when a real
         alignment computation exists; when it is None for every match,
         the donation-share heuristic gets the reduced 25% weight.

    Removed in v4.2: the "state-relevant policy" exemption that skipped
    party-line votes on policy areas related to the member's TOP DONOR
    industries. Donor industries are not a proxy for state interests —
    that exemption shielded exactly the votes most suspect for capture,
    and gave bigger fundraisers more exemptions. Seat-relative
    expectations now carry the constituent-representation adjustment.
    """
    effective_party = voting_record.get("effectiveParty", party)
    alignment = _signed_state_alignment(state, party, effective_party=effective_party)

    all_votes = (voting_record.get("keyVotes") or []) + (
        voting_record.get("recentVotes") or []
    )

    voted_with = 0.0
    voted_against = 0.0
    for v in all_votes:
        wp = v.get("votedWithParty") if isinstance(v, dict) else None
        if wp is None:
            continue

        # Multi-area alignment weight: when a bill spans multiple policy
        # areas, some may align with the senator's party and some may not.
        # The weight reflects the proportion of areas that lean toward the
        # overall party alignment. A vote on a 60/40 D-leaning bill is
        # less informative about party loyalty than a vote on a 100% D bill.
        # This implements the weighted-expert aggregation framework from
        # Clemen (1989, "Combining Forecasts," Intl J Forecasting 5:4).
        # NOTE on composition: nominations are ~43% of party-labeled votes
        # in the current Senate (2026-07 audit). They are deliberately
        # weighted the same as legislation — an experiment down-weighting
        # them ×0.5 inflated the score for members whose loyalty
        # concentrates on nominations while their breaks are legislative
        # (a party leader jumped from 55 to 68 and out of the
        # ground-truth range). Confirmation votes are genuine, whipped
        # party-line tests.
        weight = 1.0
        if isinstance(v, dict):
            raw_weight = v.get("partyAlignmentWeight", 0.0)
            if raw_weight > 0.0:
                weight = raw_weight

        if wp is True:
            voted_with += weight
        elif wp is False:
            voted_against += weight

    party_total = voted_with + voted_against

    # Expected break rate for the seat. BASE_RATE: CQ party-unity data
    # puts typical dissent at 3-5%; every senator strays occasionally on
    # procedural or home-state matters.
    BASE_RATE = 0.03
    if alignment >= 0:
        expected = BASE_RATE + 0.05 * (1.0 - alignment)
    else:
        expected = 0.08 + 0.12 * (-alignment)

    if party_total >= 3.0:
        against_pct = voted_against / party_total
        if against_pct >= expected:
            # Crossing beyond the seat's expectation earns credit ONLY to
            # the extent it plausibly moves toward the state's median
            # voter: full credit in opposed/swing seats (the median sits
            # across or between the parties), shrinking to 0.25 in deep
            # aligned seats — there, surplus crossing moves AWAY from the
            # state median and is not itself representation. The small
            # residual (rather than zero or a penalty) reflects that we
            # cannot observe WHICH WAY a break points relative to state
            # opinion, so safe-seat crossing is treated as near-neutral,
            # not as virtue and not as defiance. Saturates at +25pts of
            # surplus break rate.
            surplus = against_pct - expected
            credit = max(0.25, 1.0 - 0.75 * max(alignment, 0.0))
            party_score = 50.0 + 50.0 * min(surplus / 0.25, 1.0) * credit
        else:
            # More loyal than the seat expects: drift below neutral by up
            # to 25 points, scaled in absolute break-rate points so an
            # aligned-safe-seat loyalist (expected ≈3%, actual ≈1%) stays
            # near 47 while a swing-state hyper-loyalist (expected ≈8%,
            # actual ≈1%) lands near 33.
            deficit = expected - against_pct
            party_score = 50.0 - 25.0 * min(deficit / 0.10, 1.0)
    else:
        party_score = 50

    # Donor independence via donor-vote connection matches
    total_raised = funding.get("totalRaised", 0)
    pac_ratio = (
        funding.get("totalFromPACs", 0) / total_raised
        if total_raised > 0
        else 0
    )

    # Baseline reflects data visibility: a small operation with no visible
    # donor-vote connections is plausibly independent; a $100M+ fundraiser
    # with no visible connections almost certainly has a data gap — at
    # that scale influence flows through bundled donations and outside
    # spending rather than anything our matching can see.
    if total_raised >= 100_000_000:
        base_donor = 50.0
    elif total_raised >= 50_000_000:
        base_donor = 60.0
    elif total_raised >= 10_000_000:
        base_donor = 67.0
    else:
        base_donor = 72.0

    with_alignment = [
        m for m in lobbying_matches
        if m.get("senatorVoteAligned") is not None
    ] if lobbying_matches else []

    if with_alignment:
        # Real alignment data: weighted alignment rate, where consensus
        # votes (bipartisan majority) carry much less weight as a signal
        # of capture than divided votes.
        weighted_aligned = 0.0
        total_weight = 0.0
        for m in with_alignment:
            weight = 0.2 if m.get("isConsensusVote") else 1.0
            total_weight += weight
            if m.get("senatorVoteAligned"):
                weighted_aligned += weight

        lobby_alignment_rate = weighted_aligned / total_weight if total_weight > 0 else 0
        # Floor at 0.25 so lobbying alignment always carries some penalty
        # even when direct PAC contributions are zero.
        pac_amplifier = max(0.25, min(pac_ratio * 2, 1.0))
        donor_score = (1 - lobby_alignment_rate * pac_amplifier) * 100
        donor_weight = 0.40
    elif lobbying_matches:
        # Matches exist but alignment is unknown (senatorVoteAligned is
        # None for all of them — the current state of the pipeline data).
        # Penalize the visibility baseline by how much money the matched
        # donors represent and how many matches involve divided votes.
        # NOTE: do not use raw match count against a fixed divisor — the
        # match list is capped upstream, which made count/8 a constant.
        total_lobby_donations = sum(
            m.get("donationToSenator", 0) for m in lobbying_matches
        )
        donation_ratio = (
            total_lobby_donations / total_raised if total_raised > 0 else 0
        )
        non_consensus_share = sum(
            1 for m in lobbying_matches if not m.get("isConsensusVote")
        ) / len(lobbying_matches)

        penalty = min(donation_ratio * 4, 0.20) + 0.08 * non_consensus_share
        donor_score = base_donor * (1 - penalty)
        donor_weight = 0.25
    else:
        donor_score = base_donor
        donor_weight = 0.25

    return clamp(
        party_score * (1 - donor_weight) + donor_score * donor_weight
    )


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

    if not industry_breakdown or not total_raised:
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

      1. Bill advancement rate (40%): fraction of sponsored *substantive*
         bills (S/HR/joint resolutions) that became law, passed a chamber,
         or were ordered reported.  Simple and concurrent resolutions are
         excluded: commemorative resolutions are routinely "agreed to" and
         counting them (as v3 did, together with double-counting laws)
         saturated this component — the 2026-06 audit measured a real
         substantive advancement median of 2.5% (p90 5.3%) vs the v3
         inflated median of 9.4%. "Placed on calendar" and "cloture" are
         no longer credited either (Rule XIV calendar placement skips
         committee and signals nothing about advancement). Full credit
         stays at 5%, which now sits just above the p90 of the honest
         rate, matching Volden & Wiseman (2014).

      2. Legislative leadership (30%): PageRank score from the
         cosponsorship network (Brin & Page 1998, computed in
         sponsorship_analysis.py). Senators whose bills attract
         cosponsors from influential colleagues score higher.

      3. Sponsorship volume (30%): bills introduced *per congress served*,
         linear with full credit at 80/congress.  The v3 metric applied
         log2(total career bills)/log2(100), but the bill lists span whole
         careers (audit median: 338 bills over 7 congresses), so nearly
         every senator maxed the component.  Per-congress rates: senate
         median ≈42 (→53), p10 ≈25 (→31), p90 ≈69 (→86).

    All components apply Bayesian shrinkage toward 50 when data is
    sparse, preventing extreme scores from thin evidence.
    """
    if not sponsored_bills:
        # No bill data at all — neutral 50 per the design principle that
        # missing data never scores as good or bad. When cosponsorship
        # leadership exists, blend it in with neutral advancement/volume
        # priors so a well-networked senator without bill data sits
        # coherently above (not below) the fully-unknown case.
        if leadership_score and leadership_score > 0:
            lp = min(leadership_score, 1.0) * 100
            return clamp(50 * 0.40 + lp * 0.30 + 50 * 0.30)
        return 50

    n_bills = len(sponsored_bills)

    # Component 1: advancement rate over substantive bills only
    SUBSTANTIVE_TYPES = {"s", "hr", "sjres", "hjres"}
    substantive = [
        b for b in sponsored_bills
        if (b.get("billType") or "").lower() in SUBSTANTIVE_TYPES
    ]

    became_law = 0
    advanced = 0
    for bill in substantive:
        if bill.get("isLaw"):
            became_law += 1
        else:
            action = (bill.get("latestAction") or "").lower()
            if any(kw in action for kw in [
                "passed", "agreed to", "ordered to be reported",
            ]):
                advanced += 1

    n_sub = len(substantive)
    if n_sub > 0:
        success_rate = (became_law + advanced) / n_sub
        advancement_raw = min(success_rate / 0.05, 1.0) * 100
        # Shrink toward 50 with few bills
        advancement_conf = min(n_sub / 10, 1.0)
        advancement_score = advancement_raw * advancement_conf + 50 * (1 - advancement_conf)
    else:
        # Only resolutions sponsored — no substantive signal.
        advancement_score = 50.0

    # Component 2: leadership score from cosponsorship PageRank
    if leadership_score is not None and leadership_score > 0:
        # Raw score is 0-1 from PageRank (percentile-like). Scale to 0-100.
        leadership_pct = min(leadership_score, 1.0) * 100
    else:
        leadership_pct = 40  # below neutral — we expected data

    # Component 3: sponsorship volume per congress served
    congresses = {b.get("congress") for b in sponsored_bills if b.get("congress")}
    per_congress = n_bills / max(len(congresses), 1)
    volume_raw = min(per_congress / 80.0, 1.0) * 100

    return clamp(
        advancement_score * 0.40
        + leadership_pct * 0.30
        + volume_raw * 0.30
    )
