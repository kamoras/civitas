"""
Score calculator — computes the representation sub-scores from real data:
three weighted into the overall (SCORE_WEIGHTS), two informational.

Higher score = better representation of constituents.
All scores are 0-100 where 100 = ideal representative, 0 = fully captured.

The scored dimensions (SCORE_WEIGHTS) — Promise Persistence (removed
v6.0) and Funding Diversity (folded into Funding Independence, v6.5)
still run and still store their score_* columns, just excluded from
the weighted sum below:
  1. Funding Independence       — PAC share, small-donor share, top-donor
                                  concentration, industry concentration (v6.13;
                                  v6.5 folded in the former Funding Diversity)
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
PAC multiplier is chamber-specific (0.5 / that chamber's median PAC
share, measured every run — compute_funding_reference) rather than one
shared value. Top-donor
concentration is calibrated so a 15%-of-pool share scores 100 and a
40%-of-pool share scores 0 (2026-07-23 recalibration — the earlier
20%/100%-median-60% anchors had drifted to roughly double the live
population's real median, 28%; see _funding_independence_core's own
comment on this component for the live audit numbers). Parmigiani
(2025, "Campaign contributions and legislative behavior," Journal of
Public Economics 243) reports the same top-decile-donor-share metric at
a 47% mean in a different population/period — closer to this platform's
own re-measured 28-31% median than the old 60% assumption was, real
independent corroboration that the CURRENT calibration, not the old
one, sits in the normal range that other work on this exact metric
finds (not a number copied from that paper — our own audit is the
calibration source). Prior v1 multipliers (1.3×, 1.5×) compressed
variation into the 61–94 range; recalibration creates a symmetric
distribution around the empirical sample median.

Promise Persistence: follows Naurin (2011, "Election Promises, Party
Behaviour and Voter Perceptions," Palgrave) who showed that promise
fulfillment is measurable and varies meaningfully across legislators.
The confidence penalty (blending toward 50 when few promises are
evaluable) is linear shrinkage toward 50 at a fixed rate, min(n/k, 1).
It borrows the idea of Stein-type shrinkage (Efron & Morris 1975, "Data
Analysis Using Stein's Estimator," JASA 70:350) but is not their
estimator: the rate comes from a count threshold, not from the
population's measured variance, and it stops shrinking at n >= k. Floor advocacy uses Martin (2011,
"Using Parliamentary Questions to Measure Constituency Focus," Political
Studies 59:2) as precedent for floor speech as a proxy for legislative
effort.

Constituent Alignment: raw party-line break rates are misleading without
context. Following Carson et al. (2010, "The Electoral Costs of Party
Loyalty," AJPS 54:3), we use Cook PVI as a proxy for constituent
preferences and score each member against the break rate same-party
members of their chamber show at the same seat lean — a senator in a safe
R+20 state voting with their party is representing constituents, not
failing at independence, while the same loyalty in a swing state diverges
from the median voter. Roll-call position is scored the same way against
a seat-conditional norm (Canes-Wrone, Brady & Cogan 2002, "Out of Step,
Out of Office," APSR 96:1). This is the delegate model of representation
(Miller & Stokes 1963, "Constituency Influence in Congress," APSR 57:1),
with seat partisan lean standing in for issue-level constituent opinion.
Both studies validate their measures by the incumbent's vote share; v6.13
used that same test to choose this dimension's design
(docs/research/constituent-alignment.md). Donor independence via lobbying
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
here; Parmigiani does. Resolved, not disclosed-and-deferred: Parmigiani's
own discussion argues top-share measures are better suited than HHI/Gini
to the long-tailed distributions donor data actually has — the same
reason income-inequality research prefers top-1%-share over Gini — which
raised a real question of whether Funding Diversity should move from
inverse-HHI to a top-share-based measure. Tested directly (2026-07)
against every scorecarded member's live industry breakdown (n=413 with
>=5% industry-classified funding, the same usability floor this
function's own fallback branch below uses): HHI and top-1-industry-share
are rank-correlated at rho=0.942 (Spearman, p<1e-190) — the two measures
agree on all but a handful of members. This isn't a coincidence to
distrust; it has a structural reason. Parmigiani's argument is about raw
per-DONOR amounts, which really are long-tailed like personal income —
but this component buckets by industry first (the adaptation this file's
docstring already names), and that aggregation step is exactly what
smooths away the extreme tail a top-share measure is built to handle.
Bucketing several thousand individual donors into one "Finance" row
caps how dominant any single row can get long before HHI or top-share
ever see the data — the two statistics converge because the input they
share has already had its long tail removed. The real disagreements that
remain (e.g. a member with several moderately-large industries vs. one
genuinely dominant one — Gwen Moore, Ayanna Pressley in the live 2026-07
data) are the exact case the theory predicts, but they affect roughly a
dozen members, not the population — not enough to justify moving every
member's score for a measure that would rank them almost identically.
Kept as inverse-HHI on that basis.

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
- Canes-Wrone, B., Brady, D.W. & Cogan, J.F. (2002). APSR, 96(1), 127-140.
- Harbridge, L. & Malhotra, N. (2011). AJPS, 55(3), 494-510.
- Harbridge-Yong, L., Volden, C. & Wiseman, A.E. (2023). J. Politics, 85(3).
- Lewis, J.B. et al. Voteview: Congressional Roll-Call Votes Database
  (voteview.com) — DW-NOMINATE member estimates.

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
score_promise_persistence keeps being stored — not deleted outright,
matching how the scored-dimension removal was handled elsewhere — but
with campaign_promises always empty there is no kept/broken/partial
data left to show, so no profile page displays one; it's excluded from
SCORE_WEIGHTS and the weighted overall score. Its 25%
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

Changes from v6.5 -> v6.6 (2026-07) [superseded in v6.13 — see the v6.13
note by ALGORITHM_VERSION]: Constituent Alignment's seat-relative
vote component (_constituent_alignment_core) reworked to stop penalizing
party loyalty and to stop crediting party defection direction-blind,
prompted by a fairness review of the "sticks with the party too much"
penalty (a swing/opposed-seat loyalist could be driven ~14 points below
neutral purely for a below-expected defection rate).

Governing principle (stated explicitly so the two changes below share ONE
theory of representation instead of borrowing a different one for each):
move the score off neutral only for LEGIBLE evidence that a member is
representing their constituents; treat behavior whose representational
meaning we cannot read as neutral (50). "Legible" means we can observe the
DIRECTION of a deviation relative to the seat and plausibly sign it toward
or away from the seat's center. This is a deliberately humble use of the
delegate model: state partisan lean (Cook PVI) is a noisy one-dimensional
proxy for constituent opinion, valid for RELATIVE/directional comparison but
not as an absolute per-member target — so we use it only to credit clear
moves toward the seat's center, never to penalize failure to hit a derived
"expected" rate.

  1. Over-loyalty no longer penalized (floors at neutral). The v4.2 design
     scored a below-expected defection rate as a deficit, dropping
     swing-seat and opposed-seat loyalists as low as 25. Under the governing
     principle a low defection rate is UNREADABLE, not damning, so it maps to
     neutral:
       - It may be faithful representation of the coalition that actually
         elected the member — a senator's operative constituency is their
         reelection/primary supporters, not the geographic median voter
         (Fenno, Home Style 1978; Bishin, Tyranny of the Minority 2009;
         Clinton, J. Politics 2006, finds members track copartisans). Members
         sit to the partisan side of their state median in BOTH parties as
         the structural norm (Bafumi & Herron, APSR 2010). These do NOT make
         loyalty good (we can't read that either — see the limitation note);
         they make it UNREADABLE, which is why loyalty is neutral rather than
         either penalized or rewarded.
       - It carries little individual signal regardless: party-unity ≈90%+ is
         structural for the average member of both parties even in
         competitive states (Voteview/CQ; Aldrich & Rohde conditional party
         government), an era/institution property of a sorted (Levendusky
         2009) and nationalized (Hopkins 2018) electorate (Sinclair, Party
         Wars 2006), not an individual choice.
       - A loyalty RATE is not the misrepresentation construct anyway: being
         "out of step" is district-relative ideological EXTREMITY
         (Canes-Wrone, Brady & Cogan, APSR 2002), a spatial POSITION measure
         distinct from a defection rate (Krehbiel, AJPS 2000 — unity scores
         conflate discipline with shared preferences and are agenda-
         contaminated). The one loyalty-rate citation, Carson/Koger/Lebo/
         Young (AJPS 2010), documents an electoral COST members trade off,
         not a representational failure (House-only, pre-nationalization).

  This is the ONLY behavioral change in v6.6. It is deterministic — no
  calibration constant, no data-dependent magnitude — so it is fully
  validated by unit tests alone: a below-expected loyalist scores exactly 50
  on the seat-relative component regardless of any live distribution. The
  above-expected crossing side is UNCHANGED from v6.5 (seat-direction credit
  only).

  DELIBERATELY NOT SHIPPED, and now believed WRONG AS DESIGNED, not just
  uncalibrated (revised 2026-07-20 — see v6.7's note below for how this was
  checked): a second, member-level directional discount for the crossing
  side. A raw defection rate is direction-blind, and the empirically highest
  defectors are often ideological EXTREMISTS breaking from their own flank,
  not moderates — Rand Paul from the right, Bernie Sanders from the left
  (Kirkland & Slapin, Electoral Studies 2017). Crediting a high defection
  rate direction-blind therefore over-rewards flank grandstanding. The
  intended fix discounted surplus-crossing credit by the member's ideological
  flank position, read from the party-blind SVD ideology_score computed here
  from cosponsorship patterns. This was originally withheld only for lack of
  live data to calibrate against (this file's standing rule: calibration
  constants are FIT AGAINST REAL DATA before shipping — see
  CROSSING_QUALITY_DISCOUNT, held at 0.0 for the same reason). Once live data
  became available (v6.7), checking the premise against it found the
  live crossers are the OPPOSITE of what the discount assumes: every one of
  the current chamber's biggest surplus-crossers (Murkowski, Collins, Paul,
  McConnell on the R side; the equivalent D crossers) reads as ideologically
  CENTRIST on their own party's cosponsorship-derived scale, not flank-
  extreme — 0 of the live population's crossers fall in their own party's
  extreme tercile. This isn't noise to calibrate around; it's a construct-
  validity problem: cosponsorship-based ideology and crossing rate are
  mechanically coupled, not independent — a member who crosses often also
  cosponsors more bipartisan legislation, which is the exact input that pulls
  their SVD position toward the center. The signal this file has (cosponsor-
  ship network position) cannot see the construct Kirkland & Slapin measure
  (roll-call ideological extremity/DW-NOMINATE-style ideal points) — they are
  different axes that happen to share a citation, not the same thing under
  two names. Building this discount on ideology_score as currently computed
  would either never fire (as it does not on today's population) or, if
  recalibrated to fire on today's actual crossers, would end up discounting
  the chamber's most centrist members for crossing — the opposite of its
  stated intent. Not shippable without a genuinely different ideology signal
  (a roll-call-based ideal point, which this file does not compute) or a
  redesign of what "flank" means here; grid-searching a magnitude against the
  current ideology_score would not fix this.

  Known limitation, addressed PARTIALLY in v6.7 below — deviation, not (yet
  fully) congruence: as shipped in v6.6, this dimension measured only the
  RATE and DIRECTION of a member's deviation from their party, never the
  DISTANCE between the member's position and their constituency's — so a
  member whose party is badly mismatched with a lopsided state, but who never
  crosses, scored an unearned neutral 50 with no way to tell them apart from
  a genuinely representative loyalist. v6.7's position-mismatch discount
  (below) closes the NEGATIVE half of that gap. The POSITIVE half — crediting
  ABOVE neutral for a loyalist whose party genuinely matches their state — is
  still not attempted, for the reason stated in v6.7's note: it needs an
  affirmative "this position IS what the seat wants" target, a materially
  higher evidentiary bar than "this position is an outlier for the seat,"
  and remains a named, open boundary rather than scaffolded half-built code.

Changes from v6.6 -> v6.7 (2026-07) [removed in v6.13]: added a position-mismatch discount to
the below-expected-loyalty branch (_constituent_alignment_core), answering a
direct question about the v6.6 design: if loyalty is never penalized, can a
member who votes blatantly out of step with their state — e.g. a member
whose record reads as their party's ideological extreme, representing a
seat that isn't a safe seat for that extreme — still score neutral forever?
Under v6.6 alone, yes: the branch had no signal at all besides the
(deliberately unreadable) defection rate. v6.7 adds a second, INDEPENDENT,
legible signal the v6.6 note above already named as the real construct
(Canes-Wrone, Brady & Cogan 2002's district-relative ideological EXTREMITY)
and had already computed elsewhere in the pipeline: ideology_score (SVD-
based, from cosponsorship patterns, party-blind by construction) versus
party_ideology_bounds (that member's own party's cohort-relative terciles —
see sponsorship_analysis.party_ideology_bounds). A below-expected loyalist
whose ideology_score sits in their own party's extreme third, in a seat that
isn't safely aligned for that extremity (seat-direction-discounted the same
way surplus-crossing credit already is — 0 discount in a deep safe seat,
full strength in a swing/opposed one), is penalized below neutral; everyone
else in the branch is unaffected and still floors at exactly 50. This is
NOT the same mechanism as the still-not-shipped crossing-side flank discount
above — that one discounts CREDIT for extremist crossers; this one
penalizes UNEARNED neutrality for extremist loyalists — but it uses the
same ideology_score input, and checking it against live data corrected that
discount's stated blocker in a way that argues AGAINST shipping it, not for
it: ideology_score is not, in fact, unavailable outside the pipeline
(party_ideology_bounds.json proves that), but checking the crossing-side
discount's premise against the live population found the two signals it
would multiply together are mechanically coupled, not independent — see
the "DELIBERATELY NOT SHIPPED" note above for the full finding (0 of the
current chamber's live crossers register as their own party's flank-
extreme). The blocker was data availability; it is now a construct-
validity problem instead, which recalibration cannot fix.

  POSITION_MISMATCH_MAX_PENALTY (see its own comment) IS a magnitude
  constant fit against real data before shipping, unlike CROSSING_QUALITY_
  DISCOUNT: grid-searched at 0/5/.../40 against the live 2026-07-20
  ideology_score + seat-lean distribution (101 senators) checked against
  every GROUND_TRUTH range and the IV stdev floor — every value in that
  range passed, so 25.0 was chosen by reusing this file's own existing
  magnitude for the symmetric surplus-crossing case rather than an arbitrary
  passing value. Re-run the grid search after any pipeline run meaningfully
  shifts the ideology_score distribution. (The GROUND_TRUTH ranges it was
  originally checked against were since replaced by ground_truth.py's
  derived consistency gate — validate against that gate now.)

  Validation posture: the discount's TRIGGER condition (extreme tercile +
  unsafe seat) and its scaling (seat-direction discount) are deterministic
  and unit-tested, same as v6.6's floor. Its MAGNITUDE was fit against live
  data per the above, and should be re-validated (ground_truth.py's derived
  consistency + distribution checks) after every pipeline run the same way
  v6.6's floor already requires — flooring/discounting removes or
  redistributes spread the old formula manufactured differently, so the IV
  distribution checks in ground_truth.check_score_distribution are the
  standing post-run gate for any change to this branch, not just this one.

Changes from v6.7 -> v6.8 (2026-07-21): fairness audit of the first full
pipeline run under v6.7 (2026-07-21 population, 99 scored D/R senators),
prompted by a direct question ("Chris Murphy is penalized hard on
Constituent Alignment — is this fair, or are we losing dimension on the
metric?"). Found the position-mismatch discount (v6.7, above) and Coalition
Breadth (v5, this dimension's other component) are NOT independent signals
as v6.7 assumed: within-party ideology_score extremity correlates with
bipartisanship_score at r=-0.76 (r²=0.58, n=99) — both are different linear-
algebra projections of the SAME cosponsorship matrix (ideology_score is an
SVD position on it; bipartisanship_score is its cross-party edge rate). A
member with a narrow, within-party cosponsorship network was therefore
penalized for that ONE underlying fact twice inside a single 100%-weighted
dimension. Concretely, this over-penalized senators with no real-world
reputation for flank extremism: Tammy Duckworth (IL, a veteran-focused
senator broadly regarded as center-left) landed among the "10 most extreme"
Democrats on this metric alone, alongside Chris Murphy (CT) and Cory Booker
(NJ) — all three scored 32-36 on this dimension pre-fix, despite all three
representing safe D states where narrow coalition-building is exactly what
v6.6's own governing principle already calls unreadable, not damning.

  Two changes, both applying the SAME "seat-safety scales the discount"
  mechanism v6.6/v6.7 already established — no new mechanism introduced
  (Recommendation #4 of the audit: leave the scaling mechanism itself alone,
  fix its application):

  1. POSITION_MISMATCH_MAX_PENALTY cut 25.0 -> 10.0 (see its own comment for
     the full grid-search account). Chosen as roughly (1 - r²) of the
     original — the ~42% of the signal this discount carries that ISN'T
     already captured by Coalition Breadth — rather than an arbitrary lower
     value. A swing-state member with a genuinely mismatched position (e.g.
     David McCormick, PA) still lands at 46 instead of the pre-fix 37: a
     real, discernible discount, not a no-op.

  2. Coalition Breadth's below-median case is now seat-safety-scaled
     (_constituent_alignment_core's breadth block), mirroring the surplus-
     crossing and position-mismatch branches exactly: floors fully at
     neutral in a maximally safe seat (a safe seat's median voter is with
     the member's party, so a narrow within-party coalition may be faithful
     representation of that mandate — Fenno 1978), stands at its full raw
     value in a swing/opposed seat (there, low cross-party cosponsorship IS
     legible evidence of a representational gap). Before this change,
     Coalition Breadth was the one component in this dimension with NO
     seat-safety adjustment at all — inconsistent with the "move off
     neutral only for legible evidence" governing principle every other
     branch here already follows, and the mechanical source of why a safe-
     seat member's narrow network was fully punished through this channel
     regardless of seat.

  Together, these reduce (not eliminate — the two components still measure
  genuinely related aspects of coalition behavior, just no longer near-
  duplicate ones) the double-count and bring both components under the same
  "legible evidence only" standard. Deliberately NOT attempted
  (Recommendation #3, partially addressed by this note rather than new
  code): a fuller disclosure of the remaining construct-validity limitation
  — Coalition Breadth and the position-mismatch discount are still both
  downstream of the SAME cosponsorship matrix and will never be fully
  orthogonal signals within this dimension; that is a standing property of
  using one data source (cosponsorship networks) for two conceptually
  distinct measures (ideological position vs. cross-party coalition-
  building), not a bug this pass can code its way out of. Re-run the r/r²
  correlation check (see POSITION_MISMATCH_MAX_PENALTY's comment) after any
  pipeline run meaningfully shifts either distribution.

Changes from v6.8 -> v6.9 (2026-07-21): a platform-wide political-science
audit of the live population (the same night as v6.8, a different
dimension) found Legislative Effectiveness's chamber-fairness handling was
only half-fixed. _LES_POPULATION_AVG_SENATE/_HOUSE (the population-average
credit constants, renamed to _LES_POPULATION_MEDIAN_* in v6.10) were
already correctly chamber-specific, but
_LES_AVG_BASELINE — the constant every member's own majority/minority
advancement rate gets divided by to compute their status_ratio — was a
SINGLE value pooled across both chambers. Measuring the two chambers' real
_advancement_baseline rates separately found they genuinely differ (House
mean 0.0443, n=427; Senate mean 0.0305, n=101 — a real structural
difference in how _advancement_baseline's own audited, bill-type-keyed
rates work out per chamber, not noise), so comparing every member against
ONE pooled average systematically inflated House members' status_ratio
(and therefore their expected-credit bar) while deflating the Senate's —
the two supposedly-chamber-specific constants were silently fighting each
other.

  Measured effect on the live population before this fix: 61% of House
  members (n=431) scored below the neutral midpoint on Legislative
  Effectiveness vs. only 38% of the Senate (n=101) — means 44.6 vs 60.5,
  despite no real reason to expect House members to be systematically less
  legislatively effective than senators once compared fairly against their
  own chamber's real norms. Simulated against the live population before
  shipping (same discipline as every calibration change in this file):
  splitting _LES_AVG_BASELINE into _LES_AVG_BASELINE_SENATE/_HOUSE brings
  both chambers to a comparable, much less lopsided split (~53-58% below-
  neutral each). The residual (not exactly 50/50 either direction) is a
  separate, smaller effect — the raw per-congress credit distribution is
  itself right-skewed in both chambers (mean sits above median), so
  comparing against the population MEAN as "expected" will always put
  slightly more than half of any chamber below neutral; unlike the cross-
  chamber pooling bug this fix targets, that skew is symmetric across
  chambers and not something this pass changes.

  scripts/calibrate_les_credit_scale.py now reports both chambers'
  baselines separately instead of one pooled figure, so a future
  recalibration can't silently regress back to a single shared constant.
  Re-run it (and re-check the below-neutral split by chamber) after any
  pipeline run meaningfully shifts either chamber's bill-type/advancement-
  rate mix.

Changes from v6.9 -> v6.10 (2026-07-23): closed the smaller residual
imbalance v6.9 flagged but explicitly left open. After the v6.9 chamber
split, both chambers still had slightly more than half their members
scoring below the neutral midpoint on Legislative Effectiveness. That was
not a chamber-comparison bug — it was symmetric across both chambers — but
an artifact of the reference point itself: a member's per-congress credit
was scored against their chamber's population MEAN, and that distribution is
right-skewed (a minority of highly prolific sponsors pull the mean well
above where the typical member sits: 2026-07 audit Senate mean 285.3 /
median 254, House mean 122.0 / median 107). Scoring against a mean that sits
above the median puts >50% of any such distribution below neutral by
construction, regardless of real effectiveness.

  The fix centers the reference point on each chamber's MEDIAN instead of
  its mean (_LES_POPULATION_AVG_SENATE/_HOUSE renamed to
  _LES_POPULATION_MEDIAN_SENATE/_HOUSE, 254 / 107), so the typical member
  now scores ~50 and each chamber lands near a 50/50 split around neutral.
  This is the same "each chamber's own median scores 50" calibration
  convention every other expected-vs-actual component in this file already
  follows (Funding Independence's small-donor state baseline, Funding
  Diversity's chamber-median multiplier, Constituent Alignment's cohort
  median) — Legislative Effectiveness was the lone holdout still centered on
  a mean. The status_ratio adjustment (member vs. chamber-average
  _advancement_baseline) is unchanged: it only tilts the median bar up or
  down for a member's own majority/minority bill mix. scripts/
  calibrate_les_credit_scale.py now reports the median as the suggested
  constant; re-run it after any pipeline run meaningfully shifts either
  chamber's per-congress credit distribution.

Changes from v6.10 -> v6.11 (2026-07-23) [position congruence's seat-safety
scaling and DW-NOMINATE source superseded in v6.13]: Constituent Alignment restructured
around the construct its own notes kept naming, prompted by a direct design
question ("bipartisan doesn't always mean aligned to your constituents —
what signals would make this metric stronger?"). Two changes, one shared
finding: coalition breadth was a legislative-STYLE signal living inside a
representation dimension.

  1. Coalition Breadth MOVED out of Constituent Alignment into Legislative
     Effectiveness, reframed as "Bipartisan coalition attraction" (15%,
     from LES 70->60 and leadership 30->25; both revert exactly when data
     is missing). Fairness check run before the move, not after: the
     political-science case for bipartisanship as an EFFECTIVENESS signal
     is strong — Harbridge-Yong, Volden & Wiseman (2023, "The Bipartisan
     Path to Effective Lawmaking," J. Politics 85:3; 93rd-114th
     Congresses) find members who attract a larger share of their bill
     cosponsors from the opposing party are substantially more successful
     lawmakers, robust for majority AND minority members — while its case
     as a CONSTITUENT-ALIGNMENT signal was always weak (Harbridge &
     Malhotra 2011: demand for bipartisanship varies with seat
     composition; a bipartisan member of a lopsided seat can be
     bipartisan and misaligned at once — and v6.8 had already caught the
     component partially re-measuring this dimension's position signal,
     r=-0.76). Two fidelity details from the same check: HVW's effect is
     specifically the ATTRACTION of cross-party cosponsors, not the offer
     of them, so the LE component consumes a new receive-only rate
     (compute_bipartisanship_scores(direction="receive")) instead of the
     Lugar-style give+receive blend (which keeps powering the profile
     display unchanged); and their evidence is a robust association, not
     clean causal identification, one reason the weight stays modest. The
     old component's seat-safety scaling does NOT move with it —
     representation logic has no place in an effectiveness dimension; low
     bipartisan attraction predicts lower lawmaking success regardless of
     seat.

  2. Position congruence ADDED to Constituent Alignment (30% when data
     exists; seat-relative vote alignment holds the rest, 100% when not).
     This is the "positive half" the v6.6 limitation note left open and
     the construct-validity fix the v6.6 "DELIBERATELY NOT SHIPPED" note
     said was impossible with cosponsorship-SVD ideology: a ROLL-CALL
     ideal point (DW-NOMINATE dim1, Voteview/Lewis et al. — the exact
     signal that note wished for) scored against a seat-conditional
     expectation (per-chamber, per-party OLS of position on seat PVI).
     District-relative ideological extremity is the misrepresentation
     construct with the strongest electoral-accountability evidence
     (Canes-Wrone, Brady & Cogan 2002, "Out of Step, Out of Office"), and
     unlike a defection rate it is a legible POSITION signal: flank-ward
     deviation from the seat-conditional party norm scores below neutral
     (seat-safety-scaled to zero in deep safe seats — Bafumi & Herron
     2010 — the v6.7 pattern), center-ward deviation scores above (the
     surplus-crossing credit pattern, floor 0.25). A genuinely congruent
     loyalist can now score above 50, which no rate-based signal could
     ever produce. When active it SUPERSEDES the v6.7 loyal-branch
     position-mismatch discount (same construct, better signal; applying
     both would repeat the v6.8 double-count); the discount remains as
     the fallback when data is absent. Every number the formula consumes
     (per-member dim1, regression coefficients, p90-|extremity|
     saturation) is ingested AUTOMATICALLY each pipeline run
     (fetch/voteview.py: fetch from Voteview, per-party OLS fit,
     ingestion gates in the fetch_state_pvi.py mold, then
     write_member_ideal_points to /data/member_ideal_points.json on the
     writable volume — the party_ideology_bounds.json pattern; no manual
     step exists) — no Python-side magnitude constants, honoring the
     no-un-fit-constants rule. The component is inert only until the
     FIRST successful ingest (skipped, weight renormalized, vote
     component carries 100%); a later fetch/gate failure keeps the last
     good data rather than degrading scores.

  Known limitations, disclosed: (a) both CA components are now roll-call-
  derived (break rate vs. spatial position) — different constructs,
  related data; re-check their live correlation after the first full run,
  same standing check as v6.8's; (b) NOMINATE congruence measures
  congruence with the GEOGRAPHIC seat median (via PVI), while members
  systematically track their reelection constituency (Fenno 1978; Clinton
  2006; Bafumi & Herron 2010) — the seat-safety scaling is the deliberate
  humility about that, not a fix for it; (c) LE components 2 and 3 are
  both cosponsorship-derived (centrality vs. cross-party share), kept at
  a combined 40% and flagged for the same post-run correlation check.
  Post-run validation: the derived consistency gate (ground_truth.py,
  replaces the old hand-maintained GROUND_TRUTH table) +
  scripts/check_signal_correlations.py (the standing check for (a) and
  (c), one command against the live API).

  Confirmed against real Voteview data, first live ingest (2026-07-23):
  Senate Republicans' seat-PVI-vs-position fit is not statistically real
  (OLS/Theil-Sen/Spearman all agree, robust to outlier removal — see
  fetch/voteview.py's module docstring for the full measurement).
  Senate Democrats' fit IS real. Gating is per-CHAMBER, not per party
  (deliberately — a nonpartisan platform cannot ship a component only
  one party can structurally earn), so position congruence is currently
  House-only: both House parties' fits pass, neither Senate party's
  fit is used until the Senate's own data supports both symmetrically.
  Re-measured fresh every pipeline run, not a fixed assumption.

  Signals evaluated for v6.11 and DELIBERATELY NOT SHIPPED (same
  documented-so-nobody-re-litigates-blind pattern as the v6.6 crossing-
  side flank discount; each names its precise blocker, because only one
  of the three is "blocked" — the other two are rejected outright):

  - CES vote-matched opinion congruence (Ansolabehere & Jones 2010,
    AJPS): the Cooperative Election Study asks ~60k respondents each
    cycle how THEY would vote on specific named roll calls — the gold-
    standard "did the member vote how constituents wanted" construct,
    and the strongest candidate signal this dimension doesn't have.
    REJECTED on the merits, not blocked: CES items reference the
    PREVIOUS congress's votes (CES 2024 -> 118th), while v5.8's "current
    term" rule scores only the current congress. Scoring a member's
    CURRENT-term record against a PRIOR-term opinion survey means the
    "constituent" half of the comparison predates some of the votes
    being judged — not a data-quality gap to tolerate, a construct
    mismatch, the same category of problem v6.6 found in the crossing-
    side flank discount above. Not revisited unless CES starts
    publishing same-cycle common content, which it structurally does
    not (the survey runs alongside the election it covers, so the
    current congress's votes can't yet exist when it's fielded).
  - Congressionally Directed Spending / allocation responsiveness (Stein
    & Bickers 1994; Grimmer, Messing & Westwood 2012; Grimmer 2013 shows
    members in seats leaning against their party rationally SUBSTITUTE
    appropriations work for position-taking — meaning a vote-only metric
    structurally undervalues exactly those members; Eulau & Karps 1977
    call this the "allocation responsiveness" channel). The only
    genuinely orthogonal candidate (not derived from votes, cosponsors,
    or money-in). Blocker: current-cycle CDS disclosures are per-
    subcommittee xlsx/PDF tables on appropriations.senate.gov (House:
    committee PDFs), which need format-inspected, ingestion-gated
    parsers — this file's own standards forbid shipping a parser written
    blind against uninspected files, and no reputable machine-readable
    mirror covering the current congress exists (the one public dataset,
    BPC's, stops at FY2022 = the 117th). Fair-scoring note for whoever
    builds it: appropriators secure structurally more CDS than non-
    appropriators, so the baseline must be committee-conditional or the
    component becomes a committee-membership proxy.
  - Tausanovitch & Warshaw MRP seat ideology (americanideologyproject.
    com) as a second seat-lean input: REJECTED on the merits, not
    blocked. It is another one-dimensional ordering of seats that
    correlates strongly with PVI; adding it as a separate signal would
    recreate exactly the correlated-pair problem this file spent
    v6.5-v6.8 removing (r=0.72 funding pair; r=-0.76 cosponsorship
    pair). Its one legitimate future use is as a cross-validation anchor
    on PVI inside fetch_member_ideal_points.py's gates — not as an
    opinion source for issue-level congruence, since CES (the item
    above) is rejected for the same reason on this axis: current-term
    scoring needs current-term opinion data, which neither source
    supplies.
"""

