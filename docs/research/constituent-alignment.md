# Constituent Alignment: what the evidence supports

This note records how Constituent Alignment's design was chosen, from v6.13
on. Before v6.13, several of its rules rested on arguments that were never
tested: the claim that party loyalty is "unreadable", and discounts for safe
seats. Each rule below was kept or changed based on how well it predicts real
election results. Section 10 (v6.16) is the one place the evidence points two
ways, the member's own party's voters one way and the whole electorate the
other, and says which the score follows and why.

Every number here is printed by
[`backend/scripts/research_constituent_alignment.py`](../../backend/scripts/research_constituent_alignment.py),
which downloads the public data at pinned commits and runs every test.

## The question and the test

Constituent Alignment asks whether a member votes the way their seat asks them
to. There is no direct measure of that, so the literature validates proxies
with one test: **once a district's partisanship and the national tide are
accounted for, does the proxy predict how the incumbent does with their own
voters?** If voters reward or punish what the measure picks up, the measure is
capturing something constituents judge.

- Canes-Wrone, Brady & Cogan (2002, *APSR* 96:1) found House members whose
  roll-call positions are more extreme than their district's partisanship
  predicts lose vote share, including members in safe seats.
- Carson, Koger, Lebo & Young (2010, *AJPS* 54:3) found that party loyalty on
  divisive votes costs vote share beyond what ideology explains.
- Nokken & Poole (2004, *LSQ* 29:4) estimate ideal points one congress at a
  time. DW-NOMINATE only lets a member drift along a straight line over their
  whole career.
- Kirkland & Slapin (2017) show that many of the most frequent party
  defectors defect from the flank side. That was the stated reason for a
  planned (never shipped) discount on crossing credit.
- Bafumi & Herron (2010, *APSR*) show members of both parties sit to their
  party's side of their constituents, so a raw district median is the wrong
  target. That is the reason for per-party fits.

**Data.** U.S. House general elections from 1994 to 2010, pairing each
congress with the election at its end (the 103rd–106th and 108th–111th
congresses). The sample is incumbents on the ballot in races both parties
contested: 2,545 incumbent-elections. The outcome is the incumbent's party's
share of the two-party House vote. Controls are that party's share of the
two-party presidential vote in the district, election-year × party fixed
effects, and log terms served. Positions come from DW-NOMINATE. Break rates
come from the 108th House's 1,218 roll calls, of which 604 are party-unity
votes. The Senate replication uses the 109th and 111th Senates.

## Results and the decisions they drove

### 1. Position congruence carries signal, and the per-party residual is the right form

| Measure (per 1 SD toward the party's flank) | Coefficient | t | ΔR² | Leave-one-election-out RMSE |
|---|---|---|---|---|
| Baseline (no position) | | | | 6.196 |
| **A. Per-party residual on seat lean** (shipped) | −0.96 | −5.1 | 0.0094 | **6.150** |
| B. Pooled slope, party intercepts | −0.93 | −4.9 | 0.0087 | 6.157 |
| C. Raw party-signed position | −1.24 | −4.8 | 0.0082 | 6.168 |

A position one standard deviation toward the flank, relative to same-party
members in similar seats, costs about 1 point of vote share. With flexible
controls (a separate quadratic in partisanship for each year × party) it is
−0.76 (t=−4.2). The per-party residual predicts held-out elections best,
though the margin over B is small. **Kept: per-party residual.**

### 2. No safe-seat scaling

| Seat (party-signed lean) | Coefficient | t | n |
|---|---|---|---|
| Opposed or swing | −0.43 | −1.1 | 553 |
| Leans own party | −0.80 | −2.8 | 772 |
| Safe for own party | −0.80 | −3.3 | 1,220 |

The interaction between extremity and seat safety is 0.03 (t=0.1). The
pre-v6.13 weighting, which zeroed the flank penalty in safe seats and cut the
center-ward credit to 0.25 there, fits worse than no weighting (ΔR² 0.0070 vs
0.0094; held-out RMSE 6.163 vs 6.150). **Removed** from both components.
Loyalty tells the same story (below).

### 3. Symmetric position scoring

Flank-ward slope −0.73 (t=−2.4); center-ward slope −0.79 (t=−2.4); test of
equal slopes p=0.92. Moving toward the seat's center is rewarded as much as
moving toward the flank is punished. **Changed:** the component is now
symmetric.

### 4. Loyalty is informative, so it is scored

Break rates by seat lean across the whole 108th House, compared with the
hand-set curve used before v6.13:

| Seat alignment (party-signed) | n | Measured mean | Hand-set curve |
|---|---|---|---|
| −1.0 to −0.34 (opposed) | 29 | 0.255 | 0.164 |
| −0.34 to 0 | 42 | 0.130 | 0.098 |
| 0 to 0.34 | 71 | 0.086 | 0.070 |
| 0.34 to 0.67 | 100 | 0.060 | 0.055 |
| 0.67 to 1.0 (safe) | 197 | 0.046 | 0.034 |

The hand-set curve expected too little crossing everywhere, most of all in
opposed seats. The two parties' curves also differ (measured D intercept
0.128 vs R 0.076, and different bends at a swing seat).

Predicting 2004 vote share (N = 300 contested incumbents):

| Measure | Coefficient | t | ΔR² |
|---|---|---|---|
| Break rate minus pooled measured expectation (per SD) | 1.41 | 4.4 | 0.0307 |
| Break rate minus **per-party** measured expectation (per SD) | 1.46 | 4.7 | 0.0338 |
| Pre-v6.13 score (loyalty floored at 50) | 0.197/pt | 3.5 | 0.0237 |
| **v6.13 symmetric score** | 0.059/pt | 4.9 | **0.0367** |

Split at the expectation, the loyal side carries the strongest signal:
**2.27 points per SD below expectation (t=3.4)**, against 0.96 (t=2.1) above
it. The v6.6 rule held that side at 50, which discarded the strongest signal
in the test. The effect is similar in safe seats (1.52, t=2.8) and other seats
(1.36, t=3.9).

**Changed:** the expected break rate is now measured from the chamber every
run, per party, with a bend at a swing seat (`compute_constituent_reference`).
Scoring is symmetric around it, and the scale is the chamber's
90th-percentile deviation. (Since v6.15 the score turns back down past that
deviation on the crossing side, and the loyal side reaches 0 only at four
times it; see sections 8 and 9.)

### 5. No discount for flank-side defectors

