"""
Score calculator — computes the five representation sub-scores from real data.

Higher score = better representation of constituents.
All scores are 0-100 where 100 = ideal representative, 0 = fully captured.

The scored dimensions (SCORE_WEIGHTS) — Promise Persistence (removed
v6.0) and Funding Diversity (folded into Funding Independence, v6.5)
still run and still store their score_* columns, just excluded from
the weighted sum below:
  1. Funding Independence       — donor concentration, PAC dependency, self-funding,
                                  source breadth, industry diversification (v6.5: incl.
                                  the former Funding Diversity dimension)
  2. Constituent Alignment      — voting behavior vs what the seat's electorate
                                  expects (PVI-relative; stored/keyed as
                                  independentVoting for compatibility)
  3. Legislative Effectiveness  — bill passage, cosponsorship leadership, volume

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
Funding Independence: donor-influence framing follows Bonica (2014,
"Mapping the Ideological Marketplace," AJPS 58:2) and Barber (2016,
"Representing the Preferences of Donors, Partisans, and Voters in the
U.S. Senate," POQ 80(S1)) — both papers are about donor/candidate
ideological positioning, not donor-concentration statistics by rank
(verified by full-text search, 2026-07: neither paper contains a PAC-
ratio or top-N-donor-concentration table). The specific calibration
targets below are this platform's own live empirical audits, not
numbers reproduced from either paper — see _calc_funding_independence's
own "Academic rationale" note for the fuller account, including why the
PAC multiplier is now chamber-specific (Senate ×3.2, House ×1.35;
scripts/audit_pac_ratio.py) rather than one shared value. Top-donor
concentration is calibrated so a 20%-of-pool share scores 100 and the
platform's own empirical median (60%) scores 50 — Parmigiani (2025,
"Campaign contributions and legislative behavior," Journal of Public
Economics 243) reports the same top-decile-donor-share metric at a 47%
mean in a different population/period, real independent corroboration
that concentration in the 40-60%+ range is the normal pattern (not a
number copied from that paper — our own audit is the calibration
source). Prior v1 multipliers (1.3×, 1.5×) compressed variation into the
61–94 range; recalibration creates a symmetric distribution around the
empirical sample median.

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

Funding Diversity: the inverse Herfindahl-Hirschman Index (HHI) applied
to industry-level donation shares — concentrated funding from a single
industry suggests potential regulatory capture, while broad funding
suggests diverse constituent support — has real, on-topic precedent in
the campaign-finance literature: Parmigiani (2025, "Campaign
contributions and legislative behavior," Journal of Public Economics
243) computes an HHI of contribution concentration per legislator per
cycle as a robustness measure, the same statistic this component
computes, just bucketed by donor industry rather than by individual
donor. Rhoades (1993, "The Herfindahl-Hirschman Index," Fed Reserve
Bulletin 79) is kept as a secondary reference for HHI's general
mechanics — it is a banking/antitrust note with no campaign-finance
content of its own, so it explains the statistic but not why it applies
here; Parmigiani does. Known limitation, disclosed rather than fixed in
this pass: Parmigiani's own discussion argues top-share measures are
arguably better suited than HHI/Gini to the long-tailed distributions
donor data actually has — the same reason income-inequality research
prefers top-1%-share over Gini. Whether Funding Diversity should move
from inverse-HHI to a top-share-based measure is a real open question,
flagged here as a fast-follow rather than decided in this pass — it
would move every senator's score and deserves its own dedicated
evaluation.

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
- Parmigiani, A. (2025). Journal of Public Economics, 243, 105319.

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
  is unknown (senatorVoteAligned is always None — see structural note
  below), and the unknown-branch heuristic no longer divides the
  upstream-capped match count by 8 (a constant 1.0 that scored everyone
  80). Party-independence curve gains a 2% base rate and the state-lean
  discount is capped at a 20% full-credit break rate so safe-state party
  leaders with single-digit break rates no longer score as independents.

  senatorVoteAligned structural note (2026-07 audit): computing it
  honestly needs to know whether a donor's industry *wanted* a given
  bill to pass or fail, and no ingested source (LDA filings —
  fetch/lda.py — only carry aggregate spend, not per-bill positions)
  discloses that. The only way to fill it in without that data is a
  hand-authored industry→stance mapping ("PHARMA opposes price
  controls," etc.), which both risks being wrong (a bill's effect on
  an industry depends on its actual content, not just which way its
  policy area points) and is exactly the kind of authored political
  conclusion this platform's scores are designed never to contain —
  see the platform's no-hardcoded-hints principle. The 25%-weight
  donation-share/non-consensus-vote-share formula is the honest
  ceiling absent a real per-bill position data source — this is the
  ONLY donor-independence branch (removed 2026-07-15: a dead 40%-weight
  "real alignment data" branch, gated on senatorVoteAligned being
  non-None, could never execute since the sole producer of
  lobbyingMatches hardcodes that field to None; kept as unreachable
  scaffolding for a data source that doesn't exist and wasn't planned).
  If a real per-bill position source is ever ingested, add the richer
  branch back then, not speculatively now.
- Legislative Effectiveness: advancement counts substantive bills only
  (no simple/concurrent resolutions), stops double-counting laws, and
  drops "placed on calendar"/"cloture" credit; volume is per-congress
  (total/80) instead of career-log2. v3 saturated: LE mean was 81.6 with
  everyone maxing advancement and volume.
- v5.8 (2026-07): "current term" redefined as the current congress for
  votes/bills/effectiveness (fetch/congress.py, senate_pipeline.py,
  house_pipeline.py) and as the most recent election for funding
  (fetch/fec.py select_recent_elections) — a member who did great work a
  decade ago and has coasted since no longer gets credit for it every
  run. See AGENTS.md "current term" for the full rationale, including
  why funding uses a different rule than votes/bills.
- v5.9 (2026-07): Legislative Effectiveness's volume component (30%
  weight) now excludes simple/concurrent resolutions, matching the filter
  the advancement component (40%) already had since v4. A real member's
  National Mushroom Day resolution — ceremonial, agreed to without debate
  by unanimous consent — was inflating their volume score even though the
  same resolution correctly earned zero advancement credit; the two
  components had silently drifted out of sync. Volume ceilings
  recalibrated to the substantive-only distribution (resolutions had
  inflated the raw per-congress p90 by ~14-16%).
- v5.10 (2026-07): Two Promise Persistence fixes, prompted by the same
  audit that found the Mushroom Day bug (nobody was scoring above ~68
  overall; PP had failed its own 8.0 population-stdev floor every night
  since 2026-07-11). First, positions_from_sponsored_bills() (derives
  "promises" from a member's own bills when platform text is sparse) was
  feeding in ceremonial resolutions unfiltered — a real production
  example was a sorority-anniversary resolution converted into a
  "promise," then inevitably marked unclear. Now excludes the same
  SUBSTANTIVE_BILL_TYPES resolutions do. Second — the deeper finding —
  the real average is ~0.5 evaluable promises/senator (59/100 have
  zero), far below the ~2.7avg the v5.1/v5.3 pseudocount resize assumed;
  this is a genuine data-scarcity floor (see the module docstring at
  PRIOR_PSEUDOCOUNT's definition), not primarily a threshold bug.
  PRIOR_PSEUDOCOUNT resized 6->3 to restore population spread at this
  harsher real sample size. Both changes shadow-tested against live
  data; population stdev restored from ~5.0 to ~9.2. Why evidence volume
  is this low in the first place — LLM gray-zone gate underperforming,
  vs. genuinely few promises naming legislation that gets an actual
  floor vote within one congress — is flagged as still open, not solved
  by this pass.
- v5.11 (2026-07): Redesigned donor-vote "lobbying connection" detection
  (detect_donor_vote_connections, policy_alignment.py), which feeds
  Constituent Alignment's donor-influence penalty (senatorVoteAligned is
  always None with today's data sources, so every detected match applies
  a real penalty via the donation-ratio/non-consensus-share path — see
  _calc_constituent_alignment). User report: essentially every vote near
  any donor was flagging as a "connection" regardless of size or topical
  relevance. Root cause was two uncalibrated pieces: a 0.35 raw-text
  similarity threshold (far below the noise floor for this text pair
  type) and no donor-magnitude gate at all. Replaced with a two-stage
  gate: (1) donor's INDUSTRY must be a substantial share (>=25%) of the
  member's CLASSIFIED-industry-only funding — not total raised, which is
  mostly small-dollar/non-industry money by construction (measured: real
  median top-industry share is 1.5% of total raised vs 32% of
  classified-industry-only money; the latter is the actually meaningful
  denominator), (2) the industry's already-classified policy-area
  similarity to the vote must clear 0.75 (measured: genuine matches like
  TECH-vs-TECH score 0.87-0.93, cross-category noise floor sits at
  mean 0.66/p90 0.71 — a clean, calibrated gap, replacing the old raw
  free-text embedding approach). Expect fewer, more meaningful lobbying
  matches, which will reduce the donor-influence penalty for senators
  whose previous matches were mostly false positives.
- v5.12 (2026-07): Legislative Effectiveness's leadership component
  (30% weight, cosponsorship PageRank) previously defaulted to a flat 40
  when no leadership_score existed — an explicit below-neutral punitive
  value ("below neutral — we expected data"), and otherwise took the raw
  PageRank percentile at full face value regardless of tenure. PageRank
  centrality is structurally a function of network size, which takes
  years to build — a freshman senator's near-zero raw percentile
  reflects time, not ineffectiveness. A 2026-07 leaderboard review found
  this the dominant driver of a real tenure-vs-LE correlation (r=+0.24
  across the Senate; freshmen <=2yrs averaged LE=29.5 vs veterans
  >=10yrs at 54.1), directly contradicting this project's own "seniority
  alone is never penalized" design principle. Fixed: missing data now
  defaults to neutral 50 (matching every other component's treatment of
  missing data), and the raw percentile is shrunk toward neutral with a
  confidence factor scaled to a full 6-year Senate term — a first-year
  member's leadership score sits close to neutral, a 6+-year member's
  reflects their full raw percentile. Shadow-tested: freshman/veteran LE
  gap narrows from 24.6 to 19.1 points (r: +0.241 -> +0.164) — a real,
  partial improvement, not a full fix. The remaining gap most likely
  traces to Components 1 (advancement — bills genuinely take time to
  move regardless of a sponsor's effectiveness) and 3 (volume —
  accumulates over congresses served), which this pass did not touch;
  flagged as a candidate follow-up, not solved here.

Changes from v5 → v6.0 (2026-07): Promise Persistence removed as a scored
dimension entirely, not just recalibrated again. v5.10 found the real
average was ~0.5 evaluable promises/senator; a follow-up measurement found
it had gotten worse, not better — 0 of 100 senators reach even "medium"
confidence per calculate_confidence()'s own thresholds (mean 0.3 evaluable
promises, 76% with zero), collapsing the dimension to near-pure prior for
effectively the entire Senate. This is the fourth attempt at this dimension
(v5, v5.1/v5.3, v5.10) without resolving the underlying gap: real campaign
promises are generic platform language ("Expand Medicare coverage"), and
embedding-based matching against specific vote/bill text structurally
can't bridge that register — see policy_alignment.compute_promise_vote_
alignment's docstring. _calc_promise_persistence keeps running and
score_promise_persistence keeps being stored (still real, still displayed
as raw promise kept/broken/partial data on profile pages) — it's just
excluded from SCORE_WEIGHTS and the weighted overall score. Its 25%
weight redistributed proportionally across the other four (see
config_definitions.SCORE_WEIGHTS's docstring for the exact numbers).

Changes from v6.0 → v6.1 (2026-07): Funding Diversity's industry-
concentration signal had a flat step-function fallback (65 if
small_frac > 0.3 else 50) for senators with too little classified-
industry money to compute a meaningful HHI. A population audit found
this silently capped the dimension's maximum at 69 for the entire
Senate — Bernie Sanders (63% small-donor money, the most grassroots-
funded senator in the dataset) scored exactly 69, because his 0.28%
classified-industry share triggered the flat 65 regardless of how
overwhelmingly diversified his actual funding was. The flat fallback
treated "just over the threshold" and "essentially all small-dollar"
identically. Replaced with a fallback that scales continuously with
small_frac (50 + small_frac*50 — still 50 at small_frac=0, still 65 at
exactly the old 0.3 threshold, so continuous with prior behavior there,
but now able to reach 100 for a hypothetical fully-small-dollar
campaign instead of capping at 65).

Changes from v6.4 → v6.5 (2026-07): Funding Diversity folded into Funding
Independence as one dimension; Donor Independence removed from Constituent
Alignment. Both changes respond to the same underlying finding
(config_definitions.SCORE_WEIGHTS's r=0.72 audit): Funding Independence
and Funding Diversity measure the same underlying funding-profile signal,
and Constituent Alignment's Donor Independence component measured a
close cousin of it (both keyed off total_raised and donor-industry
concentration) while itself degrading to one of four fixed values for
85% of senators (ground_truth.py). Funding Diversity's two signals
(source breadth, industry concentration) are now two additional
components inside Funding Independence's weighted score, at internal
weights equal to their PRIOR contribution to the overall score divided
by the merged dimension's new weight (0.20 and 0.13 respectively,
summing to the new 0.33) — a linear renormalization, not a fresh
judgment call: the continuous math is provably identical to the pre-
merge weighted sum. clamp() rounds to an int, though, and the merge
moves from two independent roundings (FI, then FD) to one — so a given
senator's overall score can shift by roughly half a point from that
reorganization, not from any new weighting decision. score_funding_
diversity keeps being computed and stored
exactly as before (still real, same "kept independently visible" pattern
as promisePersistence's v6.0 removal) — only SCORE_WEIGHTS and the
Funding Independence breakdown change. Donor Independence's freed 25%
goes entirely to Constituent Alignment's seat-relative vote alignment
component (coalition breadth keeps its own independently-justified 20%)
rather than being redistributed to prop up a three-way split that no
longer has three genuinely distinct signals.
"""