import logging
import math
import statistics

from app.models import PromiseAlignment
from app.pipeline.analyze.population_reference import (
    CONSTITUENT_REFERENCE,
    FUNDING_REFERENCE,
    LES_REFERENCE,
)

logger = logging.getLogger(__name__)

# Bump when scoring formulas or their data inputs change in a way that
# shifts scores. Recorded on every ScoreSnapshot so trend charts can
# annotate methodology changes; keep frontend/src/lib/scoreVersions.ts
# in sync — it holds the full public changelog (v3 onward) and is the
# place to read for the complete history. This comment block is a
# shorter dev-facing index of the same transitions, starting where the
# "Changes from vX -> vY" narrative above (Academic rationale section)
# does; earlier per-version detail lives only in scoreVersions.ts.
#
# v5.10 (2026-07): two Promise Persistence fixes — excluded ceremonial
# resolutions from bill-derived "promises" (same filter SUBSTANTIVE_
# BILL_TYPES already applies elsewhere) and resized PRIOR_PSEUDOCOUNT
# 6->3 after finding real evidence volume (~0.5 evaluable promises/
# senator) was far below what the prior pseudocount assumed. See the
# "v5.10" paragraph above for the full derivation.
#
# v5.11 (2026-07): replaced donor-vote "lobbying connection" detection's
# uncalibrated 0.35 raw-text-similarity threshold with a two-stage,
# measured gate (donor industry share of classified funding >=25%, plus
# industry/vote policy-area similarity >=0.75). See the "v5.11"
# paragraph above.
#
# v5.12 (2026-07): Legislative Effectiveness's leadership component
# (cosponsorship PageRank) stopped punishing low tenure — missing data
# now defaults to neutral 50 instead of a punitive 40, and raw
# percentile is shrunk toward neutral by a tenure-scaled confidence
# factor. See the "v5.12" paragraph above.
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
#
# v6.5 -> v6.6 (2026-07): Constituent Alignment's over-loyalty branch floors
# at neutral (50) instead of dropping to as low as 25 — a below-expected
# party-defection rate is no longer scored as misrepresentation. Single,
# deterministic behavioral change (no calibration constant). A companion
# member-ideology directional discount for the crossing side was designed but
# deliberately NOT shipped — its magnitude can't be fit without the live
# scored ideology distribution, and this file does not ship un-fit
# calibration constants. See the top-of-file "Changes from v6.5 -> v6.6"
# note for the full account, citations, and the not-shipped rationale.
#
# v6.6 -> v6.7 (2026-07): added a position-mismatch discount to that same
# over-loyalty branch: a below-expected loyalist whose ideology_score sits
# in their own party's extreme tercile, representing a seat that isn't
# safely aligned for that extremity, is discounted below neutral (up to
# POSITION_MISMATCH_MAX_PENALTY, seat-direction-scaled). Answers the "a
# blatantly out-of-step loyalist floors at neutral forever" gap v6.6 left
# open — a second, legible signal (WHERE the member sits, not how often
# they cross) the branch previously ignored entirely. Magnitude fit against
# the live ideology_score + seat-lean distribution (grid search vs.
# GROUND_TRUTH + the IV stdev floor), not guessed. See the top-of-file
# "Changes from v6.6 -> v6.7" note for the full account.
#
# v6.7 -> v6.8 (2026-07-21): a fairness audit found the position-mismatch
# discount and Coalition Breadth double-count the same cosponsorship-derived
# signal (r=-0.76 between ideology_score extremity and bipartisanship_score,
# n=99), over-penalizing safe-seat members with no real-world flank
# reputation (Duckworth, Murphy, Booker all scored 32-36 pre-fix).
# POSITION_MISMATCH_MAX_PENALTY cut 25.0 -> 10.0 and Coalition Breadth's
# below-median case now gets the same seat-safety scaling every other branch
# here already has. See the top-of-file "Changes from v6.7 -> v6.8" note.
#
# v6.8 -> v6.9 (2026-07-21): a platform-wide audit (prompted the same night
# as v6.8, different dimension) found Legislative Effectiveness's
# _LES_AVG_BASELINE was a single value pooled across both chambers, even
# though House and Senate members' real _advancement_baseline rates
# genuinely differ. Comparing every member against one cross-chamber
# average silently inflated House members' expected-credit bar and
# deflated the Senate's — live population effect: 61% of House scored
# below neutral vs only 38% of the Senate (means 44.6 vs 60.5, n=431/101),
# despite both chambers performing comparably once measured against their
# own chamber's real baseline. Split into _LES_AVG_BASELINE_SENATE/_HOUSE,
# the same pattern _LES_POPULATION_MEDIAN_SENATE/_HOUSE already used — the
# two constants were meant to work together chamber-specifically and were
# silently fighting each other. See the top-of-file "Changes from v6.8 ->
# v6.9" note.
#
# v6.9 -> v6.10 (2026-07-23): closed the smaller residual imbalance v6.9
# explicitly left open — even after the chamber split, slightly more than
# half of each chamber scored below neutral because the reference point was
# each chamber's population MEAN, and the per-congress credit distribution
# is right-skewed (a minority of prolific sponsors pull the mean above the
# typical member). Switched the reference to each chamber's MEDIAN
# (_LES_POPULATION_AVG_* renamed to _LES_POPULATION_MEDIAN_*), the same
# "median member scores 50" convention every other expected-vs-actual
# component in this file already uses. Symmetric across both chambers, not a
# House/Senate bias. See the top-of-file "Changes from v6.9 -> v6.10" note.
#
# v6.10 -> v6.11 (2026-07-23): Coalition Breadth moved out of Constituent
# Alignment into Legislative Effectiveness as "Bipartisan coalition
# attraction" (receive-only rate per Harbridge-Yong/Volden/Wiseman 2023 —
# attracting cross-party cosponsors predicts lawmaking success; offering
# them doesn't carry the effect), and Constituent Alignment gained a
# Position congruence component (30% when data exists): DW-NOMINATE dim1
# vs. a seat-conditional per-party expectation (Canes-Wrone/Brady/Cogan
# 2002's district-relative extremity — the construct v6.6/v6.7's notes
# named but couldn't measure from cosponsorship SVD). Supersedes the
# loyal-branch position-mismatch discount when active. Ideal points are
# ingested automatically every pipeline run (fetch/voteview.py ->
# /data/member_ideal_points.json, gated; no manual step) — the component
# is inert only until the first successful ingest. See the top-of-file
# "Changes from v6.10 -> v6.11" note for the full account.
#
# v6.11 -> v6.12 (2026-07-23): Funding Independence recalibration — PAC
# caps and election windows, refit together per that audit's own
# instruction not to move them separately.
# Two constants had drifted hard from a fresh live audit (n=532, both
# chambers): the fallback PAC-dollar penalty (no contributing PAC has a
# resolved committee type) assumed a $2.0M median/$4.4M p90; live is
# $662,750/$1,915,242 — roughly a third. Top-donor concentration assumed a
# 60% median (0.20->100/1.00->0 anchors); live median is 28% (confirmed
# per chamber, not a mixing artifact: Senate 30.5%, House 27.5%), so the
# typical member was scoring ~90/100 on this component regardless of real
# concentration — almost no signal for the bulk of the population. Both
# refit to the live distribution (see _funding_independence_core's inline
# comments for the exact derivation); FEC PAC contribution caps themselves
# ($5,000/$3,500 per election) are unchanged — those are legal limits, not
# empirical calibration, and still current for the 2025-2026 cycle.
#
# v6.12 -> v6.13 (2026-09): each roll call now counts once in a member's
# record. The Senate pipeline fed every recent roll call into the record
# twice and deduplicated the key/recent split by billId against only the
# non-key leftovers, so a recent roll call picked as a key vote stayed in
# both lists — and select_key_votes favours party breaks, so the duplicated
# votes were mostly breaks (measured: 20 roll calls / 2 breaks scored as
# 25 / 4, Constituent Alignment 54 -> 66 for a swing-state member). In
# both chambers, a key bill whose floor vote was also a recent roll call
# was counted through both paths. Votes are now identified by roll call
# (normalize_votes.vote_identity), never billId. No formula changed; the
# inputs were wrong.
#
# Also v6.13 — Legislative Effectiveness's population reference is measured
# every run (compute_les_reference) instead of hand-copied constants, and
# the current congress's majority comes from the live roster
# (derive_chamber_majority): the hardcoded majority table ended at the
# 119th Congress, and a median frozen mid-congress drifted as credit
# accumulated. Saturation is now each chamber's own 1.5 stdev rather than a
# pooled value. Senate sponsored bills are stage-classified before scoring;
# they used to be classified after calculate_scores ran, so Senate LE was
# scored on the latestAction keyword fallback while the House used stages.
#
# Also v6.13 — funding: (1) scored on the most recent COMPLETED election,
# not a re-election campaign still in progress (select_recent_elections);
# (2) itemized detail covers the election's full period, six years Senate /
# two House (election_period_cycles); (3) shares are taken over
# contributions + candidate self-loans, not receipts, which include JFC
# transfers and understated PAC reliance for members who fundraise through
# them (summarize_election_totals); (4) the PAC-share multipliers 3.2 / 1.35
# (0.5 / a hand-typed chamber median) are replaced by the chamber median
# measured every run (compute_funding_reference).
#
# Also v6.13 — Legislative leadership: a 0.0 PageRank score (the chamber's
# lowest member, by construction of the [0, 1] rescale) is scored, not
# treated as missing data; and a cosponsored bill with no outcome data gets
# the mean known edge weight instead of the ENACTED maximum.
#
# Also v6.13 — the remaining hand-typed calibrations are measured each run
# (AGENTS.md §3a): the fallback PAC-dollar scale (2 x chamber median PAC
# dollars), top-donor concentration (chamber median = 50, one p10-p90
# spread saturates — replaces fixed 0.15/0.40 anchors), House small-donor
# share (House median = 50 — replaces a flat 40% cap), and the majority/
# minority advancement rates. The duplicate small-donor-fit literal is gone.
#
# Also v6.13 — Constituent Alignment rebuilt on evidence (see
# _calc_constituent_alignment and docs/research/constituent-alignment.md).
# Every choice was tested against 2,545 House re-election results
# (1994-2010): the measured per-party seat expectation replaces the
# hand-set break-rate curve; below-expected loyalty now scores below
# neutral (the v6.6 floor discarded the strongest signal in the test);
# neither component is scaled by seat safety (no interaction in the data);
# position congruence is symmetric and uses congress-specific Nokken-Poole
# positions. Removed with it: the v6.7 cosponsorship-SVD position-mismatch
# discount, CROSSING_QUALITY_DISCOUNT (inert at 0.0; flank-side defectors
# showed no penalty to discount for), and the party_ideology_bounds.json
# file that only the discount read. The v6.6-v6.11 notes above describe
# designs this replaces.
#
# Also v6.13 — Funding Independence counts each signal once (see
# _calc_funding_independence and docs/research/funding-independence.md;
# FEC bulk data, 2020-2024 incumbents): outside spending left the PAC share
# (it tracked race competitiveness, Spearman with |PVI| -0.37/-0.39, and
# ran opposite to PAC share), source breadth was removed (R^2 0.79-0.86
# with the small-donor share), and the industry-concentration fallback is
# a neutral 50 instead of a third copy of the small-donor share. The
# remaining weights keep their proportions (20:10:10:13 over 53).
ALGORITHM_VERSION = "v6.13"

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
    """Weighted overall score from a scored Senator/Representative row, or
    from a plain representationScore-shaped dict (camelCase keys matching
    SCORE_WEIGHTS directly — api/public.py's serialized API responses).

    Shared by senate_pipeline.py's and house_pipeline.py's daily
    ScoreSnapshot recorders and api/public.py's response builders — all
    weight the same score_* fields by the same config_definitions.
    SCORE_WEIGHTS, previously copy-pasted in each. Sums dynamically over
    SCORE_WEIGHTS.items() rather than naming each dimension, so a weight-
    table change (e.g. removing a dimension) can't silently desync this
    formula from the config again — that gap was exactly what made the
    promisePersistence removal above require an audit of 7 independently
    hardcoded copies instead of touching one file.
    """
    from app.config_definitions import SCORE_WEIGHTS

    if isinstance(entity, dict):
        overall = sum(entity.get(key, 0) * weight for key, weight in SCORE_WEIGHTS.items())
    else:
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
#
# 2026-07 (a disclosed exception): this only runs for bills where isLaw
# is already false (the strongest, non-keyword "did this advance" signal
# — becoming law — is checked first and short-circuits before this ever
# runs; see _cosponsorship_edge_weight). Live-measured against 1366 real
# distinct latestAction strings from production: 352 matched these
# keywords, and only 1 (0.28%) contained a plausible false-positive
# pattern — a double-clause sentence where an unrelated procedural
# sub-motion ("agreed to") was matched instead of the bill's own outcome
# ("...failed of passage... agreed to [the motion to table
# reconsideration]"). An embedding check against short institutional
# procedural phrases wouldn't obviously do better on a case this specific
# and rare; the keyword match's transparency and determinism are worth
# more here than chasing one edge case in 352.
_ADVANCEMENT_ACTION_KEYWORDS = ("passed", "agreed to", "ordered to be reported")