Members who break from their party's flank did not fare worse for it. Their
extra slope is +2.17 (t=1.9, n=32), the wrong sign for a discount.
`CROSSING_QUALITY_DISCOUNT` (inert at 0.0) was **removed**.

### 6. Congress-specific positions (Nokken-Poole)

A congress-specific position for the 108th House (the first principal
component of its own roll calls, the same idea as Nokken-Poole) correlates
0.972 with DW-NOMINATE, yet predicts 2004 better (−1.31, t=−3.1, ΔR² 0.0170)
than DW-NOMINATE does (−1.04, t=−3.0, ΔR² 0.0138). Entered together, the
congress-specific position dominates (−0.94, t=−2.0, vs −0.59, t=−1.5).
Scoring the current term rather than the career (AGENTS.md principle 6)
points the same way. **Changed:** `fetch/voteview.py` reads
`nokken_poole_dim1`, falling back to `nominate_dim1` for a whole chamber
only when Voteview has not yet published Nokken-Poole scores for it.

### 7. The cosponsorship position-mismatch discount

Once loyalty is scored directly, the v6.7 discount (an SVD cosponsorship
position in the extreme tercile, applied only to loyalists) measures the same
thing a second time. v6.8 found that kind of double count doing damage. Where
both could be tested, in 2004, position adds little once the break-rate
deviation is in the model (−0.12, t=−0.3; the two correlate at −0.58). The
discount was **removed**, along with `party_ideology_bounds.json`, which only
it read.

### 8. Breaking far above expectation

**The question.** The component rises with a member's break rate until it
saturates at 100. A member who breaks with their party far more than the seat
calls for may be as far from what their voters sent them to do as a member
who never breaks. If so, the score should peak and then fall. The question
came from a senator who scored 100: on Voteview's party-unity votes, a break
rate of 1.2% in the 118th Senate and 20.4% in the 119th, 89 of the 159 breaks
in the 119th on cloture or confirmation votes for nominations.

"What voters sent them to do" depends on which voters. Fenno's (1978)
concentric constituencies separate the member's whole seat from their own
party's voters, so this section tests both.

**The whole seat: Senate general elections 1990–2024.** Every Senate roll call
from the 101st to the 118th Congress (Voteview) is joined to MIT Election
Lab's 1976–2024 Senate returns. That gives 461 contested incumbents across 18
elections, replacing the underpowered 47-member replication above. The
expectation is fit per party and per congress, exactly as
`compute_constituent_reference` does it (fit and saturation point on members
with at least 20 party-labeled votes). Independents are scored with the party
they vote with on party-unity roll calls, as the pipeline scores them with
their caucus. Controls are a quadratic in the
own-party presidential vote plus year × party effects. Standard errors are
clustered by senator.

| Measure (per SD of deviation) | Coefficient | t |
|---|---|---|
| Signed deviation (shipped direction) | 0.76 | 1.8 |
| Folded \|deviation\| | 1.15 | 2.2 |
| Squared term | 0.14 | 0.7 |
| Crossing side, up to saturation | 2.34 | 1.9 |
| Crossing side, past saturation (n=31) | 0.21 | 0.2 |

The general electorate does not penalize heavy breaking. The slope past
saturation is flat, and the folded term's positive sign comes from a loyal
side that is not rewarded (−0.59, t=−0.5). The House 2004 test agrees:
folded 0.26 (t=0.6), past saturation −0.33 (t=−0.3, n=19). Split by period,
the whole association fades after 2008: signed deviation 1.02 (t=1.9) for
1990–2008 and 0.37 (t=0.6) for 2010–2024. That fits the nationalization of
Senate elections (Bonica & Cox 2018; Utych 2020).

**The member's own party: House primaries 1990–2010.** House roll calls from
the 101st to the 111th Congress are joined to Pettigrew, Owen & Wanless's
House primary returns. That gives 3,869 incumbents: 27% faced a primary
challenger and 38 lost.

| Outcome | Folded \|deviation\| | Squared term | Past saturation |
|---|---|---|---|
| Drew a challenger | 0.02 (t=1.7) | 0.00 (t=1.4) | −0.00 (t=−0.1) |
| Lost the primary | 0.01 (t=1.3) | 0.00 (t=1.3) | 0.01 (t=1.3) |
| **Primary vote share, contested (N=1,044)** | **−1.99 (t=−2.4)** | **−0.37 (t=−2.4)** | **−3.02 (t=−2.0, n=79)** |

Among incumbents who were challenged, primary vote share falls with distance
from the expectation. The fall is concentrated past saturation: members more
than 2 SD above expectation averaged 70.5% of the primary vote, against
77.4% just below it. Being more loyal than expected costs nothing here (0.53,
t=0.4). So a member's own party's voters do take something away for heavy
defection, which is the pattern this section's question predicted. The
effect is modest, and it rarely decides a nomination: challenges and losses
barely move.

**The literature.** No published study estimates where "too much" defection
begins. Its findings sort by audience in the same way as the tests above:

- **General electorates reward independence from the party, relative to the
  seat.** See Canes-Wrone, Brady & Cogan 2002 and Carson et al. 2010, and in
  the Senate Algara & Zamadics 2019. The reward is fading (Bonica & Cox
  2018), often goes unnoticed (Donnelly 2019; Dancey & Sheagley 2018), and in
  some tests is null (Dancey, Henderson & Sheagley 2024; Cowley & Umit 2023
  for the UK).
- **Co-partisans and primary voters reward loyalty.** Pyeatt (2015, *LSQ*)
  finds incumbents "receive benefits in the primary from greater levels of
  partisanship." Anderson, Butler & Harbridge-Yong (2020) find that primary
  voters punish members for working with the opposition. Harbridge &
  Malhotra (2011) find that strong partisans do not reward bipartisanship.
  Mummolo, Peterson & Westwood (2021) find co-partisans stay loyal until a
  candidate takes dissonant stances on four or more salient issues, the only
  threshold estimate in the literature, though it measures disagreement on
  issues, not a break rate. Hirano et al. (2010) and Boatright (2013) find
  primary punishment is rarer than commonly assumed. The House primary test
  above is consistent with both camps: a real cost in vote share, rarely a
  lost seat.
- **What voters reward is agreement, not defection as such.** See Ansolabehere
  & Jones 2010, Ansolabehere & Kuriwaki 2022, and Hollibaugh, Rothenberg &
  Rulison 2013. A break helps when it moves the member toward the voter and
  should hurt when it moves the member past them. A break *count* cannot
  tell those apart. Kirkland & Slapin (2017, 2018) show many frequent
  defectors break from the party's flank, so defection is not the same as
  moderation.
