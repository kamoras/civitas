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
DW-NOMINATE first dimension) than the party does
(`party_line_record._toward_other_party`). Other breaks are listed on the
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