# Tenure (years) at which a member's raw cosponsorship-PageRank leadership
# score is trusted at full face value. Below it, the score is shrunk toward
# neutral because PageRank centrality is structurally a function of network
# size, which takes years to build — a freshman's near-zero percentile
# reflects time in office, not ineffectiveness (2026-07 audit; see the
# v5.12 changelog note). Scaled to a full 6-year Senate term: long enough to
# plausibly build a real network, short enough that a second-term member
# isn't still getting a pass. Shared by Legislative Effectiveness's
# leadership component (_legislative_effectiveness_core) and the displayed
# leader/follower label (sponsorship_analysis.describe_senator_position), so
# the same "seniority alone is never penalized" correction applies to both.
LEADERSHIP_TENURE_FULL_CREDIT_YEARS = 6.0

# Per-state Cook PVI (positive = R lean, negative = D lean) — the seat
# expectation for SENATORS, the state-level analog of the per-district
# district_pvi.json used for House members (_district_pvi below).
#
# COMPUTED from real presidential returns, not a hand-typed table: the
# checked-in app/data/state_pvi.json is generated by
# scripts/fetch_state_pvi.py, which derives each state's PVI from MEDSL's
# official 2016 & 2020 vote counts via Cook's published formula (the two-
# election average of the state's two-party Democratic vote share minus
# the national two-party Democratic share, negated so positive = R lean).
# It reproduces Cook Political Report's 2022 published state PVIs within
# +/-1 (the generator gates on this). Refresh it the same way
# district_pvi.json / state_population.json are refreshed — rerun the
# script and commit the regenerated JSON — after a new presidential
# election shifts the two-cycle window.