import logging
import math

from app.models import PromiseAlignment

logger = logging.getLogger(__name__)

# Bump when scoring formulas or their data inputs change in a way that
# shifts scores. Recorded on every ScoreSnapshot so trend charts can
# annotate methodology changes; keep frontend/src/lib/scoreVersions.ts
# in sync (it holds the human-readable changelog).
#
# v5.12 -> v6.0 (2026-07): removed promisePersistence as a scored dimension
# (see config_definitions.SCORE_WEIGHTS's docstring for the empirical
# finding) and reweighted the remaining four. Major-version bump, matching
# this file's own precedent for dimension-composition changes (v2 -> v3
# folded Accessibility into Promise Persistence; this removes Promise
# Persistence itself).
#
# v6.1 -> v6.2 (2026-07): Legislative Leadership's PageRank now weights
# cosponsorship edges by whether the underlying bill advanced (see
# sponsorship_analysis.ENACTED_EDGE_WEIGHT/ADVANCED_EDGE_WEIGHT/
# STALLED_EDGE_WEIGHT) instead of a flat weight per cosponsorship,
# addressing an external review's finding that message-bill cosponsorship
# was indistinguishable from real legislative collaboration.
#
# v6.2 -> v6.3 (2026-07): Funding Independence's small-donor share
# component is now state-population-relative for senators instead of a
# flat 40%-cap, addressing a population audit that found small-state
# senators averaged 40 vs. 59.5 for large-state senators — driven almost
# entirely by small-donor fundraising capacity (structural: bigger states
# have larger natural donor pools and more national media exposure), not
# by PAC-money differences (flat-to-higher in small states). See
# _state_small_donor_baseline/_small_donor_capacity_score. House
# unaffected — districts are population-equalized by design.
#
# v6.3 -> v6.4 (2026-07, academic-fidelity audit): a citation review found
# several claims didn't hold up under full-text verification, and one
# genuine miscalibration behind them:
#   - FI PAC dependency: Barber (2016) and Bonica (2014) don't contain any
#     PAC-ratio or donor-concentration calibration figure (verified by
#     full-text search) — the ×2.0 multiplier was this platform's own
#     empirical calibration mislabeled as paper-derived. Re-auditing it
#     live (scripts/audit_pac_ratio.py) found the real median PAC ratio
#     is chamber-specific and far from the old shared assumption: Senate
#     15.7%, House 37.1%, not a shared ≈28%. Multiplier is now
#     chamber-specific (×3.2 Senate, ×1.35 House).
#   - FI top-donor concentration: the top-of-file docstring's "20% scores
#     50" claim was stale and didn't match the actual formula (20%→100,
#     60% empirical median→50) — fixed to match reality. Bonica (2014)
#     replaced with Parmigiani (2025, J. Public Econ. 243) for this claim
#     — a real, on-topic paper reporting the same top-decile-share metric.
#   - FD industry concentration: added Parmigiani (2025) as the primary
#     campaign-finance-specific HHI citation (Rhoades 1993 kept as a
#     secondary general-mechanics reference) and disclosed a real,
#     sourced limitation — top-share/Gini measures may be better suited
#     than HHI to donor data's long tail.
#   - Legislative Effectiveness: rebuilt to actually implement Volden &
#     Wiseman (2014)'s real methodology (significance-weighted,
#     cumulative-stage credit) instead of the three independent
#     components (advancement rate/leadership/volume) that cited them
#     without following their approach. Two disclosed departures: only
#     2 of V&W's 3 significance tiers are implemented (no proxy for their
#     hand-curated "significant legislation" tier), and normalization uses
#     an expected-vs-actual credit comparison (reusing this file's own
#     established pattern) instead of V&W's live per-term population-mean
#     ratio, since this platform's data is career-cumulative, not
#     single-term. See _les_component_score and the module comment above
#     _LES_STAGE_ORDER for the full account.
#
# v6.4 -> v6.5 (2026-07): Funding Diversity folded into Funding
# Independence as one scored dimension (SCORE_WEIGHTS: fundingDiversity
# removed, fundingIndependence 0.20 -> 0.33); Donor Independence removed
# from Constituent Alignment, its freed 25% going to seat-relative vote
# alignment. Both respond to the same r=0.72 correlated-signal finding
# (config_definitions.SCORE_WEIGHTS's docstring). See the top-of-file
# "Changes from v6.4 -> v6.5" note for the full account.
ALGORITHM_VERSION = "v6.5"

# weight-key -> Senator/Representative score_* attribute name. Both models
# use identical score_* column names, so one map covers both entity types.
_SCORE_FIELD_MAP: dict[str, str] = {
    "fundingIndependence": "score_funding_independence",
    "promisePersistence": "score_promise_persistence",
    "independentVoting": "score_independent_voting",
    "fundingDiversity": "score_funding_diversity",
    "legislativeEffectiveness": "score_legislative_effectiveness",
}


def compute_overall_score(entity) -> float:
    """Weighted overall score from a scored Senator/Representative row.

    Shared by senate_pipeline.py's and house_pipeline.py's daily
    ScoreSnapshot recorders — both weight the same score_* columns by the
    same config_definitions.SCORE_WEIGHTS, previously copy-pasted. Sums
    dynamically over SCORE_WEIGHTS.items() rather than naming each
    dimension, so a weight-table change (e.g. removing a dimension) can't
    silently desync this formula from the config again — that gap was
    exactly what made the promisePersistence removal above require an
    audit of 7 independently hardcoded copies instead of touching one file.
    """
    from app.config_definitions import SCORE_WEIGHTS

    overall = sum(
        getattr(entity, _SCORE_FIELD_MAP[key], 0) * weight
        for key, weight in SCORE_WEIGHTS.items()
    )
    return round(overall, 2)


NON_INDUSTRY_CODES = {"OTHER", "SMALL_DONORS", "LARGE_INDIVIDUAL", "POLITICAL", "UNCLASSIFIED"}

# Substantive legislation only — excludes simple/concurrent resolutions
# (sres/hres/sconres/hconres), which are routinely ceremonial ("National
# Mushroom Day", a sorority-anniversary resolution) and agreed to without
# debate. Used wherever "did this member do real legislative work" needs
# to exclude commemorative content: Legislative Effectiveness (both
# advancement and volume) and, via cross_reference.py, promise derivation
# from a member's own sponsored bills.
SUBSTANTIVE_BILL_TYPES = {"s", "hr", "sjres", "hjres"}