- **Voters credit rebels with integrity without feeling better represented by
  them.** See Campbell, Cowley, Vivyan & Wagner 2019 and the 2023 European
  PSRM experiments. Rewarding that trait is not the same as measuring
  representation.
- **Normatively, the target depends on the model of representation.**
  - Under the responsible-party model (APSA 1950) or Mansbridge's (2003)
    promissory representation, defection is a failure of the mandate.
  - Under the delegate model, only agreement with constituents counts.
  - Under the trustee model, the break rate is irrelevant.
  - Pitkin (1967) rejects both pure loyalty and pure independence as
    representation.

**What this settles and what it does not.** A score that keeps rising with
defection is supported only from the general electorate's side, and only
until 2008. From the member's own party's side, heavy defection has a
measured cost past saturation, and nothing in the literature supports
treating more defection as better representation beyond the seat's
expectation. Where "too much" begins has no published estimate. The only
measured turning point is this section's saturation point.

**Changed (v6.15):** the score represents both audiences: the seat that
elected the member and the party label it elected them under. The
seat-relative vote score still peaks at the saturation deviation, which the
chamber measures every run. Past it, the score falls at the same rate it rose
(`OVER_BREAK_DECLINE = 1.0`), reaching 50 at twice the saturation deviation
and 0 at three times. The decline rate is a design weight, not a fitted one.
Mirroring the rise was chosen because the measured slopes are of similar size
with opposite signs (+2.34 per SD rising in the Senate, −3.02 falling in House
primaries), so the data gives no basis for an asymmetric shape.

The decline alone costs almost none of the electoral signal the symmetric
v6.13 score had. Both are measured on the same chamber-wide saturation point.
With v6.13's loyal side, the peaked score predicts 2004 House vote share at
ΔR² 0.0354 (t=4.9), against 0.0368 for the symmetric score. The shipped loyal
side is gentler (section 9), which gives ΔR² 0.0285 (t=4.4).

### 9. How hard to score loyalty

**The problem.** In v6.13 the loyal side reached 0 one saturation deviation
below the expectation, the same distance at which the crossing side reaches
100. In today's polarized Senate that deviation is under 4 points. Using
2025 Voteview party-unity votes:

- A senator breaking on 4% of votes against an expected 8% scored 0.
- A senator breaking on 0% against an expected 6% scored 0.
- 28 of 101 senators fell below 25 on the vote component, mostly for being
  a few points more loyal than their seat's norm.

At equal distance from the norm, loyalty also scored lower than excess
disloyalty. Two gaps more loyal gave 0, while two gaps more disloyal gave 50.

**What the evidence says, by audience:**

- **General electorate, House 2004:** loyalty beyond the norm is penalized,
  the strongest effect in the study (section 4).
- **General electorate, Senate 1990–2024:** no effect (−0.59, t=−0.5), and
  none at all since 2010.
- **Own party's primary voters, House 1990–2010:** no effect (0.53, t=0.4).
  These voters punish excess *disloyalty* past the peak instead (section 8).
- **Literature:** primary voters reward loyalty (Pyeatt 2015; Anderson,
  Butler & Harbridge-Yong 2020). General electorates reward independence
  (Carson et al. 2010).

**The test.** The loyal side's scale, in gaps below the expectation at which
the score reaches 0, with the rest of the v6.15 shape fixed:

| Loyal side reaches 0 at | House 2004 ΔR² (t) | Senate 1990–2024 ΔR² (t) |
|---|---|---|
| 1× (v6.13) | 0.0354 (4.9) | 0.0031 (1.2) |
| 2× | 0.0326 (4.8) | 0.0036 (1.2) |
| **4× (shipped)** | **0.0285 (4.4)** | **0.0036 (1.2)** |
| 8× | 0.0254 (4.2) | 0.0036 (1.2) |
| held at 50 | 0.0215 (3.9) | — |

The House prefers a steep loyal side. The Senate prefers a flat one, weakly:
nothing there is significant. Holding loyalty at 50 loses clearly in the
House, so loyalty stays scored.

**Changed (v6.15):** `LOYAL_SIDE_SCALE = 4`. At equal distance from the norm,
extra loyalty now costs less than extra disloyalty past the peak. Two gaps
more loyal scores 25; two gaps more disloyal scores 50 and three gaps scores
0. On 2025 votes, 4 senators fall below 25 instead of 28 (the two loyal
senators above now score 36 and 29).

**Party balance.** The gentler loyal side and the over-break decline make
the shape lopsided, so each party's average no longer sits at exactly 50.
Across every Senate from the 101st to the 119th, the parties' average vote
scores differed by 1.8 points on average under v6.15, against 2.2 under
v6.13. The sign changed from one Congress to the next, so the shape shows no
systematic lean toward either party. Averages now sit around 52–55.

The 119th Senate has the widest gap in the series: 5.3 points, D higher.
Its three heaviest breakers past the peak are Republicans. In a given Congress, the gap follows who breaks far past the
norm.

This is a design weight, chosen on where the Senate and primary evidence
point. The House 2004 fit is the cost: ΔR² falls from 0.0354 to 0.0285, and
the score still carries a clear signal there (t=4.4).

### 10. Peaked at the seat's norm, measured in standard deviations (v6.16)

**The problem.** Two senators in the live 119th Senate showed the v6.15
shape measuring the wrong thing.

- **A swing-seat Democrat** broke on 3.5% of 172 votes against an expected
  7.1%, and scored 41. Most swing-seat Democrats break 2–5%; a few heavy
  breakers (16% to 22%) pulled the least-squares
  expectation up to 7.1%, so the typical member sat below it. Under v6.15,
  33 of 47 Democrats and 46 of 53 Republicans scored under 50.
- **A safe-seat Republican** broke on 5.5% against an expected 1.5%, and
  scored 90.
  The v6.15 scale was percentage points, 4.9 of them for the whole chamber.
  Four extra points on a 1.5% expectation is breaking more than three times
  as often as the seat's norm, yet it counted the same as four points on 7%.

**Changed (v6.16):**

- **Standard deviations per vote, not points.** A member's gap is the
  Pearson residual (rate − p) / √(p(1 − p)), p the seat's expected rate
  (`seat_residual`). The binomial spread is small where the expected rate is
  small, so the same few points count for more there. Per vote rather than a
  z-score, so the same behavior reads the same in March and in December. The
  loyal side is naturally bounded (no member breaks fewer than zero times),
  while breaking can run many standard deviations high. p is kept half a
  vote of the member's record from 0 and 1, the usual continuity correction.