_state_pvi_cache: dict[str, int] | None = None


_PVI_PERSISTENT_DIR = "/data"


def _read_pvi_json(filename: str) -> dict:
    """Read a PVI JSON file, preferring the persistent volume's
    auto-refreshed copy (/data/, written by an automated fetch module) over
    the bundled git-tracked fallback (app/data/, updated only by manually
    running a scripts/fetch_*.py script). Same override pattern as
    transform/committee_data.py's loader."""
    import json
    import pathlib
    bundled_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "data"
    for directory in (pathlib.Path(_PVI_PERSISTENT_DIR), bundled_dir):
        try:
            return json.loads((directory / filename).read_text())
        except Exception:
            continue
    return {}


def _state_pvi() -> dict[str, int]:
    """Per-state Cook PVI ("ST" -> signed int, positive = R lean).

    Ingested from state_pvi.json (generated by scripts/fetch_state_pvi.py
    — see the comment above; unlike district_pvi.json this is NOT
    refreshed automatically — see ops_alerts.check_state_pvi_staleness for
    why). Falls back to an empty map if the file is unavailable, exactly
    like _district_pvi(): an unresolved state then reads as lean 0 (a
    neutral swing seat) via the .get(state, 0) call sites, rather than
    crashing scoring or silently substituting stale hand-entered numbers.
    Missing data is never punitive — the same degrade-gracefully
    convention as every other loader in this file.
    """
    global _state_pvi_cache
    if _state_pvi_cache is None:
        raw = _read_pvi_json("state_pvi.json")
        if raw.get("states"):
            _state_pvi_cache = {k: int(v) for k, v in raw["states"].items()}
        else:
            logger.error(
                "state_pvi.json unavailable — every senator's seat lean will "
                "default to neutral (0); regenerate with scripts/fetch_state_pvi.py"
            )
            _state_pvi_cache = {}
    return _state_pvi_cache

_member_ideal_points_cache: dict | None = None


_MEMBER_IDEAL_POINTS_PATH = "/data/member_ideal_points.json"


def _member_ideal_points(chamber: str) -> dict:
    """Roll-call ideal-point data for one chamber ("senate" or "house"):
    {"members": {bioguideId: nominate_dim1}, "fit": {party: {"a", "b"}},
    "extremity_p90": float}. Used by _constituent_alignment_core's
    position-congruence component (v6.11) — the member's DW-NOMINATE
    first-dimension position scored against a seat-conditional
    expectation.

    Ingested from /data/member_ideal_points.json (the persistent
    writable volume — same one party_ideology_bounds.json lives on, and
    for the same reason: this is generated by the running app itself,
    not an offline artifact). Each chamber's pipeline refreshes its own
    section EVERY RUN from Voteview's published DW-NOMINATE estimates
    (Lewis et al., voteview.com) joined against the seat-lean tables
    (fetch/voteview.py — fetch, per-party OLS fit, ingestion gates,
    then write_member_ideal_points below). Fully automated: no manual
    generation step exists. Every number the scoring formula consumes
    (positions, regression coefficients, saturation scale) is computed
    from real data by that ingest, never hand-typed here.

    Falls back to an empty dict if the file or the chamber's key is
    missing — the position-congruence component is then skipped entirely
    (never scored neutral, exactly like coalition breadth's old
    missing-data handling), and constituent alignment runs on the
    seat-relative vote component alone. So the component is inert only
    until the FIRST successful ingest; a later fetch/gate failure keeps
    the last good data (stale beats punitive, and DW-NOMINATE moves
    slowly week to week). Missing data is never punitive — same
    convention as every other loader in this file.
    """
    global _member_ideal_points_cache
    if _member_ideal_points_cache is None:
        import json
        import pathlib
        path = pathlib.Path(_MEMBER_IDEAL_POINTS_PATH)
        try:
            _member_ideal_points_cache = json.loads(path.read_text())
        except Exception:
            logger.warning(
                "member_ideal_points.json unavailable — position-congruence "
                "component will be skipped for every member until the first "
                "successful Voteview ingest (fetch/voteview.py, runs "
                "automatically each pipeline run)"
            )
            _member_ideal_points_cache = {}
    chamber_data = _member_ideal_points_cache.get(chamber)
    return chamber_data if isinstance(chamber_data, dict) else {}


def write_member_ideal_points(chamber: str, data: dict) -> None:
    """Persist one chamber's gated ideal-point section (fetch/voteview.py's
    build output) to /data/member_ideal_points.json. Read-merge-write, not
    overwrite — the two chamber pipelines run independently and each owns
    only its own section, exactly like write_party_ideology_bounds above.
    Callers gate BEFORE calling (refresh_member_ideal_points): this
    function persists what it's given.

    Never raises: best-effort side artifact, a write failure must not
    abort an otherwise-successful pipeline run (the loader then serves
    the previous file, or skips the component) — same contract and same
    hard-learned rationale as write_party_ideology_bounds.
    """
    import json
    import pathlib
    global _member_ideal_points_cache
    path = pathlib.Path(_MEMBER_IDEAL_POINTS_PATH)
    try:
        try:
            existing = json.loads(path.read_text())
        except Exception:
            existing = {}
        from app.pipeline.fetch.voteview import METHOD_DESC, SOURCE_DESC
        existing["_source"] = SOURCE_DESC
        existing["_method"] = METHOD_DESC
        existing[chamber] = data
        path.write_text(json.dumps(existing, indent=1, sort_keys=True) + "\n")
        _member_ideal_points_cache = None  # force reload next _member_ideal_points() call
    except Exception:
        logger.warning(
            "Failed to write member_ideal_points.json for %s — position-"
            "congruence will use stale or empty data until the next "
            "successful write; pipeline run continues.",
            chamber, exc_info=True,
        )


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

    Ingested from each district's Wikipedia infobox — refreshed
    automatically (weekly, or immediately if missing) by
    app/pipeline/fetch/district_pvi.py to /data/district_pvi.json; the
    bundled app/data/district_pvi.json is only the pre-first-ingest
    fallback (all 435 seats incl. vacancies; ingestion gates documented in
    the fetch module). State PVI is the wrong seat expectation for House
    members in split states — a D+19 urban district in a red state was
    scored as an "opposed seat" whose member should cross party lines
    ~20% of the time, when the seat actually elected exactly that
    platform.
    """
    global _district_pvi_cache
    if _district_pvi_cache is None:
        raw = _read_pvi_json("district_pvi.json")
        if raw.get("districts"):
            _district_pvi_cache = {k: int(v) for k, v in raw["districts"].items()}
        else:
            logger.warning("district_pvi.json unavailable — falling back to state PVI")
            _district_pvi_cache = {}
    return _district_pvi_cache


def get_state_pvi_map() -> dict[str, int]:
    """Public read-only accessor for _state_pvi() (2026-07, midterm-elections
    feature): api/elections.py exposes this data publicly for the first
    time (previously internal-only, scoring-only). A thin wrapper rather
    than importing the underscore-prefixed cache function directly keeps
    the "this file's own scoring internals" vs. "what's safe to expose
    externally" boundary explicit."""
    return dict(_state_pvi())


def get_district_pvi_map() -> dict[str, int]:
    """Public read-only accessor for _district_pvi() — see get_state_pvi_map."""
    return dict(_district_pvi())


def get_pvi_meta() -> dict:
    """Provenance metadata from the two PVI data files (their _source/
    _method/_window/_as_of keys), for public labeling (2026-07 review F7:
    publishing the bare numbers with no source, election window, or
    lean-is-not-a-forecast caveat over-claims what PVI measures — the
    files carry this metadata precisely so an exposure surface can show
    it). Values are None when a file lacks a key or is unavailable; the
    API/frontend degrade to generic wording rather than invent provenance."""
    meta: dict = {}
    for key, fname in (("states", "state_pvi.json"), ("districts", "district_pvi.json")):
        raw = _read_pvi_json(fname)
        meta[key] = {
            "source": raw.get("_source"),
            "method": raw.get("_method"),
            "window": raw.get("_window"),
            "asOf": raw.get("_as_of"),
        } if raw else None
    meta["note"] = (
        "Cook-PVI-style partisan lean relative to the national presidential "
        "vote. Measures how a state or district leans, not who will win a "
        "specific race — incumbency, candidate quality, and open seats are "
        "not part of this number."
    )
    return meta