# A bill's latestAction text is free-form Congress.gov prose, not a
# controlled vocabulary — these substrings are the ones that reliably
# appear across "passed the chamber," "agreed to" (resolutions/unanimous
# consent), and "ordered to be reported" (cleared committee) language.
# Shared with sponsorship_analysis.py's cosponsorship-edge weighting, so
# "did this bill advance" means the same thing in both Legislative
# Effectiveness and Legislative Leadership.
_ADVANCEMENT_ACTION_KEYWORDS = ("passed", "agreed to", "ordered to be reported")

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

_state_population_cache: dict[str, float] | None = None


def _state_population() -> dict[str, float]:
    """State population in millions, 2020 Census (state abbreviation ->
    float). Used only by _state_small_donor_baseline (Funding
    Independence's small-donor component, Senate only — see that
    function's docstring); DC/territories have no voting senators and are
    intentionally omitted, falling back to the national mean baseline.

    Ingested from Wikipedia into app/data/state_population.json;
    regenerate with scripts/fetch_state_population.py (also the single
    source scripts/fetch_state_small_donor_baseline.py's regression audit
    reads, so the two can't silently drift apart the way a second
    hardcoded copy could). Population, unlike Cook PVI, doesn't shift
    enough between censuses to need per-cycle regeneration — rerun after
    the 2030 census.
    """
    global _state_population_cache
    if _state_population_cache is None:
        import json
        import pathlib
        path = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "state_population.json"
        try:
            _state_population_cache = {
                k: float(v) for k, v in json.loads(path.read_text())["states"].items()
            }
        except Exception:
            logger.warning("state_population.json unavailable — small-donor baseline will use the national mean for every state")
            _state_population_cache = {}
    return _state_population_cache


def clamp(value: float, min_val: int = 0, max_val: int = 100) -> int:
    """Clamp a value to [min_val, max_val] and round to int."""
    return max(min_val, min(max_val, round(value)))


_district_pvi_cache: dict[str, int] | None = None


def _district_pvi() -> dict[str, int]:
    """Per-district Cook PVI ("ST-N" -> signed int, positive = R lean).

    Ingested from each district's Wikipedia infobox into
    app/data/district_pvi.json; regenerate with
    scripts/fetch_district_pvi.py (all 435 seats incl. vacancies;
    ingestion gates documented there). State PVI is the wrong seat
    expectation for House members in
    split states — a D+19 urban district in a red state was scored as an
    "opposed seat" whose member should cross party lines ~20% of the
    time, when the seat actually elected exactly that platform.
    """
    global _district_pvi_cache
    if _district_pvi_cache is None:
        import json
        import pathlib
        path = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "district_pvi.json"
        try:
            _district_pvi_cache = {
                k: int(v) for k, v in json.loads(path.read_text())["districts"].items()
            }
        except Exception:
            logger.warning("district_pvi.json unavailable — falling back to state PVI")
            _district_pvi_cache = {}
    return _district_pvi_cache


def calculate_scores(senator: dict) -> dict:
    """
    Calculate the five representation sub-scores from real data.

    Returns:
        Dict with the five representationScore sub-fields.
    """
    voting_record = senator.get("votingRecord", {})
    funding = senator.get("funding", {})
    lobbying_matches = senator.get("lobbyingMatches", [])

    return {
        "fundingIndependence": _calc_funding_independence(
            funding, senator.get("state", ""), senator.get("district"),
        ),
        "promisePersistence": _calc_promise_persistence(
            voting_record,
            senator.get("party", "I"),
            senator.get("campaignPromises", []),
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
            bipartisanship=senator.get("bipartisanshipScore"),
            district=senator.get("district"),
        ),
        "fundingDiversity": _calc_funding_diversity(funding),
        "legislativeEffectiveness": _calc_legislative_effectiveness(
            senator.get("sponsoredBills", []),
            senator.get("leadershipScore"),
            party=voting_record.get("effectiveParty") or senator.get("party", "I"),
            years_in_office=senator.get("yearsInOffice"),
        ),
    }