- **A fractional-logit expectation.** A least-squares line predicted 0% for
  the safest seats, where no residual exists (on the 119th Senate it gave
  Vermont and Maryland Democrats 0%). The logit fit (Papke & Wooldridge
  1996) stays inside (0, 1) and keeps each party's average rate. It is
  fitted by Newton's method with step-halving: plain iteratively reweighted
  least squares diverged to coefficients in the billions on the 118th
  Senate's Democrats.
- **100 at the expectation.** The score asks whether a member does what
  their seat elected them to do, so breaking about as often as comparable
  same-party members is the top of the scale. It falls linearly to 0 at 1.5
  scales above the expectation and 3 below (`CROSSING_ZERO_GAPS`,
  `LOYAL_ZERO_GAPS`), loyalty the gentler side as in v6.15.
- **One scale per party.** Each party's scale is the 90th percentile of its
  own members' |residual|. On one pooled scale the more cohesive party in a
  given Congress scores higher simply for sitting closer to its own norm.

**What the evidence says.** Same members, same outcomes and controls as
sections 4 and 8. Coefficient per score point, t, and the R² the score adds
to the controls:

| Outcome | v6.15 score | v6.16 shape, v6.15 points | **v6.16 (shipped)** |
|---|---|---|---|
| House 2004 general, vote share | +0.088 (4.4), ΔR² 0.0285 | −0.032 (−2.4), 0.0079 | **−0.032 (−2.5), 0.0085** |
| Senate generals 1990–2024, vote share | +0.035 (1.2), 0.0036 | −0.039 (−2.5), 0.0088 | **−0.039 (−2.6), 0.0102** |
| House primaries 1990–2010, contested, primary share | −0.015 (−0.5), 0.0003 | +0.058 (2.4), 0.0080 | **+0.053 (2.4), 0.0081** |
| drew a primary challenger | +0.000 (0.6) | −0.001 (−1.6) | **−0.001 (−1.7)** |
| lost the primary | −0.000 (−1.2) | −0.000 (−1.1) | **−0.000 (−0.8)** |

The two audiences disagree, and the shape decides which one the score
follows. The member's own party's primary voters reward exactly this shape:
incumbents who score higher on it win a larger share of a contested primary
(and draw challengers slightly less often, though that is not significant). The whole electorate does the
opposite: in Senate general elections and the 2004 House elections, members
who break more than their seat's norm did somewhat better, and heavy breakers
were not punished (section 8: flat past saturation in the Senate).

The change of unit is not what moves these numbers; the shape is. The
middle column applies v6.16's shape to v6.15's point scale and lands in the
same place.

**The choice.** v6.16 follows the member's own party's voters. The score
measures whether a member does what they were elected to do, under a party
label as well as by a seat, not what maximizes their general-election vote
share. This is a stated choice with evidence on both sides, not a finding
that settles it. The general-election result is reported here and on the
site's About page.

**Zero points.** Where the score reaches 0 on each side, in scales:

| Crossing / loyal | House 2004 | Senate 1990–2024 | House primaries |
|---|---|---|---|
| 1.0 / 1.0 | 0.000 (0.0) | −0.026 (−2.1) | +0.027 (1.5) |
| 1.0 / 3.0 | −0.034 (−3.3) | −0.031 (−2.4) | +0.037 (2.1) |
| **1.5 / 3.0 (shipped)** | **−0.032 (−2.5)** | **−0.039 (−2.6)** | **+0.053 (2.4)** |
| 1.5 / 6.0 | −0.041 (−3.4) | −0.037 (−2.5) | +0.051 (2.4) |
| 2.0 / 4.0 | −0.037 (−2.6) | −0.047 (−2.6) | +0.070 (2.6) |
| 3.0 / 6.0 | −0.051 (−2.5) | −0.060 (−2.6) | +0.100 (2.7) |

Wider zero points strengthen both associations together, so the data does
not pick one. 1.5 / 3 keeps loyalty at half the cost of excess breaking, and
puts the heaviest breakers of today's Senate at or near 0.

**Where a breakdown's "0 at" number comes from.** The zero point a scorecard
prints is the zero multiple times the member's party's scale:
`CROSSING_ZERO_GAPS × scale` above the expectation, `LOYAL_ZERO_GAPS × scale`
below it. The scale is the 90th percentile of the party's |residual|, measured
from the chamber every run. On the live 119th Senate (September 2026 — these
figures come from the site's own per-senator breakdowns run through the
scorer, not from the research script) the
Democratic scale was 0.37 standard deviations per vote and the Republican 0.33,
so a Democrat reaches 0 at 1.5 × 0.37 ≈ 0.56 more independent than their seat's
norm, a Republican at about 0.49.

The two multiples are design weights; nothing above estimates them. They were
chosen for what they do on real records:

- **1.5 above.** Reserves 0 for the heaviest breakers, members well past where
  even their party's most out-of-pattern tenth sits. On the live Senate that is
  5 of 100, at 1.59 to 5.45 gaps. Six more sit between 1 and 1.5 gaps, and score between 33
  and 0.
- **3 below.** Loyalty costs half as much per gap. The residual is bounded on
  this side (nobody breaks fewer than zero times), so in practice nobody
  reaches 0: the most loyal senator was 0.72 gaps below their norm, which
  costs 24 points.

A multiple would need re-deciding if the chamber's spread changed shape, not
merely size: the scale moves with the chamber on every run.

**Party balance.** Across every Senate from the 101st to the 119th, the
parties' average vote scores differ by 1.8 points on average under v6.16,
the same as under v6.15 and against 2.2 under v6.13, and which party is
higher changes from one Congress to the next. On one scale pooled across
both parties the average gap was 7.9 points, and 22.3 in the worst Congress,
the sign following whichever party was more unified. That is why the scale
is per party. Averages sit around 73–80: most members are close to their
seat's norm.

### 11. Only breaks toward the other party (v6.20)

**The problem.** A House Republican known as a reliable party-line vote
scored 8 on Constituent Alignment in September 2026, with a vote part of 0.
Two things produced that:

- **The window.** The rate was read off the member's stored votes, a sample of the
  chamber's latest 120 roll calls plus key bills: 6 breaks in 76 party-line
  votes, 7.9%. Over every roll call of the 119th Congress it was 15 of 304,
  4.9%.