def calculate_scores(senator: dict) -> dict:
    """
    Calculate the representation sub-scores from real data — the three in
    SCORE_WEIGHTS plus the informational Promise Persistence and Funding
    Diversity.

    Returns:
        Dict with those five representationScore sub-fields.
    """
    voting_record = senator.get("votingRecord", {})
    funding = senator.get("funding", {})
    lobbying_matches = senator.get("lobbyingMatches", [])

    return {
        "fundingIndependence": _calc_funding_independence(
            funding, senator.get("state", ""), senator.get("district"),
            senator.get("fundingReference"),
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
            district=senator.get("district"),
            bioguide_id=senator.get("bioguideId"),
            reference=senator.get("constituentReference"),
        ),
        "fundingDiversity": _calc_funding_diversity(funding),
        "legislativeEffectiveness": _calc_legislative_effectiveness(
            senator.get("sponsoredBills", []),
            senator.get("leadershipScore"),
            party=voting_record.get("effectiveParty") or senator.get("party", "I"),
            years_in_office=senator.get("yearsInOffice"),
            attracted_bipartisanship=senator.get("attractedBipartisanshipScore"),
            les_reference=senator.get("lesReference"),
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
            senator.get("fundingReference"),
        ),
        "independentVoting": _constituent_alignment_core(
            voting_record,
            lobbying_matches,
            funding,
            senator.get("state", ""),
            senator.get("party", "I"),
            district=senator.get("district"),
            bioguide_id=senator.get("bioguideId"),
            reference=senator.get("constituentReference"),
        ),
        "fundingDiversity": _funding_diversity_core(funding),
        "legislativeEffectiveness": _legislative_effectiveness_core(
            senator.get("sponsoredBills", []),
            senator.get("leadershipScore"),
            party=voting_record.get("effectiveParty") or senator.get("party", "I"),
            years_in_office=senator.get("yearsInOffice"),
            attracted_bipartisanship=senator.get("attractedBipartisanshipScore"),
            les_reference=senator.get("lesReference"),
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

    has_funding = funding_share_base(funding) > 0
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
    scripts/fetch_state_small_donor_baseline.py). The file ships in the
    image; if it is ever unreadable the fit is empty and the small-donor
    component scores neutral (_small_donor_capacity_score) rather than
    falling back to a second hand-typed copy of the same numbers, which is
    what used to live here (AGENTS.md §3a, point 3).
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
            logger.error(
                "small_donor_baseline.json unreadable — the small-donor component "
                "scores neutral until it is restored (scripts/fetch_state_small_donor_baseline.py)"
            )
            _small_donor_baseline_fit_cache = {}
    return _small_donor_baseline_fit_cache


def _state_small_donor_baseline(state: str) -> float:
    """Expected small-donor % for a state's population. Unresolved states
    (unknown code, DC, territories) fall back to the national mean so an
    unresolvable state is never itself a penalty or a windfall."""
    fit = _small_donor_baseline_fit()
    if not fit:
        return 0.0
    pop = _state_population().get(state)
    if not pop:
        return fit["national_mean_pct"]
    expected = fit["A"] + fit["B"] * math.log(pop)
    return max(fit["min_expected_pct"], min(fit["max_expected_pct"], expected))


def _small_donor_capacity_score(
    small_pct: float, state: str, district: int | None, chamber_ref: dict | None = None,
) -> tuple[float, float]:
    """Small-donor credit relative to what's expected for the seat, not a
    flat absolute cap. Returns (score, expected_pct).

    Senate: expected from the state's population (the regression in
    small_donor_baseline.json — bigger states have bigger natural donor
    pools), saturating at the fit's saturation point.

    House: districts are apportioned to ~760k people each, so population
    doesn't differentiate them; expected is the House's own median
    small-donor share, saturating at one p10-p90 spread — both measured
    each run (compute_funding_reference). This replaced a flat 40% cap
    (v6.13) that was hand-set and put the typical House member near 45
    rather than 50, unlike every other chamber-relative component.
    """
    if district is not None:
        ref = chamber_ref or {}
        median = ref.get("small_donor_median")
        spread = (ref.get("small_donor_p90") or 0) - (ref.get("small_donor_p10") or 0)
        if median is None or spread <= 0:
            return 50.0, median or 0.0
        return max(0.0, min(100.0, 50.0 + 50.0 * (small_pct - median) / spread)), median

    fit = _small_donor_baseline_fit()
    if not fit:
        return 50.0, 0.0
    expected = _state_small_donor_baseline(state)
    saturation = fit["saturation_pt"]
    if small_pct >= expected:
        surplus = small_pct - expected
        score = 50.0 + 50.0 * min(surplus / saturation, 1.0)
    else:
        deficit = expected - small_pct
        score = 50.0 - 50.0 * min(deficit / saturation, 1.0)
    return score, expected


def _calc_funding_independence(
    funding: dict, state: str = "", district: int | None = None, reference: dict | None = None,
) -> int:
    """
    Funding Independence Score (0-100, higher = better).

    Measured on the member's most recent completed election, with every
    share taken over contributions (fetch/fec.select_recent_elections,
    normalize_finance.summarize_election_totals). Four components; the
    chamber references each is scored against are measured every run
    (compute_funding_reference — AGENTS.md §3a):

      1. PAC dependency (20/53): PAC share of contributions, scored so the
         chamber's median member lands at 50 (House members rely on PAC
         money far more than senators — a structural difference, not a
         choice), then scaled by how close the contributing PACs ran to
         their legal per-election caps ($5,000 multicandidate / $3,500
         other, FEC 2025-26). Share alone has a scale bias: a $100M
         campaign dilutes millions of PAC dollars to a small share
         (2026-07 audit: FI vs log(total raised) r=+0.68), so the cap
         utilization of each PAC with a known committee type measures
         the depth of the commitment directly. Without committee-type
         data, the absolute PAC dollars are scaled against twice the
         chamber median instead.
      2. Small-donor share (10/53): unitemized (<$200) contributions,
         against what the state's size predicts for senators
         (small_donor_baseline.json) and against the House median for
         representatives (_small_donor_capacity_score).
      3. Top-donor concentration (10/53): top-10 external donors as a share
         of the itemized external donor pool (self-funding and affiliated
         transfers excluded), scored against the chamber median with one
         p10-p90 spread saturating.
      4. Industry concentration (13/53): inverse HHI across classified
         industries (_industry_concentration), shrunk toward a neutral 50
         as less of the money is industry-classified.

    Removed in v6.13, each on measured evidence (FEC bulk data, 2020-2024
    incumbents with >$100K raised: 415 House / 86 Senate with seat lean;
    1,276 total; reproduce with scripts/audit_funding_components.py):

      - Outside spending in the PAC share. Independent expenditures
        supporting the member were added at half weight as "aligned-
        industry investment". They are by law not coordinated with the
        candidate (52 U.S.C. §30101(17)), 86% came from super PACs and
        hybrid PACs rather than industry PACs, and they track race
        competitiveness, not dependency: the term averaged 0.069 in House
        seats within 3 points of even vs 0.010 beyond 8 (Spearman with
        |PVI| -0.37 House, -0.39 Senate), and ran OPPOSITE to PAC share
        (-0.28 / -0.32). It penalized swing-seat members for money they
        cannot solicit or direct — about 28 points off the PAC component
        for the average senator in a seat within 3 points of even (8 in
        the House), against 8 and 1 in seats beyond 15. Super PACs
        concentrating in competitive races is also documented directly
        (Scala 2021, "Are Super PACs Super-Efficient?", State of the
        Parties).
      - Source breadth (13/66). It scored small-donor money at 1.0,
        industry money 0.6-0.5 and "opaque" money (party, the candidate's
        own) 0.2, so it was a second copy of the small-donor share: R^2
        0.79-0.86 against it across plausible classification rates. Its
        remaining signal penalized self-funding, the one money source no
        donor can influence.
      - The industry-concentration fallback toward "50 + 50 x small-donor
        share" when little money is classified — a third copy of the
        small-donor share. Unmeasurable concentration is now neutral 50,
        like every other missing input in this file.

    The remaining weights keep their pre-v6.13 proportions (20:10:10:13),
    renormalized over the four components — no new weighting judgment.

    Academic rationale
    ------------------
    Stratmann (2005, Public Choice 124(1-2), 135-156) finds a linear
    PAC-contribution-to-vote relationship — support for a linear (not
    step or logarithmic) PAC-dependency curve. Neither Barber (2016, POQ
    80(S1)) nor Bonica (2014, AJPS 58(2)) calibrates a PAC or concentration
    figure (verified by full-text search, 2026-07); every anchor here is
    measured from the chamber itself. Industry concentration follows
    Parmigiani (2025, J. Public Econ. 243), which computes the same HHI per
    legislator.
    """
    return _funding_independence_core(funding, state, district, reference)["score"]


def funding_share_base(funding: dict) -> float:
    """The denominator every funding share is taken over: contributions
    (from contributors or the candidate), falling back to total receipts
    for records that predate the field. See normalize_finance.
    summarize_election_totals for why receipts overstate it."""
    return funding.get("totalContributions") or funding.get("totalRaised", 0) or 0


# Fewest funded members that still describe a chamber's PAC-share
# distribution; below it the last persisted reference is kept.
_MIN_FUNDING_REFERENCE_MEMBERS = 30


# A donor pool is large enough to measure top-10 concentration from.
_CONCENTRATION_MIN_DONORS = 20
_CONCENTRATION_MIN_POOL = 250_000


def _top_donor_concentration(funding: dict) -> tuple[float | None, int, float]:
    """(top-10 share of the itemized external donor pool, #external donors,
    pool $) — the concentration is None when the pool is too small to
    measure. Shared by the score and its population reference."""
    external = sorted(
        (
            d for d in funding.get("topDonors", [])
            if d.get("type") not in ("CandidateAffiliated", "Self-Funded")
        ),
        key=lambda d: d.get("total", 0),
        reverse=True,
    )
    pool = sum(d.get("total", 0) for d in external)
    if len(external) >= _CONCENTRATION_MIN_DONORS and pool >= _CONCENTRATION_MIN_POOL:
        return sum(d.get("total", 0) for d in external[:10]) / pool, len(external), pool
    return None, len(external), pool


def compute_funding_reference(fundings: list[dict]) -> dict | None:
    """One chamber's Funding Independence reference from this run's
    members' funding dicts:

    - pac_ratio_median: median PAC share of contributions (the raw ratio,
      before the outside-spending adjustment — what scripts/audit_pac_ratio.py
      measured when the multipliers were hand-typed);
    - pac_dollars_median: median PAC dollars, the fallback volume scale for
      members whose PACs have no known committee type;
    - concentration_p10 / _median / _p90: top-10 donor concentration among
      members with a measurable pool.

    None when too few members have funding to measure the PAC share; the
    concentration stats are omitted (keep the last persisted ones) when too
    few members have a measurable pool."""
    ratios, dollars, concentrations, small = [], [], [], []
    for f in fundings:
        f = f or {}
        base = funding_share_base(f)
        if base > 0:
            pac = f.get("totalFromPACs") or 0
            ratios.append(min(pac / base, 1.0))
            dollars.append(pac)
            small.append(f.get("smallDonorPercentage") or 0)
        c, _, _ = _top_donor_concentration(f)
        if c is not None:
            concentrations.append(c)
    if len(ratios) < _MIN_FUNDING_REFERENCE_MEMBERS:
        return None
    ref = {
        "n": len(ratios),
        "pac_ratio_median": round(statistics.median(ratios), 6),
        "pac_ratio_mean": round(statistics.mean(ratios), 6),
        "pac_dollars_median": round(statistics.median(dollars), 2),
        "small_donor_p10": round(statistics.quantiles(small, n=10)[0], 4),
        "small_donor_median": round(statistics.median(small), 4),
        "small_donor_p90": round(statistics.quantiles(small, n=10)[8], 4),
    }
    if len(concentrations) >= _MIN_FUNDING_REFERENCE_MEMBERS:
        deciles = statistics.quantiles(concentrations, n=10)
        ref.update({
            "concentration_n": len(concentrations),
            "concentration_p10": round(deciles[0], 6),
            "concentration_median": round(statistics.median(concentrations), 6),
            "concentration_p90": round(deciles[8], 6),
        })
    return ref


def _funding_independence_core(
    funding: dict, state: str = "", district: int | None = None, reference: dict | None = None,
) -> dict:
    """Same math as _calc_funding_independence, returning every intermediate
    value alongside the final score. Single implementation — _calc_funding_
    independence and the on-demand explain_scores() breakdown both call this;
    neither reimplements the formula separately."""
    total_raised = funding_share_base(funding)
    if not total_raised or total_raised == 0:
        return {"score": 50, "components": [], "note": "No funding data — neutral default."}

    # Component 1: PAC dependency. Outside spending is deliberately not in
    # it (removed v6.13 — see the docstring's measured account).
    pac_total = funding.get("totalFromPACs", 0)
    pac_ratio = pac_total / total_raised

    # Chamber-relative: the chamber's MEDIAN PAC share scores 50
    # (multiplier = 0.5 / median). House candidates rely on PAC money far
    # more than Senate candidates — a real structural difference (2026-07
    # audit: House median 37.1%, Senate 15.7% of receipts), so each chamber
    # is measured against its own. The median used to be hand-typed as the
    # multipliers 1.35 / 3.2 (AGENTS.md §3a); it is now measured every run
    # from the members being scored (compute_funding_reference), which also
    # keeps it on the same denominator as the ratio itself.
    chamber = "house" if district is not None else "senate"
    ref = {
        **(FUNDING_REFERENCE.load().get(chamber) or {}),
        **((reference or {}).get(chamber) or {}),
    }
    pac_median = ref.get("pac_ratio_median")
    if pac_median:
        ratio_score = max(0.0, (1.0 - pac_ratio * (0.5 / pac_median))) * 100
    else:
        ratio_score = 50.0

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
        # correction. The volume scale is twice the chamber's median PAC
        # dollars (measured each run — compute_funding_reference), so the
        # median member lands at x0.75 and anything at or above twice it
        # floors at x0.5. It used to be a hand-typed $1,325,000 (2 x a
        # 2026-07 median of $662,750, itself refit after an earlier $2.0M
        # value had drifted 3x as the election cycle moved) — exactly the
        # drift a measured value doesn't suffer.
        pac_dollars_median = ref.get("pac_dollars_median")
        if pac_dollars_median:
            fallback_cap = 2 * pac_dollars_median
            volume_factor = 0.5 + 0.5 * max(0.0, 1.0 - pac_total / fallback_cap)
        else:
            volume_factor = 0.75
        volume_detail_suffix = f"no PAC committee-type data — fallback scaling for ${pac_total:,.0f} in absolute PAC dollars"

    pac_score = ratio_score * volume_factor

    # Component 2: small-donor share (25% weight), state-relative for
    # senators — see _small_donor_capacity_score.
    small_pct = funding.get("smallDonorPercentage", 0) or 0
    small_score, small_expected_pct = _small_donor_capacity_score(small_pct, state, district, ref)

    # Component 3: relative top-donor concentration (25% weight)
    concentration, n_external, pool = _top_donor_concentration(funding)
    c_median = ref.get("concentration_median")
    c_spread = (ref.get("concentration_p90") or 0) - (ref.get("concentration_p10") or 0)
    if concentration is not None and c_median is not None and c_spread > 0:
        # Chamber-relative: the median member's concentration scores 50,
        # and one p10-p90 spread above/below saturates at 0/100 — measured
        # each run (compute_funding_reference). The previous fixed anchors
        # (0.15 -> 100, 0.40 -> 0) were fitted by hand around a 2026-07
        # snapshot of both chambers pooled (median 27.8%); a still-earlier
        # set had drifted until the typical member scored ~90 regardless of
        # real concentration.
        concentration_score = max(0.0, min(100.0, 50.0 - 50.0 * (concentration - c_median) / c_spread))
        concentration_detail = (
            f"top 10 of {n_external} external donors = {concentration:.0%} "
            f"of the ${pool:,.0f} itemized external donor pool "
            f"(chamber median {c_median:.0%})"
        )
    else:
        # Too few itemized external donors to measure concentration.
        concentration_score = 50.0
        concentration_detail = (
            f"only {n_external} itemized external donors (${pool:,.0f} pool) "
            "— too few to measure concentration, neutral 50"
        )

    # Component 4: industry concentration (folded in from the former
    # Funding Diversity dimension, v6.5). Money too little of which is
    # industry-classified is neutral 50 here — not Funding Diversity's
    # grassroots-scaled fallback, which would count the small-donor share
    # (component 2) a second time.
    industry_concentration_score, industry_concentration_detail = _industry_concentration(
        funding, total_raised, missing_score=50.0,
    )

    score = clamp(
        pac_score * (20 / 53)
        + small_score * (10 / 53)
        + concentration_score * (10 / 53)
        + industry_concentration_score * (13 / 53)
    )
    return {
        "score": score,
        "components": [
            {
                "label": "PAC dependency",
                "weight": round(20 / 53, 4),
                "score": round(pac_score, 1),
                "detail": (
                    f"{pac_ratio:.0%} of ${total_raised:,.0f} in contributions came from PACs"
                    f" → raw {ratio_score:.1f}, scaled ×{volume_factor:.2f} "
                    f"({volume_detail_suffix})"
                ),
            },
            {
                "label": "Small-donor share",
                "weight": round(10 / 53, 4),
                "score": round(small_score, 1),
                "detail": (
                    f"{small_pct:.0f}% of contributions from small (<$200) donors"
                    + (
                        f" vs. an expected ~{small_expected_pct:.0f}% for a state this size"
                        if district is None
                        else f" vs. the House median of {small_expected_pct:.0f}%"
                    )
                ),
            },
            {
                "label": "Top-donor concentration",
                "weight": round(10 / 53, 4),
                "score": round(concentration_score, 1),
                "detail": concentration_detail,
            },
            {
                "label": "Industry concentration",
                "weight": round(13 / 53, 4),
                "score": round(industry_concentration_score, 1),
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
    pvi = _seat_pvi(state, district)
    eval_party = effective_party or party
    if eval_party == "R":
        lean = pvi
    elif eval_party == "D":
        lean = -pvi
    else:
        return 0.0
    return max(-1.0, min(lean / 15.0, 1.0))


def _seat_pvi(state: str, district: int | None = None) -> int:
    """Raw signed Cook PVI for a seat (positive = R lean): the district's
    when a district is given and the table has it, else the state's, else
    0 (neutral swing). The un-normalized value both _signed_state_alignment
    (party-signed, /15-scaled) and the position-congruence component's
    seat-conditional regression (which fetch/voteview.py FIT on this same
    raw scale at ingest time) are derived from — one lookup,
    so the two consumers can't disagree about which seat a member has."""
    pvi = _state_pvi().get(state, 0)
    if district is not None:
        pvi = _district_pvi().get(f"{state}-{district}", pvi)
    return pvi


# Constituent Alignment's seat expectation is measured, not hand-set
# (v6.13). Each run fits, per chamber and per party, the chamber's own
# break rate on seat alignment (compute_constituent_reference); the members
# it is about to score are the population. A party needs this many members
# for its fit, and the fit only bends at a swing seat (the kink term) when
# at least _MIN_OPPOSED_SEATS_FOR_KINK of them hold seats that lean to the
# other party — otherwise a line through a handful of points decides every
# opposed-seat member's expectation.
_MIN_CONSTITUENT_REFERENCE_PARTY = 20
_MIN_OPPOSED_SEATS_FOR_KINK = 5


def party_break_rate(voting_record: dict) -> tuple[float | None, int]:
    """(weighted share of party-labeled votes cast against the member's
    party, count of those votes). None when fewer than 3 are usable. The
    one definition both the per-run reference and the member's score read,
    so the expectation is measured on exactly the statistic it is compared
    with. Each roll call counts once (dedupe_votes), matching what the
    scorecard shows."""
    from app.pipeline.transform.normalize_votes import dedupe_votes

    votes = dedupe_votes(
        (voting_record.get("keyVotes") or []) + (voting_record.get("recentVotes") or [])
    )
    with_party = against = 0.0
    n = 0
    for v in votes:
        wp = v.get("votedWithParty") if isinstance(v, dict) else None
        if wp is None:
            continue
        weight = v.get("partyAlignmentWeight") or 0.0
        weight = weight if weight > 0.0 else 1.0
        if wp is True:
            with_party += weight
        else:
            against += weight
        n += 1
    if n < 3 or with_party + against <= 0:
        return None, n
    return against / (with_party + against), n


def _expected_break_rate(fit: dict, alignment: float) -> float:
    rate = (
        float(fit["a"])
        + float(fit["b"]) * alignment
        + float(fit.get("b_opposed") or 0.0) * min(alignment, 0.0)
    )
    return min(max(rate, 0.0), 1.0)


def compute_constituent_reference(members: list[tuple[str, float, float]]) -> dict | None:
    """Measure the seat expectation Constituent Alignment scores against.

    `members` is (party, seat alignment, break rate) for every D/R member of
    one chamber with a measurable break rate. Per party, least squares:

        break_rate = a + b * alignment + b_opposed * min(alignment, 0)

    alignment is the party-signed seat lean (_signed_state_alignment), so
    the kink lets the slope differ between seats that lean to the member's
    party and seats that lean away. Per-party because the two parties'
    break rates differ at the same seat lean (majority status, whip
    strength — measured 2026-09 on the 108th House: D intercept 0.128, R
    0.076), and a per-party expectation predicted re-election vote share
    better than a pooled one (see the research note in
    docs/research/constituent-alignment.md).

    deviation_p90 is the 90th percentile of |break rate - expected| across
    both parties: the saturation scale, so the most out-of-pattern decile
    spans the component's full range. Returns None unless BOTH parties have
    enough members — one party scored against a measured expectation and
    the other against a fallback would not be comparable (the same
    both-or-neither rule as fetch/voteview.py's ingestion gates).
    """
    import numpy as np

    fits: dict[str, dict] = {}
    deviations: list[float] = []
    for party in ("D", "R"):
        rows = [(al, br) for p, al, br in members if p == party and br is not None]
        if len(rows) < _MIN_CONSTITUENT_REFERENCE_PARTY:
            return None
        al = np.array([r[0] for r in rows])
        br = np.array([r[1] for r in rows])
        kinked = int((al < 0).sum()) >= _MIN_OPPOSED_SEATS_FOR_KINK
        cols = [np.ones_like(al), al] + ([np.minimum(al, 0.0)] if kinked else [])
        coef, *_ = np.linalg.lstsq(np.column_stack(cols), br, rcond=None)
        fit = {
            "a": round(float(coef[0]), 5),
            "b": round(float(coef[1]), 5),
            "b_opposed": round(float(coef[2]), 5) if kinked else 0.0,
            "n": len(rows),
        }
        fits[party] = fit
        deviations += [abs(r - _expected_break_rate(fit, a)) for a, r in rows]
    p90 = float(np.quantile(deviations, 0.9))
    if p90 <= 0:
        return None
    return {"expected": fits, "deviation_p90": round(p90, 5), "n": len(deviations)}


def constituent_reference_inputs(members: list[dict]) -> list[tuple[str, float, float]]:
    """(party, seat alignment, break rate) for each member dict (the shape
    calculate_scores consumes: state, party, district, votingRecord) with a
    measurable break rate — exactly the values _constituent_alignment_core
    compares, so the reference and the scores can't disagree."""
    out = []
    for m in members:
        record = m.get("votingRecord") or {}
        party = record.get("effectiveParty") or m.get("party")
        rate, _ = party_break_rate(record)
        if party not in ("D", "R") or rate is None:
            continue
        alignment = _signed_state_alignment(
            m.get("state", ""), m.get("party", "I"),
            effective_party=record.get("effectiveParty"), district=m.get("district"),
        )
        out.append((party, alignment, rate))
    return out


def _constituent_reference(chamber: str, reference: dict | None) -> dict:
    return (reference or {}).get(chamber) or CONSTITUENT_REFERENCE.load().get(chamber) or {}


# Weight of position congruence when a roll-call ideal point exists; the
# seat-relative vote component carries the rest. A design weight, not a
# calibrated one — see the research note: the one election where both
# could be tested (2004 House) gave the vote component the larger
# independent association with re-election vote share, so the vote
# component keeps the majority weight, but no multi-election estimate of
# the ratio exists to fit this from.
POSITION_CONGRUENCE_WEIGHT = 0.30


def _calc_constituent_alignment(
    voting_record: dict,
    lobbying_matches: list[dict],
    funding: dict,
    state: str = "",
    party: str = "I",
    district: int | None = None,
    bioguide_id: str | None = None,
    reference: dict | None = None,
) -> int:
    """
    Constituent Alignment Score (0-100, higher = better). Stored under the
    legacy key "independentVoting".

    How far a member's voting sits from what members of their party in
    comparably-leaning seats do, in the direction their seat leans. v6.13
    rebuilt it on evidence rather than argument: every design choice below
    was tested against U.S. House re-election results (1994-2010, 2,545
    incumbent-elections) — whether the measure predicts the incumbent's
    vote share once district partisanship, the national tide and seniority
    are controlled, the test Canes-Wrone, Brady & Cogan (2002) and Carson,
    Koger, Lebo & Young (2010) use to show constituents judge these things.
    Full method, numbers and caveats: docs/research/constituent-alignment.md
    (reproduce with scripts/research_constituent_alignment.py).

    Components:
      1. Seat-relative vote alignment (70%, or 100% without ideal-point
         data): the member's break rate on party-labeled votes minus the
         break rate their chamber's same-party members show at the same seat
         lean — both measured each run (compute_constituent_reference).
         Symmetric: 50 at expectation, above for breaking more, below for
         breaking less, saturating at the chamber's 90th-percentile
         deviation.
           - Loyalty below expectation is scored, not held neutral. In the
             2004 House test the below-expectation side carried the
             strongest association with vote share (2.3 pts per SD, t=3.4),
             consistent with Carson et al. 2010. The pre-v6.13 floor
             ("unreadable") discarded it.
           - No seat-safety discount on either side: the association was
             the same in safe and competitive seats (1.5 vs 1.4 pts/SD).
           - No discount for flank-side defectors (Kirkland & Slapin 2017's
             concern): members breaking from the flank did not fare worse
             for it — if anything better (difference +2.2, t=1.9), the
             wrong sign for a discount.
      2. Position congruence (30%, when Voteview ideal points exist): the
         member's congress-specific Nokken-Poole first-dimension position
         minus what a same-party member of a seat with this lean holds
         (per-party OLS on seat PVI, fit each run by fetch/voteview.py).
         Symmetric, saturating at the chamber's 90th-percentile extremity.
           - 1 SD toward the party flank cost 0.8-1.0 pts of vote share,
             robust to flexible partisanship controls; the center-ward side
             earned the same slope (equal-slopes p=0.92), so it is credited
             symmetrically — the pre-v6.13 0.25 credit floor in safe seats
             had no support.
           - No safe-seat scaling (interaction t=0.1; the pre-v6.13 severity
             weighting predicted worse than none).
           - Per-party residual predicted best of the three designs tried
             (per-party, pooled slope, raw position), marginally.
           - Congress-specific (Nokken & Poole 2004) positions predicted the
             2004 result better than career-constrained DW-NOMINATE and
             dominated it when both were entered — and "current term, not
             career" (AGENTS.md principle 6) wants the congress-specific one
             anyway.
    """
    return _constituent_alignment_core(
        voting_record, lobbying_matches, funding, state, party,
        district, bioguide_id, reference,
    )["score"]


def _constituent_alignment_core(
    voting_record: dict,
    lobbying_matches: list[dict],
    funding: dict,
    state: str = "",
    party: str = "I",
    district: int | None = None,
    bioguide_id: str | None = None,
    reference: dict | None = None,
) -> dict:
    """Same math as _calc_constituent_alignment, returning every intermediate
    value alongside the final score. Single implementation, same reuse
    contract as _funding_independence_core above."""
    effective_party = voting_record.get("effectiveParty", party)
    eval_party = effective_party or party
    alignment = _signed_state_alignment(
        state, party, effective_party=effective_party, district=district,
    )
    chamber = "house" if district is not None else "senate"

    ideal = _member_ideal_points(chamber)
    dim1 = (ideal.get("members") or {}).get(bioguide_id) if bioguide_id else None
    position_fit = (ideal.get("fit") or {}).get(eval_party)
    congruence_sat = ideal.get("extremity_p90")
    congruence_score = None
    congruence_detail = ""
    if dim1 is not None and position_fit is not None and congruence_sat:
        expected_dim1 = float(position_fit["a"]) + float(position_fit["b"]) * _seat_pvi(state, district)
        residual = float(dim1) - expected_dim1
        extremity = -residual if eval_party == "D" else residual
        scaled = max(-1.0, min(extremity / float(congruence_sat), 1.0))
        congruence_score = 50.0 - 50.0 * scaled
        congruence_detail = (
            f"{ideal.get('measure', 'NOMINATE')} dim1 {float(dim1):+.2f} vs "
            f"{expected_dim1:+.2f} expected for a {eval_party} member of this seat — "
            + ("toward the party flank" if extremity > 0 else "toward the seat's center")
        )

    break_rate, n_party = party_break_rate(voting_record)
    ref = _constituent_reference(chamber, reference)
    vote_fit = (ref.get("expected") or {}).get(eval_party)
    deviation_scale = ref.get("deviation_p90")
    expected = None
    if break_rate is None:
        party_score = 50.0
        party_alignment_detail = "fewer than 3 party-labeled votes available — neutral 50"
    elif vote_fit is None or not deviation_scale:
        party_score = 50.0
        party_alignment_detail = (
            f"break rate {break_rate:.1%}; no measured expectation for a "
            f"{eval_party or 'non-caucusing'} member of this chamber — neutral 50"
        )
    else:
        expected = _expected_break_rate(vote_fit, alignment)
        deviation = break_rate - expected
        party_score = 50.0 + 50.0 * max(-1.0, min(deviation / float(deviation_scale), 1.0))
        party_alignment_detail = (
            f"broke with party on {break_rate:.1%} of {n_party} party-labeled votes; "
            f"{eval_party} members of this chamber in seats with this lean "
            f"(signal {alignment:+.2f}) break on {expected:.1%}"
        )

    congruence_weight = POSITION_CONGRUENCE_WEIGHT if congruence_score is not None else 0.0
    party_weight = 1.0 - congruence_weight
    score = clamp(party_score * party_weight + (congruence_score or 0.0) * congruence_weight)

    components = [
        {
            "label": "Seat-relative vote alignment",
            "weight": round(party_weight, 2),
            "score": round(party_score, 1),
            "detail": party_alignment_detail,
        },
    ]
    if congruence_weight > 0:
        components.append({
            "label": "Position congruence",
            "weight": congruence_weight,
            "score": round(congruence_score, 1),
            "detail": congruence_detail,
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


def _industry_concentration(
    funding: dict, total_raised: float, missing_score: float, missing_label: str = "neutral",
) -> tuple[float, str]:
    """(score, detail) for industry concentration: inverse HHI across
    classified industries — a member whose PAC and itemized money all comes
    from one industry is more captured than one whose money spans eight
    (Parmigiani 2025 computes the same HHI per legislator). Small donors
    and unclassified large individuals are excluded; they are not
    industry-specific money.

    When less than 5% of the money is industry-classified, HHI on that
    slice is noise, so the score is `missing_score`; between 5% and 40% it
    is shrunk toward `missing_score` in proportion to the classified
    share. Funding Independence passes a neutral 50; Funding Diversity
    passes its grassroots-scaled neutral (see _funding_diversity_core)."""
    industries = [
        ind for ind in funding.get("industryBreakdown") or []
        if ind.get("industry") not in NON_INDUSTRY_CODES
    ]
    if not industries or not total_raised:
        return missing_score, f"no industry breakdown available — {missing_label} {missing_score:.0f}"
    total_known = sum(ind.get("total", 0) for ind in industries)
    total_known_pct = total_known / total_raised * 100
    if total_known_pct < 5 or total_known <= 0:
        return missing_score, (
            f"only {total_known_pct:.1f}% of funding is industry-classified — "
            f"too little to measure HHI, {missing_label} {missing_score:.0f}"
        )
    hhi = sum((ind.get("total", 0) / total_known) ** 2 for ind in industries)
    raw = (1 - max(0, min((hhi - 0.10) / 0.90, 1.0))) * 100
    relevance = min(total_known_pct / 40, 1.0)
    score = raw * relevance + missing_score * (1 - relevance)
    return score, (
        f"HHI={hhi:.3f} across {len(industries)} industries → raw {raw:.1f}, "
        f"blended {relevance:.0%} with {missing_label} {missing_score:.0f} "
        f"({total_known_pct:.0f}% of funding industry-classified)"
    )


def _funding_diversity_core(funding: dict) -> dict:
    """Same math as _calc_funding_diversity, returning every intermediate
    value alongside the final score. Single implementation, same reuse
    contract as _funding_independence_core above."""
    industry_breakdown = funding.get("industryBreakdown", [])
    small_donor_pct = funding.get("smallDonorPercentage", 0)
    total_raised = funding_share_base(funding)

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
    concentration_score, concentration_detail = _industry_concentration(
        funding, total_raised, missing_score=grassroots_neutral,
        missing_label=f"grassroots-scaled neutral ({small_frac:.0%} small-donor share)",
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
# is out of power. The majority/minority advancement rates themselves are
# measured each run from the chamber's own bills (_measure_advancement_rates).
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
# The table above is historical record only. The CURRENT congress's
# majority comes from the live roster each pipeline run (derive_chamber_
# majority, carried on the LES reference) — a hand-maintained table has
# no entry for a congress until someone adds one, and the moment a new
# congress convened every member silently fell back to the flat
# unknowable-status rate, dropping the majority/minority adjustment.


# House bill types (substantive + commemorative) — used for chamber
# detection wherever a bill's own billType is the only chamber signal
# available (no explicit chamber field on sponsored-bill records).
_LES_HOUSE_TYPES = {"hr", "hjres", "hres", "hconres"}


def derive_chamber_majority(
    parties: list[str | None], chamber: str, tie_breaker: str | None = None,
) -> str | None:
    """The majority party of a chamber from its current roster.

    `parties` holds each member's caucus party (Independents already
    resolved to the party they caucus with — pass effectiveParty, not the
    raw "I"). Counted over D and R only; anything unresolved is ignored.
    A Senate tie goes to `tie_breaker` (the sitting Vice President's party,
    i.e. the president's — the 117th Congress's 50-50 Senate was a
    Democratic majority this way). A House tie, or a Senate tie with no
    tie_breaker, is unknowable and returns None.
    """
    d = sum(1 for p in parties if p == "D")
    r = sum(1 for p in parties if p == "R")
    if d > r:
        return "D"
    if r > d:
        return "R"
    if chamber == "senate" and tie_breaker in ("D", "R"):
        return tie_breaker
    return None


def _bill_majority(bill_type: str, congress: int | None, current: tuple[int, str] | None) -> str | None:
    """The majority party of the chamber a bill was introduced in, for its
    congress. `current` = (congress, majority) derived from the live roster;
    it wins for the congress it describes, the table covers the past."""
    is_house = bill_type in _LES_HOUSE_TYPES
    if current is not None and congress == current[0]:
        return current[1]
    return (_HOUSE_MAJORITY if is_house else _SENATE_MAJORITY).get(congress or 0)


def _advancement_baseline(
    bill_type: str,
    congress: int | None,
    party: str | None,
    current: tuple[int, str] | None = None,
    rates: dict | None = None,
) -> float:
    """Expected bill advancement rate for a sponsor, by chamber/congress/
    party. Chamber detection must cover every House bill type (including
    commemorative hres/hconres), not just substantive ones — this is
    averaged over a member's full sponsored-bill list (_les_component_
    score), not pre-filtered to substantive bills the way the old
    advancement-rate formula filtered it.

    `rates` = {"majority", "minority", "pooled"} for the bill's chamber,
    measured each run (_measure_advancement_rates); None reads the persisted
    reference. Only their RATIO affects a score — member baselines are
    divided by the chamber average — so these used to be the hand-typed
    2026-07 corpus rates (Senate 3.6% / 2.4%, House 6.4% / 2.4%)."""
    if rates is None:
        chamber = "house" if bill_type in _LES_HOUSE_TYPES else "senate"
        rates = (LES_REFERENCE.load().get(chamber) or {}).get("advancement_rates") or {}
    majority = _bill_majority(bill_type, congress, current)
    if not majority or party not in ("D", "R") or not rates:
        return rates.get("pooled", 1.0)  # status unknowable: no tilt either way
    return rates["majority"] if party == majority else rates["minority"]


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
    # 2026-07 fix: REFERRED (bill_stage.py) is the automatic, universal
    # first step every bill gets within days of introduction — same
    # credit as bare introduction, not stage 2. The old scheme gave every
    # bill that had merely been referred (virtually all of them) the same
    # V&W "received action in committee" credit as one that actually got
    # a hearing, markup, or was reported out — see bill_stage.py's module
    # docstring for the live audit that found this (one senator's
    # sponsored-bills summary reading "135 bills, 123 advancing").
    "REFERRED": 1,
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


# ── LES population reference ────────────────────────────────────────────
#
# A member's per-congress credit is scored against their chamber's
# population: the MEDIAN member's credit is the neutral bar (v6.10 — the
# distribution is right-skewed, so a mean bar puts >50% of every chamber
# below neutral by construction), tilted by the member's own majority/
# minority bill mix relative to the chamber-average baseline (v6.9), and
# the gap saturates at 1.5 population standard deviations.
#
# COMPUTED EACH PIPELINE RUN from the chamber being scored (v6.13,
# compute_les_reference), not hand-copied from a calibration script's
# output. Two reasons it had to move:
#   - Credit only accumulates as a congress goes on, and scoring is limited
#     to the current congress. A median frozen on one date drifted: every
#     member's score crept upward through the congress against a fixed bar,
#     then collapsed at the next congress when everyone restarted near zero
#     against a bar measured mid-congress.
#   - AGENTS.md §3a: calibrated values are generated data, never Python
#     literals pasted from a script's printout.
#
# Saturation is now each chamber's OWN spread (v6.13). It used to be one
# pooled constant (1.5 x the mean of the two chambers' stdevs), which over-
# counted a House member's gap relative to House spread and under-counted a
# Senate member's (live stdevs 88 vs 178) — the same cross-chamber mixing
# v6.9 removed from the medians and baselines. Pooling would also mix
# congresses at a rollover, when one chamber has run under the new congress
# and the other hasn't yet.
#
# Where the numbers come from, in order:
#   1. the reference the pipeline passes in for this run;
#   2. /data/les_reference.json — the last run's reference, which is what
#      the API's on-demand "show the math" breakdown and scripts/rescore.py
#      read, so they reproduce the pipeline's own numbers;
#   3. app/data/les_reference.json — the bundled pre-first-run fallback,
#      regenerated by scripts/calibrate_les_credit_scale.py.
# (File handling: population_reference.LES_REFERENCE.)
_LES_SATURATION_STDEVS = 1.5


def _les_member_inputs(sponsored_bills: list[dict], party: str | None) -> dict | None:
    """Per-member quantities the LES component and its population reference
    share — one definition, so the reference is always measured with the
    exact formula members are scored with. None when the member has no
    substantive bills (not part of the credit distribution)."""
    n_sub = sum(
        1 for b in sponsored_bills
        if (b.get("billType") or "").lower() in SUBSTANTIVE_BILL_TYPES
    )
    if n_sub == 0:
        return None
    congresses = {b.get("congress") for b in sponsored_bills if b.get("congress")}
    house_n = sum(
        1 for b in sponsored_bills
        if (b.get("billType") or "").lower() in _LES_HOUSE_TYPES
    )
    return {
        "n_sub": n_sub,
        "raw_per_congress": sum(_les_cumulative_credit(b) for b in sponsored_bills) / max(len(congresses), 1),
        "is_house": house_n > (len(sponsored_bills) - house_n),
    }


def _les_member_baseline(
    sponsored_bills: list[dict],
    party: str | None,
    current: tuple[int, str] | None,
    rates: dict | None = None,
) -> float:
    return sum(
        _advancement_baseline((b.get("billType") or "").lower(), b.get("congress"), party, current, rates)
        for b in sponsored_bills
    ) / len(sponsored_bills)


# Fewest advanced bills in EACH of the majority and minority groups before
# their rates are trusted; early in a congress there are few, and the
# previous rates are kept instead.
_MIN_ADVANCED_BILLS_PER_STATUS = 20


def _measure_advancement_rates(
    members: list[tuple[list[dict], str | None]], current: tuple[int, str] | None,
) -> dict | None:
    """Share of sponsored bills that advanced past referral (stage >= 2:
    committee action or further) for majority- vs minority-party sponsors
    in this chamber, plus the pooled rate. None when either group has too
    few advanced bills to measure."""
    counts = {"majority": [0, 0], "minority": [0, 0]}  # [advanced, total]
    for bills, party in members:
        if party not in ("D", "R"):
            continue
        for b in bills:
            majority = _bill_majority((b.get("billType") or "").lower(), b.get("congress"), current)
            if not majority:
                continue
            c = counts["majority" if party == majority else "minority"]
            c[1] += 1
            if _les_bill_stage(b) >= 2:
                c[0] += 1
    if any(adv < _MIN_ADVANCED_BILLS_PER_STATUS for adv, _ in counts.values()):
        return None
    adv = sum(a for a, _ in counts.values())
    total = sum(t for _, t in counts.values())
    return {
        "majority": round(counts["majority"][0] / counts["majority"][1], 6),
        "minority": round(counts["minority"][0] / counts["minority"][1], 6),
        "pooled": round(adv / total, 6),
        "n_bills": total,
    }


def compute_les_reference(
    members: list[tuple[list[dict], str | None]],
    congress: int,
    majority: str | None,
    previous_rates: dict | None = None,
) -> dict | None:
    """One chamber's LES population reference from this run's members.

    `members` is (sponsored_bills, caucus party) per member of the chamber
    being scored; `majority` is derive_chamber_majority's answer for
    `congress`. Returns None when too few members have substantive bills
    to describe a distribution — the caller then keeps the last good
    reference rather than scoring against noise.
    """
    current = (congress, majority) if majority else None
    rates = _measure_advancement_rates(members, current) or previous_rates
    credits: list[float] = []
    baselines: list[float] = []
    for bills, party in members:
        inputs = _les_member_inputs(bills, party)
        if inputs is None:
            continue
        credits.append(inputs["raw_per_congress"])
        baselines.append(_les_member_baseline(bills, party, current, rates))
    if len(credits) < _MIN_LES_REFERENCE_MEMBERS:
        return None
    return {
        "congress": congress,
        "majority": majority,
        "n": len(credits),
        "median_credit": round(statistics.median(credits), 4),
        "mean_credit": round(statistics.mean(credits), 4),
        "stdev_credit": round(statistics.pstdev(credits), 4),
        "avg_baseline": round(statistics.mean(baselines), 6),
        "advancement_rates": rates,
    }


# Fewest members with substantive bills that still describe a chamber's
# distribution. Early in a congress few members have introduced anything;
# below this, the previous reference is kept (see compute_les_reference).
_MIN_LES_REFERENCE_MEMBERS = 30


def load_les_reference() -> dict:
    """{"senate": {...}, "house": {...}} — see population_reference."""
    return LES_REFERENCE.load()


def write_les_reference(chamber: str, reference: dict) -> None:
    LES_REFERENCE.write(chamber, reference)


def _les_component_score(
    sponsored_bills: list[dict],
    party: str | None,
    years_in_office: float | None,
    reference: dict | None = None,
) -> tuple[float, str]:
    """The V&W-based 70% component: significance-weighted, cumulative-
    stage credit per congress served, scored relative to what an average
    sponsor of this party/status/chamber would be expected to achieve —
    not an absolute rate. Confirmed-zero-vs-no-data-yet distinction (see
    _zero_bill_component_score) carried forward unchanged from the
    2026-07 "inaction beats trying and failing" fix; this is the same
    invariant, re-homed into the new formula.

    `reference` is {"senate": {...}, "house": {...}} (compute_les_reference
    per chamber); None reads load_les_reference(). See the LES population
    reference comment above for where each number comes from."""
    inputs = _les_member_inputs(sponsored_bills, party)
    if inputs is None:
        return _zero_bill_component_score(years_in_office)
    n_sub = inputs["n_sub"]
    raw_per_congress = inputs["raw_per_congress"]
    chamber = "house" if inputs["is_house"] else "senate"

    ref = (reference or load_les_reference()).get(chamber)
    if not ref:
        return 50.0, "no population reference available for this chamber — neutral 50"
    member_congress = max((b.get("congress") or 0 for b in sponsored_bills), default=0)
    if member_congress and ref.get("congress") and member_congress != ref["congress"]:
        # First days of a new congress, before enough members have
        # introduced bills to measure its reference: comparing against the
        # previous congress's full-term median would score everyone as if
        # they'd done almost nothing.
        return 50.0, (
            f"no {chamber} reference measured for the {member_congress}th Congress yet "
            "— neutral 50 until enough members have sponsored bills to measure one"
        )
    current = (ref["congress"], ref["majority"]) if ref.get("majority") else None
    population_median = ref["median_credit"]
    avg_baseline = ref["avg_baseline"]
    saturation = _LES_SATURATION_STDEVS * ref["stdev_credit"]

    rates = ref.get("advancement_rates")
    if rates and avg_baseline:
        member_baseline = _les_member_baseline(sponsored_bills, party, current, rates)
        status_ratio = member_baseline / avg_baseline
    else:
        # No measured rates yet: no majority/minority tilt, rather than a
        # member baseline on a different scale from the chamber average.
        status_ratio = 1.0
    # Reference point is the chamber MEDIAN (not V&W's mean) so the typical
    # member scores ~50 despite the right-skewed credit distribution — see
    # the LES population reference comment above. status_ratio only tilts
    # that bar up or down for a member's own majority/minority bill mix.
    expected_per_congress = population_median * status_ratio

    diff = raw_per_congress - expected_per_congress
    conf = min(n_sub / 10, 1.0)
    normalized_diff = max(-1.0, min(diff / saturation, 1.0)) if saturation else 0.0
    raw_score = 50.0 + 50.0 * normalized_diff
    score = raw_score * conf + 50.0 * (1 - conf)

    # Stage-count breakdown (2026-07, per user request): the raw credit
    # figure above is one opaque number that a bill-mill sponsor (many
    # bills, all stuck at "introduced") and a genuine passage-heavy
    # sponsor could both reach — Volden & Wiseman's real methodology
    # credits introduction itself, not just advancement (see this file's
    # v6.4->v6.5-era LES module comment above _LES_STAGE_ORDER). Showing
    # the actual stage counts makes that visible instead of implied.
    substantive_bills = [
        b for b in sponsored_bills
        if (b.get("billType") or "").lower() in SUBSTANTIVE_BILL_TYPES
    ]
    introduced_only = sum(1 for b in substantive_bills if _les_bill_stage(b) == 1)
    advanced_short_of_law = sum(
        1 for b in substantive_bills if 1 < _les_bill_stage(b) < _LES_MAX_STAGE
    )
    enacted = sum(1 for b in substantive_bills if _les_bill_stage(b) >= _LES_MAX_STAGE)

    detail = (
        f"{raw_per_congress:.1f} significance-weighted stage-credit/congress vs. "
        f"{expected_per_congress:.1f} expected for this sponsor's status — "
        f"{n_sub} substantive bills: {introduced_only} introduced only (still "
        f"counts under Volden & Wiseman's real methodology), "
        f"{advanced_short_of_law} advanced further, {enacted} became law"
    )
    if conf < 1.0:
        detail += f", confidence-scaled {conf:.0%} ({n_sub} of 10 bills)"
    return score, detail


def _calc_legislative_effectiveness(
    sponsored_bills: list[dict],
    leadership_score: float | None = None,
    party: str | None = None,
    years_in_office: float | None = None,
    attracted_bipartisanship: float | None = None,
    les_reference: dict | None = None,
) -> int:
    """
    Legislative Effectiveness Score (0-100, higher = better).

    Three components (two when bipartisan-attraction data is missing —
    weights then revert to exactly the pre-v6.11 70/30 split):

      1. Bill significance & advancement (60%): Volden & Wiseman
         (2014)-based — see the module comment above _LES_STAGE_ORDER for
         the full methodology and the two disclosed departures from their
         real approach (2-tier significance, expected-vs-actual credit
         instead of population-mean-ratio normalization).

      2. Legislative leadership (25%): PageRank score from the
         cosponsorship network (Brin & Page 1998, computed in
         sponsorship_analysis.py) — no basis in Volden & Wiseman, kept as
         an explicitly separate signal. Senators whose bills attract
         cosponsors from influential colleagues score higher. Shrunk
         toward neutral 50 for freshmen (PageRank centrality takes years
         to build; a near-zero raw percentile in year one reflects network
         age, not effectiveness — 2026-07 fix, see leadership_conf below).

      3. Bipartisan coalition attraction (15%, when cosponsorship data
         exists — v6.11, moved here from Constituent Alignment): the
         share of cosponsors a member attracts to their OWN bills from
         the other party, cohort-median-normalized
         (compute_bipartisanship_scores(direction="receive")).
         Harbridge-Yong, Volden & Wiseman (2023, "The Bipartisan Path to
         Effective Lawmaking," J. Politics 85:3, 93rd-114th Congresses)
         show attracting cross-party cosponsors robustly predicts
         lawmaking success for BOTH majority- and minority-party members
         — and specifically that it is the ATTRACTION of bipartisan
         cosponsors, not the offer of cosponsorships across the aisle,
         that carries the effect, which is why this component consumes
         the receive-only rate rather than the Lugar-style give+receive
         blend the profile display uses. Two disclosed limits keep the
         weight modest: HVW's evidence is a robust association, not a
         clean causal identification (their own framing), and the signal
         is an ANTECEDENT of effectiveness rather than realized output —
         realized advancement is already component 1's job. No
         seat-safety scaling here, unlike the old Constituent Alignment
         breadth component: in an effectiveness dimension, low bipartisan
         attraction predicts lower lawmaking success regardless of how
         safe the member's seat is. Note components 2 and 3 are both
         cosponsorship-network-derived (centrality vs. cross-party
         share) — kept at a combined 40% for that reason; re-check their
         live correlation after the first full run, same standing check
         as the v6.8 r=-0.76 finding.

    Components apply linear count-confidence shrinkage toward 50 when data is
    sparse, preventing extreme scores from thin evidence — including a
    confirmed-zero-bills record after real tenure, which is a weak but
    real negative signal, not the same as a freshman with no data yet
    (see _zero_bill_component_score).
    """
    return _legislative_effectiveness_core(
        sponsored_bills, leadership_score, party, years_in_office,
        attracted_bipartisanship, les_reference,
    )["score"]


def _legislative_effectiveness_core(
    sponsored_bills: list[dict],
    leadership_score: float | None = None,
    party: str | None = None,
    years_in_office: float | None = None,
    attracted_bipartisanship: float | None = None,
    les_reference: dict | None = None,
) -> dict:
    """Same math as _calc_legislative_effectiveness, returning every
    intermediate value alongside the final score. Single implementation,
    same reuse contract as _funding_independence_core above."""
    les_score, les_detail = _les_component_score(
        sponsored_bills or [], party, years_in_office, les_reference,
    )

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
    # 0.0 is a real score, not missing data: compute_leadership_scores
    # rescales the chamber's PageRank to [0, 1], so exactly one member —
    # the chamber's lowest — gets 0.0. Treating that as "no data" gave the
    # bottom member a neutral 50 while the next one up scored ~0. Only None
    # (no cosponsorship-network data) is missing.
    if leadership_score is not None:
        # Rescaled within the chamber (log scale, median ≈ 0.5) — a
        # position in the chamber, not a percentile.
        leadership_raw = min(max(leadership_score, 0.0), 1.0) * 100
        leadership_head = f"PageRank leadership {leadership_raw:.0f}/100 within the chamber (median ≈ 50)"
    else:
        # No data yet — neutral prior, never a punitive below-50 default
        # (this repo's design principle: missing data is never "bad").
        leadership_raw = 50.0
        leadership_head = "no cosponsorship-network data — neutral 50"

    leadership_conf = min((years_in_office or 0) / LEADERSHIP_TENURE_FULL_CREDIT_YEARS, 1.0)
    leadership_pct = leadership_raw * leadership_conf + 50 * (1 - leadership_conf)
    leadership_detail = (
        f"{leadership_head}, tenure-confidence-scaled "
        f"{leadership_conf:.0%} ({years_in_office or 0:.1f} of 6 years)"
    )

    if (
        not sponsored_bills
        and leadership_score is None
        and attracted_bipartisanship is None
        and les_score == 50.0
    ):
        return {"score": 50, "components": [], "note": "No sponsored-bill or leadership data — neutral default."}

    # Bipartisan coalition attraction (v6.11 — see the docstring above for
    # the HVW 2023 rationale and disclosed limits). Cohort-median-
    # normalized like the leadership score: the chamber-median
    # attractor of cross-party cosponsors scores 50. Missing data skips
    # the component and reverts to the exact pre-v6.11 70/30 split —
    # never scored neutral, matching how this dimension's own
    # missing-data note above treats absent signals.
    if attracted_bipartisanship is not None:
        coalition_pct = min(max(attracted_bipartisanship, 0.0), 1.0) * 100
        les_weight, leadership_weight, coalition_weight = 0.60, 0.25, 0.15
    else:
        coalition_pct = 0.0
        les_weight, leadership_weight, coalition_weight = 0.70, 0.30, 0.0

    score = clamp(
        les_score * les_weight
        + leadership_pct * leadership_weight
        + coalition_pct * coalition_weight
    )
    components = [
        {"label": "Bill significance & advancement (V&W-based)", "weight": les_weight,
         "score": round(les_score, 1), "detail": les_detail},
        {"label": "Legislative leadership", "weight": leadership_weight,
         "score": round(leadership_pct, 1), "detail": leadership_detail},
    ]
    if coalition_weight > 0:
        components.append({
            "label": "Bipartisan coalition attraction",
            "weight": coalition_weight,
            "score": round(coalition_pct, 1),
            "detail": (
                f"cross-party share of cosponsors attracted to own bills, "
                f"chamber-median-normalized {attracted_bipartisanship:.0%} "
                "(median attractor = 50)"
            ),
        })
    return {"score": score, "components": components}