def explain_scores(senator: dict) -> dict:
    """Full derivation for each currently-scored dimension — mirrors
    calculate_scores() but returns the component-level breakdown each
    _x_core() function already computes, instead of just the final int.

    On-demand only (called from a dedicated API endpoint when a user
    expands a score's "show the math" panel); never called from the
    nightly pipeline. Promise Persistence is omitted — it was removed as
    a scored dimension in v6.0 and has no score bar to attach a
    breakdown to.

    Shared by both senators and representatives, same as calculate_scores
    above — both entity types assemble the same dict shape.
    """
    voting_record = senator.get("votingRecord", {})
    funding = senator.get("funding", {})
    lobbying_matches = senator.get("lobbyingMatches", [])

    return {
        "fundingIndependence": _funding_independence_core(
            funding, senator.get("state", ""), senator.get("district"),
        ),
        "independentVoting": _constituent_alignment_core(
            voting_record,
            lobbying_matches,
            funding,
            senator.get("state", ""),
            senator.get("party", "I"),
            bipartisanship=senator.get("bipartisanshipScore"),
            district=senator.get("district"),
        ),
        "fundingDiversity": _funding_diversity_core(funding),
        "legislativeEffectiveness": _legislative_effectiveness_core(
            senator.get("sponsoredBills", []),
            senator.get("leadershipScore"),
            party=voting_record.get("effectiveParty") or senator.get("party", "I"),
            years_in_office=senator.get("yearsInOffice"),
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
        if isinstance(p, dict) and p.get("alignment") in (PromiseAlignment.KEPT, PromiseAlignment.PARTIAL, PromiseAlignment.BROKEN)
    )

    # independentVoting/legislativeEffectiveness thresholds are halved from
    # their original 10/40 and 3/10: both dimensions' underlying windows
    # were cut from ~2-3 congresses to the current congress only (see
    # AGENTS.md "current term"), so the old volume thresholds would grade
    # most members down for less data purely as an artifact of the window
    # change, not because they're actually under-covered. Funding's window
    # only narrowed from 2 elections to 1 (much smaller cut), so its
    # thresholds are unchanged.
    return {
        "fundingIndependence": grade(n_donors, 3, 10) if has_funding else "low",
        "promisePersistence": grade(n_evaluable, 3, 8),
        "independentVoting": grade(n_party_votes, 5, 20),
        "fundingDiversity": grade(n_industries, 3, 6) if has_funding else "low",
        "legislativeEffectiveness": grade(len(bills), 2, 5),
    }


# Small-donor share (Funding Independence component 2) baseline.
#
# WHY a state-population-relative baseline at all: a population-tercile
# audit found the old flat 40%-cap version of this component penalized
# small-state senators for a structural fact about their state (small
# states average 10.4% small-donor share vs 23.4% in large states —
# bigger states have larger natural donor pools and more national media
# exposure driving grassroots giving) rather than their own funding
# choices, while PAC dollar *amounts* were flat-to-higher in small states
# (PACs pay for committee power, not local media costs) — i.e. only the
# small-donor signal needed a state-relative fix, not PAC dependency.
# Same "expected vs. actual for this seat" pattern as
# _signed_state_alignment/_calc_constituent_alignment (Independent
# Voting's v4.2 redesign), minus IV's credit-shrinking multiplier: unlike
# vote-crossing, there's no directional ambiguity in raising more small-
# dollar money than your state predicts, so surplus is credited at full
# weight.
#
# WHERE these specific numbers come from: an ordinary-least-squares
# regression of live senators' real smallDonorPercentage against
# ln(state population) — expected_pct = A + B*ln(population_millions).
# These are calculated values, not hand-picked, so (per AGENTS.md
# "Calibrated constants are generated data") they live in a generated
# JSON file rather than as Python literals someone copy-pasted from a
# script's printed output — see _small_donor_baseline_fit() below and
# scripts/fetch_state_small_donor_baseline.py, which computes and writes
# app/data/small_donor_baseline.json. Rerun that script (network
# required) to refresh the fit against current data.


_small_donor_baseline_fit_cache: dict[str, float] | None = None


def _small_donor_baseline_fit() -> dict[str, float]:
    """Load the small-donor baseline regression fit: {A, B,
    national_mean_pct, min_expected_pct, max_expected_pct, saturation_pt}.

    Ingested from app/data/small_donor_baseline.json (written by
    scripts/fetch_state_small_donor_baseline.py); falls back to the
    2026-07 fit (101 live senators) if the file is unavailable — missing
    data degrades to a fixed prior rather than crashing scoring, same
    convention as _district_pvi()/_state_population().
    """
    global _small_donor_baseline_fit_cache
    if _small_donor_baseline_fit_cache is None:
        import json
        import pathlib
        path = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "small_donor_baseline.json"
        try:
            data = json.loads(path.read_text())
            _small_donor_baseline_fit_cache = {
                "A": float(data["A"]),
                "B": float(data["B"]),
                "national_mean_pct": float(data["national_mean_pct"]),
                "min_expected_pct": float(data["min_expected_pct"]),
                "max_expected_pct": float(data["max_expected_pct"]),
                "saturation_pt": float(data["saturation_pt"]),
            }
        except Exception:
            logger.warning("small_donor_baseline.json unavailable — using the 2026-07 fit as a fallback")
            _small_donor_baseline_fit_cache = {
                "A": 12.26, "B": 4.53, "national_mean_pct": 18.62,
                "min_expected_pct": 7.9, "max_expected_pct": 30.9, "saturation_pt": 21.2,
            }
    return _small_donor_baseline_fit_cache


def _state_small_donor_baseline(state: str) -> float:
    """Expected small-donor % for a state's population. Unresolved states
    (unknown code, DC, territories) fall back to the national mean so an
    unresolvable state is never itself a penalty or a windfall."""
    fit = _small_donor_baseline_fit()
    pop = _state_population().get(state)
    if not pop:
        return fit["national_mean_pct"]
    expected = fit["A"] + fit["B"] * math.log(pop)
    return max(fit["min_expected_pct"], min(fit["max_expected_pct"], expected))


def _small_donor_capacity_score(
    small_pct: float, state: str, district: int | None
) -> tuple[float, float]:
    """Small-donor credit relative to what this state's population
    predicts, not a flat absolute cap. Returns (score, expected_pct).

    Senate-only: House districts are apportioned to ~700-800k population
    each by design, so the state-population bias this fixes for the
    Senate (0.6M-39.5M range, a 65x spread) shouldn't exist at anywhere
    near the same magnitude for House seats. `district is not None`
    bypasses the adjustment entirely (falls back to the original flat
    40%-cap behavior) until a real district-population audit — mirroring
    _signed_state_alignment's existing district-vs-state branch — shows
    it's needed there too.
    """
    if district is not None:
        return min(small_pct / 40.0, 1.0) * 100, _small_donor_baseline_fit()["national_mean_pct"]

    expected = _state_small_donor_baseline(state)
    saturation = _small_donor_baseline_fit()["saturation_pt"]
    if small_pct >= expected:
        surplus = small_pct - expected
        score = 50.0 + 50.0 * min(surplus / saturation, 1.0)
    else:
        deficit = expected - small_pct
        score = 50.0 - 50.0 * min(deficit / saturation, 1.0)
    return score, expected


def _calc_funding_independence(funding: dict, state: str = "", district: int | None = None) -> int:
    """
    Funding Independence Score (0-100, higher = better).

    Five components (the last two folded in from the former Funding
    Diversity dimension, v6.5 — see this file's v6.4->v6.5 changelog note),
    calibrated so the median senator scores ≈50 on each (empirical
    distributions from the 2026-06 audit of FEC cycle totals):

      1. PAC dependency (50%): PAC *share*, scaled by how close the
         contributing PACs actually are to their legal per-election
         maximum.  Share: fraction of funding from PACs (FEC cycle
         totals, Schedule A) plus half-weighted outside spending
         (Schedule E independent expenditures supporting the candidate).
         Chamber-specific multiplier (2026-07 re-audit, see
         scripts/audit_pac_ratio.py): House candidates rely on PAC money
         far more heavily than Senate candidates — live medians are 37.1%
         (House) vs. 15.7% (Senate), a real structural difference, not
         noise — so a single shared multiplier (previously ×2.0,
         calibrated against a stale, Senate-skewed ≈28% assumption)
         miscalibrated both chambers. Now ×1.35 for House, ×3.2 for
         Senate, each putting that chamber's own median near 50 — this is
         this platform's own empirical calibration, not a figure
         reproduced from any paper (see the Academic rationale note below
         for why).  Utilization factor (2026-07): PAC checks are capped
         by law while individual money isn't, so share alone has a
         mechanical scale bias — a $100M campaign dilutes millions of
         PAC dollars to a near-invisible share (2026-07 audit measured FI
         vs log(total raised) at r=+0.68). Rather than penalize by
         absolute PAC dollars raised (a blunt proxy for the same
         concern), this measures the real thing directly: for each
         contributing PAC with a resolved committee type, how much of
         its legal per-election cap ($5,000 for a Qualified/multicandidate
         PAC, $3,500 for a Nonqualified one — FEC 2025-2026 limits) did it
         actually use. Dollar-weighted across all such PACs, since a PAC
         maxing out signals a deeper commitment than ten PACs each
         giving a token amount. Falls back to the old dollar-based
         penalty when no contributing PAC has a resolved committee type
         (e.g. all lookups failed) — missing data degrades gracefully
         rather than silently skipping the correction.

      2. Small-donor share (25%, Senate only — see
         _small_donor_capacity_score): unitemized (<$200) contributions as
         a share of total receipts, scored relative to what this state's
         population predicts rather than a flat cap (2026-07 audit: small
         states average 10.4% small-donor share vs 23.4% in large states —
         a structural fact about donor-pool size and media exposure, not a
         funding choice — while PAC dollar amounts were flat-to-higher in
         small states, so only this component needed the fix). House
         members keep the original flat-cap behavior: full credit at 40%
         (audit p90 ≈43%); median ≈17% scores ≈43.

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

      4. Source breadth (formerly Funding Diversity's 1st component): how
         broad and distributed the funding base is — small-donor money
         counts fullest, classified-industry money partially, UNCLASSIFIED
         (unattributable, not evidence of concentration) neutrally, opaque
         OTHER/POLITICAL money least. See _funding_diversity_core's own
         docstring for the full derivation, reused unchanged here.

      5. Industry concentration (formerly Funding Diversity's 2nd
         component): inverse HHI across classified industry categories,
         blended toward a grassroots-scaled neutral when too little
         funding is industry-classified to measure HHI meaningfully. Same
         reuse as above.

    Internal weights for all five: a linear renormalization of each
    component's PRIOR contribution to the overall score under the pre-v6.5
    two-dimension split (see the score = clamp(...) line below for the
    exact fractions) — not a fresh judgment call: the continuous math is
    provably identical to the pre-merge weighted sum. clamp() rounds to an
    int though, and this merge moves from two independent roundings (FI,
    then FD) to one, so an individual senator's overall score can shift by
    roughly half a point from that reorganization alone, not from a new
    weighting decision.

    Academic rationale
    ------------------
    Neither Barber (2016, POQ 80(S1)) nor Bonica (2014, AJPS 58(2)) contains
    a PAC-dependency or donor-concentration calibration figure — verified by
    full-text search (2026-07 audit): Barber (2016) studies donor/partisan/
    voter ideological congruence, not PAC funding shares, and Bonica (2014)
    is a CFscore ideological-scaling methodology paper, not a donor-
    concentration-by-rank study. Both are cited elsewhere in this file for
    what they actually establish (donor-vote alignment framing, Constituent
    Alignment); neither one calibrates the ×2.0 PAC multiplier or the
    concentration curve below, which are this platform's own empirical
    audit findings, not numbers reproduced from either paper's tables. The
    28% PAC-ratio figure should be periodically re-verified against a fresh
    audit (see scripts/audit_pac_ratio.py) the same way the concentration
    component's 60% median already is. Stratmann (2005, Public Choice
    124(1–2), 135–156) is a real, on-topic academic source: it finds a
    linear PAC-contribution-to-vote relationship, which is genuine support
    for using a linear (not step-function or logarithmic) PAC-dependency
    curve — that citation stays. Source breadth and industry concentration
    carry their own academic notes on _calc_funding_diversity, reused
    unchanged (Bonica 2014/Malbin 2009 for the small-dollar grassroots
    proxy; Parmigiani 2025/Rhoades 1993 for the HHI concentration metric).
    """
    return _funding_independence_core(funding, state, district)["score"]


def _funding_independence_core(funding: dict, state: str = "", district: int | None = None) -> dict:
    """Same math as _calc_funding_independence, returning every intermediate
    value alongside the final score. Single implementation — _calc_funding_
    independence and the on-demand explain_scores() breakdown both call this;
    neither reimplements the formula separately."""
    total_raised = funding.get("totalRaised", 0)
    if not total_raised or total_raised == 0:
        return {"score": 50, "components": [], "note": "No funding data — neutral default."}

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

    # Chamber-specific multiplier, calibrated so each chamber's OWN median
    # PAC ratio lands near 50 (scripts/audit_pac_ratio.py, 2026-07 live
    # audit: Senate median 15.7%, House median 37.1% — House candidates
    # rely on PAC money far more heavily than Senate candidates do, a real
    # structural difference the old shared ×2.0 multiplier (calibrated
    # against a stale/Senate-skewed 28% assumption) didn't capture for
    # either chamber). Same district-signals-House pattern already used
    # by the small-donor component below.
    pac_ratio_multiplier = 1.35 if district is not None else 3.2
    ratio_score = max(0.0, (1.0 - pac_ratio * pac_ratio_multiplier)) * 100

    # Scale the share-based score by how close contributing PACs are to
    # their legal per-election maximum — see the docstring above for why
    # this replaced a cruder absolute-dollar penalty. Caps are FEC
    # 2025-2026 cycle limits (fec.gov/help-candidates-and-committees/
    # candidate-taking-receipts/contribution-limits/): $5,000/election for
    # a Qualified (multicandidate) PAC, $3,500/election for a Nonqualified
    # one (the latter tracks the individual limit and is inflation-
    # adjusted each cycle — recalibrate at the next cycle boundary).
    MULTICANDIDATE_PAC_CAP = 5_000
    NONMULTICANDIDATE_PAC_CAP = 3_500

    # "Q"/"N" are the only two FEC committee_type codes that are actually
    # PACs subject to these per-election caps. A contributing "COM" entity
    # can just as easily be a party committee, a joint fundraising
    # committee, or a hybrid/Carey committee (codes like "Y", "V", "W") —
    # real committee types observed live against production data for
    # this exact use case — none of which are bound by the PAC limits, and
    # some of which legitimately transfer far more than $5,000 as a
    # pass-through of many underlying individual contributions. Anything
    # outside "Q"/"N" is excluded from the utilization pool entirely
    # rather than forced into the nonqualified bucket, where a large JFC
    # transfer would misleadingly register as a maxed-out PAC.
    pac_donors = [
        d for d in funding.get("topDonors", [])
        if d.get("committeeType") in ("Q", "N")
    ]
    if pac_donors:
        total_cap = 0.0
        total_utilized = 0.0
        for d in pac_donors:
            cap = MULTICANDIDATE_PAC_CAP if d["committeeType"] == "Q" else NONMULTICANDIDATE_PAC_CAP
            total_cap += cap
            total_utilized += min(d.get("total", 0), cap)
        pac_utilization = total_utilized / total_cap if total_cap > 0 else 0.0
        # 1.0 at zero utilization (PACs giving token amounts, no penalty)
        # down to 0.5 at full utilization (PACs uniformly maxing out) —
        # same [0.5, 1.0] output range as the dollar-based factor this
        # replaced, so the component's overall scale doesn't jump for the
        # population when this ships.
        volume_factor = 1.0 - 0.5 * pac_utilization
        volume_detail_suffix = (
            f"{len(pac_donors)} PAC(s) with known committee type averaging "
            f"{pac_utilization:.0%} of their per-election cap"
        )
    else:
        # No contributing donor resolves to an actual PAC ("Q"/"N") —
        # every committee-type lookup failed, none were FEC entity_type
        # "COM" rows, or all resolved to a non-PAC committee type (party
        # committee, JFC, hybrid/Carey committee) — degrade to the
        # original dollar-based penalty rather than silently skipping the
        # correction. Calibrated to the 2026-06
        # audit distribution of two-election-window PAC receipts: $0 → no
        # penalty, median $2.0M → ×0.75, $4M+ (p90 $4.4M) → ×0.5.
        volume_factor = 0.5 + 0.5 * max(0.0, 1.0 - pac_total / 4_000_000)
        volume_detail_suffix = f"no PAC committee-type data — fallback scaling for ${pac_total:,.0f} in absolute PAC dollars"

    pac_score = ratio_score * volume_factor

    # Component 2: small-donor share (25% weight), state-relative for
    # senators — see _small_donor_capacity_score.
    small_pct = funding.get("smallDonorPercentage", 0) or 0
    small_score, small_expected_pct = _small_donor_capacity_score(small_pct, state, district)

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
        concentration_detail = (
            f"top 10 of {len(external)} external donors = {concentration:.0%} "
            f"of the ${pool:,.0f} itemized external donor pool"
        )
    else:
        # Too few itemized external donors to measure concentration.
        concentration_score = 50.0
        concentration_detail = (
            f"only {len(external)} itemized external donors (${pool:,.0f} pool) "
            "— too few to measure concentration, neutral 50"
        )

    # Components 4-5: source breadth and industry concentration (folded in
    # from the former Funding Diversity dimension, v6.5 — see this file's
    # v6.4->v6.5 changelog note and config_definitions.SCORE_WEIGHTS's
    # docstring for the r=0.72 rationale). Reuses _funding_diversity_core
    # rather than reimplementing its formula — single source of truth,
    # same reuse contract this file already follows elsewhere. Falls back
    # to a neutral 50 with the same "missing data" phrasing the rest of
    # this file uses when industryBreakdown isn't available; total_raised
    # is already known nonzero at this point (checked above).
    fd_components = {c["label"]: c for c in _funding_diversity_core(funding)["components"]}
    breadth_score = fd_components.get("Source breadth", {}).get("score", 50.0)
    breadth_detail = fd_components.get("Source breadth", {}).get(
        "detail", "no industry breakdown available — neutral 50"
    )
    industry_concentration_score = fd_components.get("Industry concentration", {}).get("score", 50.0)
    industry_concentration_detail = fd_components.get("Industry concentration", {}).get(
        "detail", "no industry breakdown available — neutral 50"
    )

    # Internal weights: a linear renormalization of each component's PRIOR
    # contribution to the OVERALL score, not a fresh judgment call — old FI
    # weight 0.20 x its own 50/25/25 split, old FD weight 0.13 x its own
    # 50/50 split, each divided by the merged dimension's new 0.33 weight.
    # 20:10:10:13:13 out of 66 (= 0.10:0.05:0.05:0.065:0.065 / 0.33). This
    # is why folding the two dimensions together doesn't change the
    # underlying weighting logic — clamp()'s rounding (each dimension to
    # an int) still applies once instead of twice, so an individual
    # senator's overall score can shift by roughly half a point from that
    # alone, not from any new judgment call about relative importance.
    score = clamp(
        pac_score * (20 / 66)
        + small_score * (10 / 66)
        + concentration_score * (10 / 66)
        + breadth_score * (13 / 66)
        + industry_concentration_score * (13 / 66)
    )
    return {
        "score": score,
        "components": [
            {
                "label": "PAC dependency",
                "weight": round(20 / 66, 4),
                "score": round(pac_score, 1),
                "detail": (
                    f"{pac_ratio:.0%} of ${total_raised:,.0f} raised came from PACs"
                    + (" (incl. outside spending)" if outside_for > 0 else "")
                    + f" → raw {ratio_score:.1f}, scaled ×{volume_factor:.2f} "
                    f"({volume_detail_suffix})"
                ),
            },
            {
                "label": "Small-donor share",
                "weight": round(10 / 66, 4),
                "score": round(small_score, 1),
                "detail": (
                    f"{small_pct:.0f}% of funding from small (<$200) donors"
                    + (
                        f" vs. an expected ~{small_expected_pct:.0f}% for a state this size"
                        if district is None
                        else ""
                    )
                ),
            },
            {
                "label": "Top-donor concentration",
                "weight": round(10 / 66, 4),
                "score": round(concentration_score, 1),
                "detail": concentration_detail,
            },
            {
                "label": "Source breadth",
                "weight": round(13 / 66, 4),
                "score": breadth_score,
                "detail": breadth_detail,
            },
            {
                "label": "Industry concentration",
                "weight": round(13 / 66, 4),
                "score": industry_concentration_score,
                "detail": industry_concentration_detail,
            },
        ],
    }


PARTICIPATION_WEIGHT = 0.10


def _calc_promise_persistence(
    voting_record: dict,
    party: str,
    campaign_promises: list[dict] | None = None,
) -> int:
    """
    Promise Persistence Score (0-100, higher = better). Unweighted in
    SCORE_WEIGHTS since v6.0 (see that dict's docstring) and, since
    campaign-promise tracking was removed entirely (2026-07 — see
    policy_alignment.py's module docstring), campaign_promises is
    always empty, so this always falls through to the neutral-prior
    branch below. Still computed and stored (score_promise_persistence)
    for now rather than deleted outright, matching how the scored-
    dimension removal was handled.

    Two components:

    1. **Vote alignment** (90%): ratio of kept/partial promises to total.
       Applies a confidence penalty when most promises are "unclear" —
       if only 1/10 was evaluable, the score trends toward 50 (neutral)
       instead of being inflated by the single kept promise.

    2. **Vote participation** (10%): senators who don't show up can't
       keep promises.  Folded in from the old standalone Accessibility
       metric.

    Vote alignment scoring:
      kept    = 1.0 point
      partial = 0.5 point
      broken  = 0.0 point
      unclear = excluded from score but penalizes confidence

    A former third component, floor advocacy (whether the senator raises
    promised issues in Congressional Record floor remarks), was removed
    entirely (2026-07): with campaign_promises always empty, its
    "advocacyCoverage" input was permanently 0, so the whole
    floor-remarks fetch (Congressional Record, ~60 days back) and its
    advocacy-classification pass ran every night to feed a boost that
    could never do anything — see floor_speech_analyzer.py's removal.
    """
    # ── Base score from vote alignment ──
    base_score: float | None = None

    if campaign_promises:
        scoreable = [
            p for p in campaign_promises
            if p.get("alignment") in (PromiseAlignment.KEPT, PromiseAlignment.BROKEN, PromiseAlignment.PARTIAL)
        ]
        n_scoreable = len(scoreable)
        if scoreable:
            raw_score = sum(
                1.0 if p["alignment"] == PromiseAlignment.KEPT
                else 0.5 if p["alignment"] == PromiseAlignment.PARTIAL
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
            #
            # PRIOR_PSEUDOCOUNT = 10 (Beta(5,5)) was sized when the evidence
            # thresholds in compute_promise_vote_alignment were 0.28/0.40 —
            # noise-floor thresholds that passed ~90% of promises as
            # evaluable, averaging ~4.8 real observations per member
            # (real-data weight n/(n+10) ≈ 0.32). The 2026-07-09 threshold
            # recalibration to 0.80/0.82 fixed a genuine false-positive
            # problem (the old thresholds matched whatever ranked highest
            # among unrelated votes as "evidence"), but it also roughly
            # halved evaluable promises per member (~2.7 avg) — with the
            # pseudocount unchanged, the prior came to dominate almost
            # everyone's score, collapsing Promise Persistence's stdev
            # (7.2→3.4 shadow-tested on live Senate data, 2026-07-10 audit) and
            # flattening the dimension to near-uninformative. 6 restored
            # roughly the original real-data weight (2.7/(2.7+6) ≈ 0.31)
            # for the new, higher-precision evidence pool — but that 2.7avg
            # estimate didn't hold up once the threshold change actually ran
            # against real data: the ground-truth stdev floor (8.0) has
            # failed every night since 2026-07-11, and a 2026-07-13 audit
            # found the real average is 0.54 evaluable promises/senator
            # (59/100 have ZERO — a genuine "no matching legislative
            # activity exists for this promise in the search window"
            # reality, not a threshold bug; see cross_reference.py's
            # ceremonial-resolution fix for the one real bug found in this
            # pass, which only recovered ~9% of the "unclear" mass). At
            # n≈0.5, no pseudocount preserves both floor-clearing spread
            # and real-data weight simultaneously — 6 gives everyone with
            # zero evaluable promises (the majority) an identical 50, which
            # is correct, but leaves too little room for the minority with
            # 1-2 real observations to move the population stdev above 8.0.
            # Resized to 3 (Beta(1.5, 1.5)): real-data weight at n=1 goes
            # from 1/7≈14% to 1/4=25% — still meaningfully shrunk (a single
            # promise can't swing a senator to an extreme), but enough to
            # restore population stdev to ~9.2 (shadow-tested with the
            # ceremonial-resolution fix applied). This is a stopgap for
            # today's evidence volume, not a fix for why it's so low — that
            # needs its own investigation (is the LLM gray-zone gate
            # under-matching, or is 0.5 evaluable/senator just the honest
            # ceiling given how few promises name a bill that actually gets
            # a floor vote within one congress).
            PRIOR_PSEUDOCOUNT = 3  # Beta(1.5, 1.5) — resized 2026-07 for the real ~0.5avg evidence volume
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

    # ── Blend ──
    final = base_score * (1 - PARTICIPATION_WEIGHT) + participation_score * PARTICIPATION_WEIGHT

    return clamp(final)


def _signed_state_alignment(
    state: str,
    party: str,
    effective_party: str | None = None,
    district: int | None = None,
) -> float:
    """How the member's party aligns with their seat's partisan lean.

    Returns -1.0 to +1.0, normalized at ±15 PVI points:
      +1.0 = deep safe seat for the member's party,
       0.0 = swing seat (or unknown party/state),
      -1.0 = seat strongly leans toward the opposing party.

    House members are measured against their DISTRICT's lean when the
    per-district table has it; state lean is the senator measure and the
    House fallback. For Independents, uses effective_party (inferred
    caucus) to determine alignment. Sanders (I-VT, caucuses D) gets the
    D lean for Vermont.
    """
    pvi = STATE_PVI.get(state, 0)
    if district is not None:
        pvi = _district_pvi().get(f"{state}-{district}", pvi)
    eval_party = effective_party or party
    if eval_party == "R":
        lean = pvi
    elif eval_party == "D":
        lean = -pvi
    else:
        return 0.0
    return max(-1.0, min(lean / 15.0, 1.0))


# How much surplus-crossing credit is discounted when a member's crossings
# concentrate on votes where the OPPOSING party voted in near lockstep on
# its own side — reading as adopting the opposition's platform position
# rather than building bipartisan consensus.
#
# Intentionally 0.0 (inert) at ship time: opposing_party_unity_pct is a
# brand new per-vote field (2026-07) with zero historical data — every
# existing KeyVote/RepKeyVote row has it NULL until the next full pipeline
# run recomputes votes with the new signal, so avg_crossing_unity is None
# for every senator today regardless of this constant's value. Every other
# calibration constant in this file was fit against real data before
# shipping (the FI small-donor baseline, LE volume ceilings, ...) — this
# one can't be, yet. Once a pipeline run has populated real unity data,
# run scripts/calibrate_crossing_quality.py (grid search against
# GROUND_TRUTH and the population stdev floor — there's no natural
# continuous target to fit against, unlike e.g. the FI baseline's OLS
# regression) and raise this from 0.0 to the largest value that still
# passes every check.
CROSSING_QUALITY_DISCOUNT = 0.0


def _calc_constituent_alignment(
    voting_record: dict,
    lobbying_matches: list[dict],
    funding: dict,
    state: str = "",
    party: str = "I",
    bipartisanship: float | None = None,
    district: int | None = None,
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

    Components:
      1. Seat-relative vote alignment (80%, or 100% when cosponsorship
         data is unavailable): the member's contested-vote break rate
         compared to an EXPECTED break rate derived from state partisan
         lean (Cook PVI):
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

      2. Coalition breadth (20%, when cosponsorship data exists): see
         below.

    Removed (2026-07): "Donor independence," a 25%-weighted component
    based on donor-vote connection matches with a fundraising-total-scaled
    baseline. It measured essentially the same underlying signal as the
    Funding Independence dimension (both driven by total raised and
    donor-industry concentration — see config_definitions.py's r=0.72
    funding-pair rationale for the analogous Funding Independence/Funding
    Diversity finding), and in practice reduced to a coarse function of
    total_raised: senatorVoteAligned is always None (structural data
    limitation — no source discloses per-bill donor positions, see the
    module docstring under "Independent Voting"), and 85% of senators
    have zero detected lobbying matches, leaving one of four fixed
    baseline values by fundraising bucket for the large majority. The
    freed weight now goes entirely to seat-relative alignment (and
    coalition breadth keeps its own independently-justified 20%) rather
    than being redistributed to preserve a three-way split that no
    longer has three genuinely distinct signals.

    Removed in v4.2: the "state-relevant policy" exemption that skipped
    party-line votes on policy areas related to the member's TOP DONOR
    industries. Donor industries are not a proxy for state interests —
    that exemption shielded exactly the votes most suspect for capture,
    and gave bigger fundraisers more exemptions. Seat-relative
    expectations now carry the constituent-representation adjustment.
    """
    return _constituent_alignment_core(
        voting_record, lobbying_matches, funding, state, party, bipartisanship, district,
    )["score"]


def _constituent_alignment_core(
    voting_record: dict,
    lobbying_matches: list[dict],
    funding: dict,
    state: str = "",
    party: str = "I",
    bipartisanship: float | None = None,
    district: int | None = None,
) -> dict:
    """Same math as _calc_constituent_alignment, returning every intermediate
    value alongside the final score. Single implementation, same reuse
    contract as _funding_independence_core above."""
    effective_party = voting_record.get("effectiveParty", party)
    alignment = _signed_state_alignment(
        state, party, effective_party=effective_party, district=district,
    )

    all_votes = (voting_record.get("keyVotes") or []) + (
        voting_record.get("recentVotes") or []
    )

    voted_with = 0.0
    voted_against = 0.0
    crossing_unity_sum = 0.0
    crossing_unity_weight = 0.0
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
            unity = v.get("opposingPartyUnityPct") if isinstance(v, dict) else None
            if unity is not None:
                crossing_unity_sum += unity * weight
                crossing_unity_weight += weight

    avg_crossing_unity = (
        crossing_unity_sum / crossing_unity_weight if crossing_unity_weight > 0 else None
    )
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
            # Second, independent discount: how partisan were the actual
            # crossings? avg_crossing_unity in [0.65, 1.0] by construction
            # (see normalize_votes.opposing_party_unity) — 0.65 means the
            # opposing party was barely unified (crossing reads as
            # consensus-building), 1.0 means it voted in lockstep
            # (crossing reads as adopting the opposition's own line, not
            # building consensus). No signal (older data, or insufficient
            # roll-call member data) never triggers a discount — missing
            # data is never punitive, same principle as every other
            # component in this file.
            if avg_crossing_unity is not None:
                normalized_partisanship = min(
                    max((avg_crossing_unity - 0.65) / 0.35, 0.0), 1.0
                )
                crossing_quality = 1.0 - CROSSING_QUALITY_DISCOUNT * normalized_partisanship
            else:
                crossing_quality = 1.0
            credit *= crossing_quality
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
        against_pct = None
        party_score = 50

    # Coalition breadth (v5): cross-party cosponsorship rate normalized to
    # the chamber cohort (Lugar Center Bipartisan Index method; Harbridge
    # 2015). Voting congruence asks "do you vote the way your seat
    # elected you to"; breadth asks "do you also legislate for the
    # constituents who didn't vote for you" — attracting other-party
    # cosponsors to your bills and lending your name across the aisle.
    # The cohort median arrives at 0.5 -> 50 (typical member), so the
    # component carries the same "match expectation = 50" semantics as
    # seat-relative voting. When cosponsorship data is missing the
    # component is skipped entirely rather than scored neutral.
    if bipartisanship is not None:
        breadth_weight = 0.20
        breadth_score = max(0.0, min(bipartisanship, 1.0)) * 100
    else:
        breadth_weight = 0.0
        breadth_score = 0.0

    party_weight = 1.0 - breadth_weight
    score = clamp(
        party_score * party_weight
        + breadth_score * breadth_weight
    )

    party_alignment_detail = (
        f"expected break rate {expected:.1%} for this seat (state lean "
        f"signal {alignment:+.2f}), actual {against_pct:.1%}"
        if against_pct is not None
        else "fewer than 3 party-labeled votes available — neutral 50"
    )
    if against_pct is not None and avg_crossing_unity is not None:
        party_alignment_detail += (
            f", crossings averaged {avg_crossing_unity:.0%} opposing-party unity"
        )

    components = [
        {
            "label": "Seat-relative vote alignment",
            "weight": round(party_weight, 2),
            "score": round(party_score, 1),
            "detail": party_alignment_detail,
        },
    ]
    if breadth_weight > 0:
        components.append({
            "label": "Coalition breadth",
            "weight": breadth_weight,
            "score": round(breadth_score, 1),
            "detail": f"cross-party cosponsorship rate {bipartisanship:.0%}, chamber-median-normalized",
        })
    return {"score": score, "components": components}


def _calc_funding_diversity(funding: dict) -> int:
    """
    Funding Diversity Score (0-100, higher = better).

    v6.5 (2026-07): no longer its own SCORE_WEIGHTS entry or top-level
    scorecard panel — folded into Funding Independence as two additional
    components (_funding_independence_core calls _funding_diversity_core
    directly and reuses these two signals). Kept running and stored to
    score_funding_diversity exactly as before (same "still real, still
    computed, just excluded from the weighted sum" pattern as
    promisePersistence's v6.0 removal) since other consumers (action
    center, Bluesky spotlight text, the funding-diversity DB column
    itself) still read it independently. See config_definitions.
    SCORE_WEIGHTS's docstring for the r=0.72 fold-in rationale.

    Measures how broad and distributed a senator's funding base is.
    Higher = funding comes from many independent sources (harder to
    capture); lower = funding concentrated in few large donors/industries.

    Two signals:

      1. Source breadth (50%): rewards broad donor bases. Small donor
         funding (<$200) represents the widest possible base — hundreds
         of thousands of individual contributors, none with outsized
         influence. Large itemized donors with classified industries
         add breadth only when spread across sectors. UNCLASSIFIED money
         (see note below — a residual we cannot attribute at all, not
         evidence of concentration) is weighted neutrally rather than
         penalized. Only OTHER/POLITICAL/large-individual-unclassified-
         by-employer money — donors we *attempted* to classify by name
         and came up empty — keeps the "least diverse" weighting, since
         that at least carries a weak opacity signal UNCLASSIFIED does
         not.

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

    Uses each entry's dollar ``total``, not its stored ``percentage`` —
    that field is rounded to the nearest integer point for display
    (normalize_finance.py), which silently zeroes out any industry
    under ~0.5% of total raised. A 2026-07 audit found 86.5% of
    industry rows had percentage=0 despite a nonzero dollar total, and
    UNCLASSIFIED (donations the classifier couldn't attribute to any
    industry — semantically "unknown," the same as OTHER/POLITICAL, not
    itself an industry) was left out of NON_INDUSTRY_CODES. Together
    these collapsed the HHI calculation to near-total "concentration" in
    UNCLASSIFIED for 95/100 senators regardless of their actual spread
    across real industries, dragging the population mean to 37 against
    every other dimension's ~50 neutral calibration.

    UNCLASSIFIED-as-neutral (2026-07): UNCLASSIFIED is a pure residual —
    ``total_raised`` minus everything else we *could* categorize
    (normalize_finance.py) — not donors the classifier examined and
    failed to place. It swallows committee transfers, joint-fundraising
    splits, and donations lacking employer data: money with no
    attribution path at all, which says nothing about whether it's
    concentrated in one source or spread across thousands. A live audit
    found a 32% median UNCLASSIFIED share (56% at p90) driving a strong
    negative correlation with this score (r=-0.66) purely from missing
    attribution, not measured concentration — directly contradicting
    this project's own "missing data defaults to neutral, never
    punitive" principle applied everywhere else. Weighted neutrally here
    (0.5, matching the population's ~50 baseline) rather than folded
    into the "least diverse" bucket with OTHER/POLITICAL/unclassified-
    large-individual money, which at least represents a real (if failed)
    classification attempt.
    """
    return _funding_diversity_core(funding)["score"]


def _funding_diversity_core(funding: dict) -> dict:
    """Same math as _calc_funding_diversity, returning every intermediate
    value alongside the final score. Single implementation, same reuse
    contract as _funding_independence_core above."""
    industry_breakdown = funding.get("industryBreakdown", [])
    small_donor_pct = funding.get("smallDonorPercentage", 0)
    total_raised = funding.get("totalRaised", 0)

    if not industry_breakdown or not total_raised:
        return {"score": 50, "components": [], "note": "No funding data — neutral default."}

    # Signal 1: source breadth
    # Small donors = broadest possible base (many independent contributors).
    # Classified industry donors = moderate breadth (we know the sector).
    # Unclassified large donors = narrowest (few large, opaque sources).
    small_frac = small_donor_pct / 100.0

    classified_industry_total = sum(
        ind.get("total", 0) for ind in industry_breakdown
        if ind.get("industry") not in NON_INDUSTRY_CODES
    )
    classified_frac = classified_industry_total / total_raised

    unclassified_total = sum(
        ind.get("total", 0) for ind in industry_breakdown
        if ind.get("industry") == "UNCLASSIFIED"
    )
    unclassified_frac = unclassified_total / total_raised

    # Small donors are the most diverse source; classified industry
    # money is moderately diverse; UNCLASSIFIED (unattributable, not
    # evidence of concentration — see docstring) is neutral; the true
    # remainder (OTHER, POLITICAL, large individuals whose employer
    # didn't match any industry) is least diverse.
    other_frac = max(0, 1 - small_frac - classified_frac - unclassified_frac)
    breadth = (
        small_frac * 1.0
        + classified_frac * 0.6
        + unclassified_frac * 0.5
        + other_frac * 0.2
    )
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
    total_known = sum(ind.get("total", 0) for ind in industries) if industries else 0
    total_known_pct = total_known / total_raised * 100

    # Fallback/blend target for when classified industry money is too
    # thin a slice to measure HHI on. Previously a flat step (65 if
    # small_frac > 0.3 else 50) regardless of how far past 0.3 small_frac
    # actually was — so a senator overwhelmingly funded by small donors
    # (Bernie Sanders: 63% small-donor, 0.28% classified-industry money)
    # got exactly the same 65 as one just barely over the threshold.
    # That silently capped Funding Diversity's population-wide maximum at
    # 69 (2026-07 audit: 0/100 senators break 69, the ceiling this flat
    # 65 mechanically imposes), even though near-total small-dollar
    # reliance IS itself close to maximal diversification — it's spread
    # across an unbounded number of individual donors, not a handful of
    # institutional ones. Scaling continuously with small_frac instead
    # (still 50 at small_frac=0, still 65 at exactly 0.3 — the old
    # threshold's value, so this is continuous with the prior behavior
    # at that point, not a discontinuous jump) lets that reward grow all
    # the way to 100 for a hypothetical fully-small-dollar campaign.
    grassroots_neutral = 50 + small_frac * 50

    if total_known_pct < 5:
        # Very little classified industry money — HHI is meaningless
        # noise on a tiny slice. Default to the grassroots-scaled neutral.
        concentration_score = grassroots_neutral
        concentration_detail = (
            f"only {total_known_pct:.1f}% of funding is industry-classified — "
            f"too little to measure HHI, defaults to grassroots-scaled neutral "
            f"({small_frac:.0%} small-donor share → {grassroots_neutral:.0f})"
        )
    else:
        hhi = sum(
            (ind.get("total", 0) / total_known) ** 2
            for ind in industries
        )
        normalized = max(0, min((hhi - 0.10) / 0.90, 1.0))
        raw_concentration = (1 - normalized) * 100

        # When industry money is a small fraction of total funding,
        # its concentration matters less. Blend toward neutral based
        # on how much of total funding is industry-classified.
        industry_relevance = min(total_known_pct / 40, 1.0)
        concentration_score = (
            raw_concentration * industry_relevance
            + grassroots_neutral * (1 - industry_relevance)
        )
        concentration_detail = (
            f"HHI={hhi:.3f} across {len(industries)} industries → raw "
            f"{raw_concentration:.1f}, blended {industry_relevance:.0%} with "
            f"grassroots-scaled neutral {grassroots_neutral:.0f} "
            f"({total_known_pct:.0f}% of funding industry-classified)"
        )

    score = clamp(breadth_score * 0.5 + concentration_score * 0.5)
    return {
        "score": score,
        "components": [
            {
                "label": "Source breadth",
                "weight": 0.5,
                "score": round(breadth_score, 1),
                "detail": (
                    f"{small_frac:.0%} small-donor + {classified_frac:.0%} "
                    f"classified-industry + {unclassified_frac:.0%} unclassified "
                    f"(neutral) + {other_frac:.0%} opaque"
                ),
            },
            {
                "label": "Industry concentration",
                "weight": 0.5,
                "score": round(concentration_score, 1),
                "detail": concentration_detail,
            },
        ],
    }


# Chamber majority party by congress (public record; applied symmetrically).
# Used to benchmark bill advancement against what a sponsor's majority/
# minority status makes achievable — Volden & Wiseman (2014) show minority
# sponsors advance bills at a fraction of the majority rate, and scoring
# against a single absolute threshold silently penalizes whichever party
# is out of power. Baseline rates are measured from this platform's own
# bill corpus (2026-07: senate 3.6% majority / 2.4% minority over 24,294
# bills; house 6.4% / 2.4% over 13,510).
_SENATE_MAJORITY: dict[int, str] = {
    104: "R", 105: "R", 106: "R", 107: "D", 108: "R", 109: "R", 110: "D",
    111: "D", 112: "D", 113: "D", 114: "R", 115: "R", 116: "R", 117: "D",
    118: "D", 119: "R",
}
_HOUSE_MAJORITY: dict[int, str] = {
    104: "R", 105: "R", 106: "R", 107: "R", 108: "R", 109: "R", 110: "D",
    111: "D", 112: "R", 113: "R", 114: "R", 115: "R", 116: "D", 117: "D",
    118: "R", 119: "R",
}


# House bill types (substantive + commemorative) — used for chamber
# detection wherever a bill's own billType is the only chamber signal
# available (no explicit chamber field on sponsored-bill records).
_LES_HOUSE_TYPES = {"hr", "hjres", "hres", "hconres"}


def _advancement_baseline(bill_type: str, congress: int | None, party: str | None) -> float:
    """Expected bill advancement rate for a sponsor, by chamber/congress/
    party. Chamber detection must cover every House bill type (including
    commemorative hres/hconres), not just substantive ones — this is
    averaged over a member's full sponsored-bill list (_les_component_
    score), not pre-filtered to substantive bills the way the old
    advancement-rate formula filtered it."""
    is_house = bill_type in _LES_HOUSE_TYPES
    majority = (_HOUSE_MAJORITY if is_house else _SENATE_MAJORITY).get(congress or 0)
    if not majority or party not in ("D", "R"):
        return 0.030  # overall measured mean when status is unknowable
    if is_house:
        return 0.064 if party == majority else 0.024
    return 0.036 if party == majority else 0.024


# A member with zero substantive bills after a real term in office is a
# genuine, if weak, negative data point — not the same as a freshman who
# simply hasn't had a chance yet. Below this tenure the two are
# indistinguishable and stay neutral; at/above it, "zero substantive
# bills" gets the same confidence-shrinkage treatment a genuine low-n
# attempt already gets, rather than being treated as missing data.
# Roughly one legislative session.
_MIN_TENURE_FOR_ZERO_SIGNAL_YEARS = 0.5

# Confidence assigned to a confirmed-zero record — set just above what a
# genuine single-bill, zero-success record gets (min(1/10, 1.0) = 0.10),
# so a confirmed zero isn't treated as a *noisier* sample than an actual
# attempt that also produced nothing. Without this, a flat 50 for
# confirmed inaction always beat a genuine low-n attempt that failed
# once that attempt's own shrinkage-toward-50 was correctly applied —
# see the 2026-07 "inaction beats trying and failing" fix.
_ZERO_BILLS_CONFIDENCE = 0.15


def _zero_bill_component_score(years_in_office: float | None) -> tuple[float, str]:
    """Shared "no substantive bills" treatment, used whether the member
    sponsored nothing at all or only ceremonial resolutions — neither
    carries a substantive record, so both get the same verdict."""
    if (years_in_office or 0) >= _MIN_TENURE_FOR_ZERO_SIGNAL_YEARS:
        score = 50.0 * (1 - _ZERO_BILLS_CONFIDENCE)
        detail = (
            f"0 substantive bills over {years_in_office:.1f} years in office "
            "— confirmed inactivity, not a data gap"
        )
        return score, detail
    return 50.0, "no substantive bills on record — neutral 50"


# Legislative Effectiveness: significance-weighted, cumulative-stage
# scoring — follows Volden & Wiseman (2014, "Legislative Effectiveness in
# the United States Congress," Cambridge UP; methodology also documented
# at thelawmakers.org, the Center for Effective Lawmaking). Their real
# LES weights each sponsored bill by significance and credits that weight
# CUMULATIVELY across every stage it reaches — a bill that becomes law
# contributes to the introduced/committee/passed-chamber/law totals all
# at once, not just its final stage. Two deliberate, disclosed departures
# from their real methodology, both confirmed by a 2026-07 deep-research
# audit of their actual published approach (not assumed from the name):
#   - Only 2 of their 3 significance tiers are implemented: commemorative
#     resolutions = 1x, substantive bills = 5x (the existing
#     SUBSTANTIVE_BILL_TYPES split). Their 3rd tier ("substantive AND
#     significant," 10x) was assigned from hand-curated expert/media
#     "major legislation of the year" lists this platform has no access
#     to and no reliable proxy for — not implemented, rather than
#     approximated with an un-validated stand-in.
#   - Their real score normalizes to the chamber-term population mean
#     (an average member scores exactly 1.0); this platform's
#     sponsored-bill data is career-cumulative (many congresses, not one
#     fixed 2-year term V&W's design assumes), so credit is computed
#     per-congress-served and compared against an EXPECTED credit
#     (majority/minority-adjusted, reusing _advancement_baseline's real
#     audited rates) rather than a literal population-mean ratio — this
#     is the same "expected-vs-actual, credit the difference" pattern
#     already used successfully elsewhere in this file (Constituent
#     Alignment's seat-relative break rate).
# Legislative leadership (PageRank cosponsorship centrality, Brin & Page
# 1998) has no basis in Volden & Wiseman's work — it is kept as an
# explicitly separate 30%-weighted component, not blended into the
# V&W-based 70%.

_LES_STAGE_ORDER: dict[str, int] = {
    "INTRODUCED": 1,
    "IN_COMMITTEE": 2,
    "PASSED_CHAMBER": 3,
    "IN_OTHER_CHAMBER": 3,  # already passed its own chamber; no separate V&W stage for this
    "TO_PRESIDENT": 3,      # same — passed both chambers, not yet a new milestone
    "ENACTED": 4,
    "VETOED": 3,            # passed Congress, never became law
}
_LES_MAX_STAGE = 4


def _les_bill_stage(bill: dict) -> int:
    """This bill's cumulative-stage position (1-4), V&W-style.

    Prefers the real `stage` classification (classify_bill_stage_from_
    actions, bill_stage.py — built from Congress.gov's own structured
    action codes) when present. Falls back to is_law / latestAction
    keyword inference when `stage` is unset: it's a brand-new field with
    no historical backfill yet (2026-07), and without this fallback every
    existing sponsored bill would default to stage 1 until a pipeline run
    repopulates it, silently collapsing this entire component to a flat
    bill count. The fallback never invents progress beyond what a hard
    fact (is_law) or the same advancement keywords the old formula used
    supports — "ordered to be reported" clears committee (stage 2, per
    bill_stage.py's own actionCode table), "passed"/"agreed to" clears
    the chamber (stage 3)."""
    stage = _LES_STAGE_ORDER.get(bill.get("stage"))
    if stage is not None:
        return stage
    if bill.get("isLaw"):
        return _LES_MAX_STAGE
    action = (bill.get("latestAction") or "").lower()
    if "passed" in action or "agreed to" in action:
        return 3
    if "ordered to be reported" in action:
        return 2
    return 1


def _les_significance_weight(bill_type: str) -> float:
    return 5.0 if bill_type in SUBSTANTIVE_BILL_TYPES else 1.0


def _les_cumulative_credit(bill: dict) -> float:
    """weight x stages-reached — V&W's real cumulative design: a bill
    credited into stage 4 contributes 4x its significance weight total
    (1 unit at each of stages 1-4), not just 1x at its final stage."""
    w = _les_significance_weight((bill.get("billType") or "").lower())
    s = _les_bill_stage(bill)
    return w * s


# Population-average significance-weighted cumulative-stage credit per
# congress served, chamber-specific (V&W's real normalization sets the
# chamber-term average to exactly 1.0; this platform's data is career-
# cumulative rather than one fixed term, so the population average is a
# periodically-recalibrated constant instead of a live computation —
# same convention as every other self-calibrated constant in this file,
# e.g. the FI small-donor state baseline). Calibrated via
# scripts/calibrate_les_credit_scale.py against live production data,
# using this module's own _les_cumulative_credit so the calibration and
# the scoring formula can never silently drift apart. 2026-07 live audit
# (101 senators, 427 reps): Senate mean=285.3/congress (median 254,
# stdev 157), House mean=122.0/congress (median 107, stdev 77).
_LES_POPULATION_AVG_SENATE = 285.3
_LES_POPULATION_AVG_HOUSE = 122.0

# Population-average _advancement_baseline rate, used to turn a member's
# own majority/minority bill mix into a RATIO against the population
# average (not an absolute add-on) — this is the majority/minority
# benchmark adjustment, adapted from _advancement_baseline's real audited
# rates without requiring the live per-term regression V&W's own
# Benchmark Score uses (infrastructure this codebase doesn't have).
# Same calibration script determines this. 2026-07 live audit: 0.0404.
_LES_AVG_BASELINE = 0.0404

# Saturation constant for the expected-vs-actual credit gap, same "never
# zero, never a runaway score from one outlier bill" shape as every other
# saturation constant in this file (e.g. Constituent Alignment's
# surplus/0.25). Same calibration script: ~1.5x the mean chamber stdev of
# real per-congress credit (2026-07 audit: Senate stdev 157, House 77 ->
# 175.5), checked against the population stdev floor — LE has no
# per-senator GROUND_TRUTH entries to check against (ground_truth.py).
_LES_CREDIT_SATURATION = 175.5


def _les_component_score(
    sponsored_bills: list[dict], party: str | None, years_in_office: float | None,
) -> tuple[float, str]:
    """The V&W-based 70% component: significance-weighted, cumulative-
    stage credit per congress served, scored relative to what an average
    sponsor of this party/status/chamber would be expected to achieve —
    not an absolute rate. Confirmed-zero-vs-no-data-yet distinction (see
    _zero_bill_component_score) carried forward unchanged from the
    2026-07 "inaction beats trying and failing" fix; this is the same
    invariant, re-homed into the new formula."""
    n_sub = sum(
        1 for b in sponsored_bills
        if (b.get("billType") or "").lower() in SUBSTANTIVE_BILL_TYPES
    )
    if n_sub == 0:
        return _zero_bill_component_score(years_in_office)

    congresses = {b.get("congress") for b in sponsored_bills if b.get("congress")}
    n_congresses = max(len(congresses), 1)
    raw_per_congress = sum(_les_cumulative_credit(b) for b in sponsored_bills) / n_congresses

    house_n = sum(
        1 for b in sponsored_bills
        if (b.get("billType") or "").lower() in _LES_HOUSE_TYPES
    )
    is_house_member = house_n > (len(sponsored_bills) - house_n)
    population_avg = _LES_POPULATION_AVG_HOUSE if is_house_member else _LES_POPULATION_AVG_SENATE

    member_baseline = sum(
        _advancement_baseline((b.get("billType") or "").lower(), b.get("congress"), party)
        for b in sponsored_bills
    ) / len(sponsored_bills)
    status_ratio = member_baseline / _LES_AVG_BASELINE if _LES_AVG_BASELINE else 1.0
    expected_per_congress = population_avg * status_ratio

    diff = raw_per_congress - expected_per_congress
    conf = min(n_sub / 10, 1.0)
    normalized_diff = max(-1.0, min(diff / _LES_CREDIT_SATURATION, 1.0))
    raw_score = 50.0 + 50.0 * normalized_diff
    score = raw_score * conf + 50.0 * (1 - conf)
    detail = (
        f"{raw_per_congress:.1f} significance-weighted stage-credit/congress "
        f"vs. {expected_per_congress:.1f} expected for this sponsor's status "
        f"({n_sub} substantive bills)"
    )
    if conf < 1.0:
        detail += f", confidence-scaled {conf:.0%} ({n_sub} of 10 bills)"
    return score, detail


def _calc_legislative_effectiveness(
    sponsored_bills: list[dict],
    leadership_score: float | None = None,
    party: str | None = None,
    years_in_office: float | None = None,
) -> int:
    """
    Legislative Effectiveness Score (0-100, higher = better).

    Two components:

      1. Bill significance & advancement (70%): Volden & Wiseman
         (2014)-based — see the module comment above _LES_STAGE_ORDER for
         the full methodology and the two disclosed departures from their
         real approach (2-tier significance, expected-vs-actual credit
         instead of population-mean-ratio normalization).

      2. Legislative leadership (30%): PageRank score from the
         cosponsorship network (Brin & Page 1998, computed in
         sponsorship_analysis.py) — no basis in Volden & Wiseman, kept as
         an explicitly separate signal. Senators whose bills attract
         cosponsors from influential colleagues score higher. Shrunk
         toward neutral 50 for freshmen (PageRank centrality takes years
         to build; a near-zero raw percentile in year one reflects network
         age, not effectiveness — 2026-07 fix, see leadership_conf below).

    Both components apply Bayesian shrinkage toward 50 when data is
    sparse, preventing extreme scores from thin evidence — including a
    confirmed-zero-bills record after real tenure, which is a weak but
    real negative signal, not the same as a freshman with no data yet
    (see _zero_bill_component_score).
    """
    return _legislative_effectiveness_core(
        sponsored_bills, leadership_score, party, years_in_office,
    )["score"]


def _legislative_effectiveness_core(
    sponsored_bills: list[dict],
    leadership_score: float | None = None,
    party: str | None = None,
    years_in_office: float | None = None,
) -> dict:
    """Same math as _calc_legislative_effectiveness, returning every
    intermediate value alongside the final score. Single implementation,
    same reuse contract as _funding_independence_core above."""
    les_score, les_detail = _les_component_score(sponsored_bills or [], party, years_in_office)

    # Component: leadership score from cosponsorship PageRank
    #
    # PageRank centrality is structurally a function of network size,
    # which takes time to build — a freshman senator's raw percentile is
    # near-zero not because they're ineffective but because they haven't
    # had years to accumulate cosponsorship connections yet. A 2026-07
    # audit found this the dominant driver of a real tenure-vs-LE
    # correlation (r=+0.24 across the population; freshmen (<=2yrs)
    # averaged LE=29.5 vs veterans (>=10yrs) at 54.1), directly
    # contradicting this project's own "seniority alone is never
    # penalized" design principle (AGENTS.md). Shrink the raw percentile
    # toward neutral 50 with the same confidence-scaling pattern already
    # used for the V&W-based component, scaled to a full 6-year Senate
    # term (long enough to plausibly build a real network; short enough
    # that a second-term member isn't still getting a pass).
    if leadership_score is not None and leadership_score > 0:
        # Raw score is 0-1 from PageRank (percentile-like). Scale to 0-100.
        leadership_raw = min(leadership_score, 1.0) * 100
    else:
        # No data yet — neutral prior, never a punitive below-50 default
        # (this repo's design principle: missing data is never "bad").
        leadership_raw = 50.0

    leadership_conf = min((years_in_office or 0) / 6.0, 1.0)
    leadership_pct = leadership_raw * leadership_conf + 50 * (1 - leadership_conf)
    leadership_detail = (
        f"PageRank percentile {leadership_raw:.0f}, tenure-confidence-scaled "
        f"{leadership_conf:.0%} ({years_in_office or 0:.1f} of 6 years)"
    )

    if not sponsored_bills and not (leadership_score and leadership_score > 0) and les_score == 50.0:
        return {"score": 50, "components": [], "note": "No sponsored-bill or leadership data — neutral default."}

    score = clamp(les_score * 0.70 + leadership_pct * 0.30)
    return {
        "score": score,
        "components": [
            {"label": "Bill significance & advancement (V&W-based)", "weight": 0.70,
             "score": round(les_score, 1), "detail": les_detail},
            {"label": "Legislative leadership", "weight": 0.30,
             "score": round(leadership_pct, 1), "detail": leadership_detail},
        ],
    }