- **The direction.** 14 of those 15 came from the Republican right flank:
  votes with Democrats against bills most Republicans backed, alongside
  the members furthest from Democrats. That is not independence toward the
  seat, and how far toward the flank a member sits is already scored, by
  position congruence. Counting it as a break charged it twice. Another
  House Republican showed the same pattern (14 of 15 from the flank).

**Changed (v6.20).** A break counts only when, on that roll call, the
party's members who broke sit on average nearer the other party (mean
first-dimension position, from the same Voteview section position
congruence reads; since v6.27 each position weighted by its reliability,
section 14) than the party does (`party_line_record._toward_other_party`). Other breaks are listed on the
scorecard as from the flank, and not counted. The rate is measured over
every roll call the chamber recorded this Congress, not a sample.

**What the evidence says.** The same tests as section 10, recomputed with
only centerward breaks counted (the denominator is still every party-unity
vote):

| Outcome | All breaks (v6.16) | Centerward only |
|---|---|---|
| Senate generals 1990–2024, vote share (N = 461) | −0.039 (−2.6), ΔR² 0.0102 | −0.042 (−3.0), 0.0115 |
| House primaries 1990–2010, contested, primary share | +0.053 (2.4), 0.0081 | +0.057 (2.6), 0.0093 |
| drew a primary challenger | −0.001 (−1.7) | −0.001 (−1.6) |
| lost the primary | −0.000 (−0.8) | −0.000 (−1.0) |

Centerward breaks predict both audiences at least as well as all breaks,
slightly better on the two outcomes that were significant. Party balance
holds: the parties' average vote scores differ by 2.1 points across Senates
(1.8 counting all breaks).

### 12. Each measure once (v6.20)

**The problem.** A nominee usually gets two roll calls, cloture then
confirmation; a bill can get a motion to proceed, cloture and passage. A
member who opposes the nominee votes the same way on both, so one
position counted twice. This has grown: 5% of the 101st Senate's roll calls
repeated a measure already voted on, 13% of the 110th's, 30% of the 115th's
and 37% of the 119th's, nearly all of it cloture on nominations. In the
House it is about 4% throughout.

**Changed (v6.20).** Each measure counts once
(`party_line_record.measure_key`): a member broke on it if they broke on any
of its party-line roll calls. The stages of one measure are cloture,
nomination, motion to proceed, passage, adoption of a resolution,
conference report, ratification, veto override and concurring in the other
chamber's amendment. An amendment, a motion to commit or to waive, and a
point of order are each their own question, though they name the bill.

**What the evidence says.** On top of section 11:

| Outcome | Centerward | Centerward, each measure once |
|---|---|---|
| Senate generals, vote share | −0.042 (−3.0), 0.0115 | −0.042 (−3.0), 0.0114 |
| House primaries, primary share | +0.057 (2.6), 0.0093 | +0.056 (2.5), 0.0091 |
| drew a primary challenger | −0.001 (−1.6) | −0.001 (−1.6) |
| lost the primary | −0.000 (−1.0) | −0.000 (−1.0) |

Neutral: most of the tested Senates predate the cloture era, and a
member's position rarely differs between stages of one measure (55 of
17,628 member-measures with repeats in the 119th Senate). It is adopted
because it is the right unit, a member's decision on a measure, and the
evidence shows it costs nothing.

**On the live 119th Congress** (roll calls through September 2026): the
House Republican median is 0.7% of measures and the 90th percentile 3.6%;
Senate Republicans 0.0% and 3.6%, Democrats 1.3% and 6.8%. The House
Republican above breaks toward Democrats on 2 of 298 measures (0.7%, the
party median), with 14 more from the flank. The heaviest breakers (60 to 103
breaks each) remain the heaviest, as before; the majority leader's
reconsider switches still don't count.

### 13. The seat's expected position: partisan lean, not voters' self-placement (tested 2026-10)

Position congruence compares a member's roll-call position with what a
same-party member of a seat with that partisan lean typically holds. Partisan
lean measures how a seat votes for president, not how liberal or conservative
its voters say they are, and a state can sit further left or right than its
presidential vote suggests. So the obvious alternative was tested: the state's
mean self-placed ideology (Cooperative Election Study 2024, weighted,
`ideo5`), as the expectation for senators.

Leave-one-out error of per-party fits of the 119th Senate's Nokken-Poole
positions:

| Party | Partisan lean | Voters' ideology | Both |
|---|---|---|---|
| D (n=47) | 0.119 | 0.134 | 0.117 |
| R (n=57) | 0.196 | 0.197 | 0.200 |

Voters' self-placement predicts senators' positions no better than partisan
lean, and adding it does not help. The limit is not the measure: two senators
of one party from one state share an electorate, yet the 43 such pairs sit a
median 0.093 apart and as much as 0.485, so much of where a senator sits is
the senator, not the seat. Partisan lean stays the expectation.

### 14. Position congruence's score: shape, scale and thin records (v6.27, tested 2026-10)

**The question.** Position congruence reads low beside everything around it:
across the 119th Congress its mean is about 50 in both chambers, and 10.7%
of Senate Republicans sit at 0 on it (unweighted, with seat lean from the
presidential vote as in this section). Section 1 tested the position as a
continuous measure. The score is a transform of it (clipped at a saturation
point, 50 at the seat's expectation), and three things about that transform
had never been put in front of election results:

- **Shape.** The vote part is 100 at the seat's expectation and falls both
  ways (v6.16). Position congruence is 50 at the expectation and rises
  toward the seat's center. Should it peak at the expectation too?
- **Scale.** The score reaches 0 at the 90th percentile of |extremity|,
  pooled across both parties. The vote part moved to one scale per party in
  v6.16 (section 10). Republican positions spread more widely around their
  expectation (the 119th Senate's per-party 90th percentiles are 0.335 R and
  0.186 D, pooled 0.262, with seat lean from the presidential vote; the
  pipeline's Cook PVI gives 0.258 under v6.26 and 0.268 under v6.27), so on
  the pooled scale more Republicans reach 0.
- **Thin records.** A Nokken-Poole position is estimated from one
  congress's roll calls. Members with few of them (sworn in late, gone
  early, or anyone early in a Congress) were scored at full strength.

**Shape and scale against elections.** The four combinations, each scored
per congress the way the pipeline scores it: congress-specific Nokken-Poole
positions from Voteview (Voteview's 0, 0 placeholders dropped), per-party
fits on seat lean, the scale over full records, and v6.27's reliability
weight (nearly inert here: almost every incumbent in these panels has a full
record). The House rows use section 1's panel and controls; seat lean is
the presidential vote, as throughout this note, not the pipeline's Cook PVI. Coefficient per
score point, t, and the R² the score adds:

| Outcome | Linear, pooled (shipped) | Linear, per party | Peaked, pooled | Peaked, per party |
|---|---|---|---|---|
| House generals 1994–2010 (N=2,545) | **0.035 (5.2), 0.0090** | 0.035 (5.3), 0.0096 | −0.002 (−0.4), 0.0000 | −0.002 (−0.3), 0.0000 |
| Senate generals 1990–2024 (N=461) | **0.042 (2.5), 0.0114** | 0.040 (2.5), 0.0108 | −0.010 (−0.7), 0.0009 | −0.008 (−0.6), 0.0007 |
| House primaries 1990–2010, contested, primary share (N=1,044) | −0.009 (−0.5), 0.0003 | −0.008 (−0.4), 0.0002 | 0.021 (1.4), 0.0019 | 0.022 (1.4), 0.0022 |
| drew a primary challenger, per 10 points (N=3,869) | 0.004 (1.2), 0.0006 | 0.005 (1.3), 0.0008 | −0.003 (−1.1), 0.0005 | −0.004 (−1.4), 0.0008 |

- **Shape: kept.** The linear shape predicts the general election in both
  chambers. The peaked shape predicts nothing there. The Senate replicates
  section 3's symmetry (flank-ward −0.86, t=−1.4; center-ward −1.23,
  t=−1.8; equal slopes p=0.73), so sitting nearer the seat's center earns
  credit rather than costing it. Primary voters lean weakly the other way
  (peaked t=1.4), not significantly, so unlike the vote part (section
  10) there is no own-party signal strong enough to pull the shape over. A
  median member scoring about 50 here is the scale working as tested: 50
  means "where a same-party member of this seat sits". One caution: peaked
  versus linear is close to folded versus signed extremity, and each side's
  slope alone is not significant.
- **Scale: kept, as not settled.** Pooled and per-party scales predict
  equally well. The direct test asks whether voters respond to a NOMINATE
  unit of extremity (pooled) or to a unit of the member's own party's
  spread (per party): the right unit is the one with the same slope for
  both parties. Neither is distinguishable. Republicans minus Democrats,
  per NOMINATE unit: House +1.44 (t=0.5, p=0.65), Senate −3.31 (t=−0.5,
  p=0.61). Per own-party unit: House −0.15 (p=0.80), Senate −1.32 (p=0.34).
  Unlike the vote part's case, the pooled scale leaves the parties'
  averages about a point apart (over every Senate, unweighted: 50.5 D and
  51.6 R pooled, 50.8 and 50.9 per party). It widens the spread for the
  party that spreads more (unweighted, share at 0: 2.9% D and 10.1% R
  pooled, 5.8% and 6.8% per party; at 100: 1.6% and 6.1% pooled, 3.8% and
  2.6% per party). v6.27's weights don't change this, since nearly every
  senator's record is full. With no evidence for either, the shipped
  scale stays.

**Thin records: what a position from n votes is worth.** The score should
read a member's position at the strength it predicts where the member
really sits. Voteview's own positions measure that directly, with no model
of how Voteview estimates them. Some members served part of one Congress
and all of the next, or all of one and part of the next: a special-election
winner, or a member who left. They have a thin position and a full one for
adjacent Congresses, and a thin record that arises this way is a run of
consecutive votes, just as in the score. Read each against their party's
center that Congress (the median of its full records), signed toward the
party's flank, every pair keyed by the transition it spans:

    full = drift[chamber, transition] × weight(n) × thin
    weight(n) = min(1, w(n) / w(200)),   w(n) = n / (n + n0)

- **Drift per transition, from full records.** Drift is how far positions
  carry from one Congress to the next. It varies by era (a full record's
  slope on the next Congress's runs from about 0.66 to 1.00), so each
  chamber and transition gets its own, set by that transition's pairs of
  full records (both sides at least 200 scaled votes).
- **Full records count in full.** Full records show no gradient in their
  count: their slope relative to the drift is 0.99 to 1.01 from 200 to
  over 1,100 votes. So a full record counts 1, and n0 is the least-squares
  fit of the thin pairs alone.
- **What is left out:**
  - Members from outside the 50 states: the House's delegates have thin
    records because they vote only in the Committee of the Whole, not
    because they served part of a Congress.
  - Transitions with no full pairs.
  - A Congress still thin by the calendar.

`scripts/calibrate_position_confidence.py` runs this over Congresses
101–119: 8,098 full pairs, 99 thin and 25 with no count on the thin side
(and a career DW-NOMINATE position, as the score requires of such a
position; see below). The intervals are reproducible: members are
resampled in a fixed order, 1,000 times.

- **One curve for both chambers, chosen by held-out prediction.** Each
  candidate structure is judged the same way: each thin member's pairs
  are predicted from a fit (n0 and drift) made without that member, and
  the squared errors are summed. A more complex structure is used only if
  it beats the simpler one by more than a standard error of the
  member-by-member difference (the one-standard-error rule of Hastie,
  Tibshirani & Friedman 2009, section 7.10, in its paired form: their
  standard error of the best model's error, 0.234 here, is looser and
  agrees). A rerun decides again. Over
  the 99 thin pairs:

  | n0 fitted | Held-out squared error |
  |---|---|
  | **once for both chambers (shipped)** | **1.456** |
  | once per chamber | 1.436 |
  | once per era (101–109, 110–119) | 1.597 |
  | once per direction (entrant, leaver) | 1.508 |
  | once, plus a party-line term | 1.561 |

  Per chamber is lower by 0.020, but the standard error of that
  difference is 0.070, so the chambers aren't shown to differ: fitted
  apart, their half points are 25 votes (Senate) and 85 (House). The
  House's interval (31–98) holds the pooled 50; the Senate's (1–50)
  reaches it only at its top. A rerun with more pairs could choose
  per chamber; it would have to win by more than the noise. Era, direction
  and the party-line term predict worse than one curve.

| | Estimate | 90% interval (members resampled, 1,000 times) |
|---|---|---|
| Votes at which a position counts half | **50** | 24–98 |
| n0 | 100 | 31 to the search grid's limit of 5,000 |
| Weight of a position with no count | **0.21** | 0.03–0.38 |

- **The curve is weakly determined.** The half point is n0 written
  another way, so it is no better determined; it is only bounded. The
  curve can't put it above 100 votes (as n0 grows, w(n) / w(200) falls to
  n / 200, which is 0.5 at 100), so the upper end, 98, is where the n0
  search stops: the data allow anything up to the linear curve.
- **A party-line term was tested and is not used.** That would make n0 a
  line in the share of roll calls on which the parties' majorities split,
  since in a more party-line Congress each vote might say less about a
  member's place within their party. Its slope is −4.25, the wrong
  direction, it barely changes the in-sample fit (squared error 1.384
  against 1.396), and it predicts held-out members worse (1.561 against
  1.456). An earlier draft of this change found a positive slope; it
  came from forcing one drift on every Congress, which loaded the era
  differences in drift onto n0.
- **Positions with no count carry some information.** They are members
  Voteview has barely scaled (newly sworn in, or with very few scalable
  votes). Measured only where the member also has a career DW-NOMINATE
  position, they count 0.21 from 25 pairs, but a few members carry it:
  leaving out one member at a time moves it between 0.13 and 0.26. A
  no-count position with no career position at all (a member just sworn
  in, with nothing yet to anchor it) is not measured by these pairs, and
  the score reads it as a position from no votes (50).
- **Each pair is predicted in its own direction.** A member whose thin
  record is the earlier one (an entrant) is predicted with the
  transition's forward drift, the slope of the later full position on the
  earlier. A member whose thin record is the later one (a leaver) is
  predicted with the reverse slope, of the earlier on the later.
- **Limits.** Centering on the party median rather than the seat's
  expectation understates the noise slightly, by the share of
  within-party position that seat lean explains (10% for House
  Republicans in the 119th, under 1% for Senate Republicans).
  The thin records here come from members who arrived or left
  mid-Congress. Every member's record early in a Congress is thin too,
  on a different agenda; whether those records behave like these is
  untested.

**Six approaches that don't work.** Each appeared in a draft of this
change and was replaced after review.

*The career-gap proxy.* The first draft fit gap² = drift + k / votes, where
the gap is between a member's Nokken-Poole position and their career
DW-NOMINATE position. It took k / drift as a cutoff, which came to 81
votes over 9,821 member-congresses of Congresses 101–118.
- Three of those points are Voteview's placeholders (0, 0 for a member it
  could not scale). Leaving them out moves the cutoff from 81 to 32.
- With them out it still swings: Senate 39, House 31, members with 5 or
  more votes 234. A member-clustered bootstrap gives 11 to 157 (5th to
  95th percentile).
- It also answered a different question. Drift around a career position
  is not the spread around the seat's expectation that the score centers
  on.

*Re-estimating positions from subsamples.* The second draft re-located
members from random subsets of their votes, by maximum likelihood under a
logit per roll call. It failed for three reasons:
- With a handful of party-line votes that estimate has no finite value and
  runs to its bound, which Nokken-Poole positions never do.
- Random subsets are the most favourable case. Real thin records are
  consecutive runs of votes, and a reviewer found that the first or last n
  votes give errors about 1.5 times larger.
- It pooled every era under one drift.

*One drift for every Congress.* The third draft used the pairs, but with a
single drift. That made n0 appear to rise with party-line voting, and it
let a full record's own attenuation shrink every member about 10%. Both
artifacts go away once drift is measured per transition.

*One curve per chamber.* The fourth draft fitted the chambers apart
because they predicted held-out members slightly better, a margin within
the noise of the comparison. The one-standard-error rule now decides,
and keeps one curve.

*Flank-rule switches without the right centering or strata.* The fifth
draft switched the flank rule at 53 votes, on pairs centered on the
median of each party's full records rather than as the rule centers, and
with noise and drift assumed independent everywhere. The sixth kept the
last full record until a new one was full, on a mistaken reading that the
dependence couldn't be modelled; within strata of distance from the
center it can (below).

**Shipped (v6.27).**
- The member's extremity is scaled by weight(n) before it is read against
  the saturation point. A position from 50 votes counts half, and one
  from 200 or more counts in full; n is the member's scaled roll calls
  this Congress (Voteview's `nominate_number_of_votes`).
  - A position with no reported count is weighted 0.21 when the member
    has a career DW-NOMINATE position, and read as no votes (50) when
    not.
  - One curve serves both chambers and every Congress, so nothing in it
    follows the sitting Congress. A new Congress needs no rerun and no
    setting. Rerunning the calibration (its Congress range follows the
    clock, and a Congress still thin by the calendar is skipped) only
    adds the newest pairs.
- The seat fits are taken over every member, since a thin position is
  noisy but not biased.
- The saturation scale is the 90th percentile of full records'
  extremities. A thin record's noise would widen a scale taken over
  everyone, and a scale taken over weighted extremities would cancel the
  weights whenever every record is equally thin.
  - Early in a Congress, with fewer than 40 full records, the chamber's
    last scale is carried. It describes the chamber's seats, not any
    member's record.
  - So the weights do pull thin positions toward 50: with every record at
    5 votes, a position at saturation scores about 46 instead of 0.
- Voteview's placeholders are dropped. A member a current section has no
  position for (those, or anyone Voteview hasn't placed yet) sits at 50,
  as a position from no votes would, instead of having the component
  left out. A section written before v6.27 has no counts and is read as
  before, unweighted, until the first v6.27 ingest rewrites it.
- Each section records its Congress, and a position counts only for roll
  calls of that same Congress (or the sitting one, for a member matched
  to no roll call).
  - Once a new Congress's roll calls are being scored, the component is
    left out until that Congress's Voteview export passes the ingest
    gates, as when no data exists.
  - A stored score's "show the math" keeps reading the positions it was
    scored on, while that Congress's section is on disk, until the next
    run rescores it.
- The weight applies to the DW-NOMINATE fallback too, as an uncalibrated
  extension: n0 was measured on Nokken-Poole positions, but this
  Congress's count is the current term's evidence either way.
- The flank-break rule of section 11 weights the defectors' and the
  party's mean positions the same way. It reads any Congress's section,
  since it needs only which side of their party the defectors sit and
  positions carry across Congresses. Early in a new Congress the last
  positions therefore classify its breaks, rather than every flank break
  counting.
  - Weighting means cannot help a lone defector, whose side is its own
    position's sign against the party's. So once the new Congress's
    section is in, the rule reads a member's last-Congress full record
    (kept beside the new section for this rule only, never scored) until
    their new record reaches a measured count, 92 votes. Each Congress's
    positions are read from their own party's weighted mean, so a
    party-wide shift between the two can't move a member against a party
    read from the other. Only the Congress just before is kept, and only
    a full last record replaces a new one (unless the new one counts for
    nothing yet, no votes): those are the cases the pairs measure.
  - The pairs, centered as the rule centers, are the evidence. A full
    record is on the same side of its party as the next Congress's full
    record 86% of the time. Thin records, against their pair's full
    record:

    | Votes | Pairs | Same side |
    |---|---|---|
    | 1–25 | 19 | 63% |
    | 26–50 | 24 | 54% |
    | 51–100 | 22 | 73% |
    | 101–150 | 21 | 86% |
    | 151–199 | 13 | 92% |

    A thin record is compared across a Congress there, the rule's
    comparison within one, so one Congress's drift has to be taken out.
    How much depends on how a record's noise and the drift combine: both
    flip a side mostly near the party's center (full records agree 62%
    within 0.02 of it, over 99% beyond 0.2). So the switch is modelled
    within three strata of distance from the center (under 0.05,
    0.05–0.1, beyond), noise and drift independent within each, agreement
    a logistic in log votes with an intercept per stratum. A thin record
    matches a last full record at 92 votes, in the full pairs' mix of
    strata (`prior_switch`); without the strata the figure is 95.
  - That point estimate is the switch: of the counts the data allow, it
    misplaces the fewest sides in expectation. It is uncertain: resampling
    members puts it between 34 and 525 votes (5th to 95th percentile),
    before a full record in 85% of resamples, since only 34 thin pairs
    have over 100 votes. A switch past a full record would be a full
    record, which is where the rule switches at the latest. This first
    applies in the 120th Congress: a 119th section written before v6.27
    keeps no earlier positions.
  - On the 119th Congress's party-unity roll calls (majorities opposed,
    Voteview's votes) the weighting alone reclassifies none of 2,351
    Senate breaks and 2 of 6,055 House breaks.

**Effect on the 119th Congress** (Voteview exports of 2026-10-03, through
the pipeline's own build with Cook PVI seat lean). This compares v6.26,
which read placeholders as positions and weighted nothing, with v6.27:

| | Senate | House |
|---|---|---|
| Saturation | 0.258 → 0.268 | 0.224 → 0.221 |
| Mean change in the component | 1.69 | 0.58 |
| Mean change in Constituent Alignment | 0.51 | 0.18 |

- Full records move only through the scale and the refit seat lines. The
  Senate's mean change comes from dropping its placeholder, which moves
  the Republican slope from +0.0033 to +0.0018, and from its scale now
  being measured on full records.
- Records under 200 votes, with no count or with no position move most:
  four in the Senate and twelve in the House.
  - Two recently sworn-in representatives with no count and no career
    position move from 23.3 and 31.1 to 50.
  - Representatives with 77 and 72 votes move from 93.2 to 78.6 and from
    75.3 to 66.1.
  - Representatives with 116–171 votes move 1 to 4 points.
  - Senators with 53 and 187 votes move about 1.
  - Members scored from 1 to 39 votes or a placeholder, all since departed,
    move by up to 50 points.
  - Of these, two far beyond saturation (135 and 182 votes) stay at its
    end.

## What the evidence does not settle

- **The association fades over time.** Per election, the position coefficient
  is −1.46, −1.93, −1.60, −0.94, −1.13, −0.49, +0.52, +0.71 (1994 to 2010).
  The 2008 and 2010 elections run the other way, in line with the
  nationalization of House elections, where the national swing swamps
  incumbent-specific signals. The pooled result stands, but the electoral
  validation is weaker in the most recent years tested.
- **Loyalty was tested in one election.** Only the 108th House roll calls were
  available here. Carson et al. (2010) find the same pattern across many more
  congresses, but this replication is one year.
- **The shape follows primary voters, not the general electorate.** Since
  v6.16 the score peaks at the seat's norm, which predicts own-party primary
  vote share and runs against general-election vote share (section 10). No
  Senate primary returns were tested (the Pettigrew, Owen & Wanless data are
  House only). Senators are scored on the same shape by extension.
- **The Senate loyal side does not replicate.** With every Senate election
  from 1990 to 2024 (N=461, section 8), the crossing side holds (2.34 up to
  saturation, t=1.9) but the loyal side does not (−0.59, t=−0.5), and the
  whole association is weak after 2008. That is one reason v6.15 flattened
  the loyal side (section 9).
- **Position congruence's scale is not settled.** One scale for both parties
  and one per party predict elections equally well, and the test of which
  unit voters respond to is inconclusive in both chambers (section 14). The
  pooled scale stays because nothing favors changing it. Unweighted, it
  leaves the party whose senators spread more widely around the seat's
  norm (Republicans, in most Senates since 1989) with more members near 0
  and near 100.
- **The thin-record weight rests on 99 members.** Few members have a thin
  record next to a full one, so the half point has a wide interval
  (24–98 votes, the upper end where the n0 search stops), and whether
  the chambers differ can't be settled: fitted apart they come to 25 and
  85 votes, but a curve per chamber predicts held-out members better by
  only 0.020, under a third of that difference's standard error (0.070). A member's thin Congress
  is also often their first or last, though a curve per direction
  (entrants, leavers) predicts worse than one for both, and whether early
  records in a Congress behave like these is untested. The no-count
  weight rests on fewer still, and the flank rule's switch (92 votes) on
  34 thin pairs above 100 votes, so its interval runs from 34 to past a
  full record. Rerunning the calibration adds each Congress's new pairs,
  and decides the structure and the switch again.
- **The 70/30 weighting is not fitted.** In 2004 the vote component had the
  larger independent association, which supports it keeping the majority
  weight. No multi-election estimate of the ratio exists to fit the weight
  from.
- **Vote share is a proxy.** Electoral response shows that constituents
  notice and judge these things. It does not measure issue-by-issue
  congruence. The seat target is still one-dimensional presidential lean,
  and members also answer to their reelection constituency (Fenno 1978;
  Clinton 2006).
- **The live statistic differs from this test's.** The pipeline measures
  break rates on Civitas's own key and recent roll calls, not on CQ
  party-unity votes. Since v6.15 it counts each party-labeled roll call once,
  unweighted, as this test does. It used to weight votes by the bill's
  content lean. The expectation is measured on that same statistic every
  run, so scores stay internally consistent, but the magnitudes above do not
  carry over one-for-one.
