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
which downloads the public data at pinned commits and runs every test,
except the earlier drafts' figures in section 14, which are marked as
such.

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
first-dimension position from the chamber's Voteview data; early in a new
Congress, the last Congress's) than the party does
(`party_line_record._toward_other_party`). Since v6.27 each position is
weighted by its reliability, and until a member's new record reaches 200
roll calls their last Congress's full record, where they have one, decides
their side (section 14; never for a member who switched parties, during
this Congress or between the two); before the new section passes its gates
everyone's last positions do (except a position recorded under the other
major party, which is never read for the member), and a member with no
usable position this Congress is read on the last one. Other breaks are
listed on the scorecard as from the flank, and not counted. The rate is
measured over every roll call the chamber recorded this Congress, not a
sample.

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
weight (nearly inert here: 0.4% of the House panel's incumbents and 2.3% of
the Senates' members are read at less than full weight). The House rows use
section 1's panel and controls; seat lean is the presidential vote, as
throughout this note, not the pipeline's Cook PVI. Coefficient per score
point, t, and the R² the score adds:

| Outcome | Linear, pooled (shipped) | Linear, per party | Peaked, pooled | Peaked, per party |
|---|---|---|---|---|
| House generals 1994–2010 (N=2,545) | **0.035 (5.2), 0.0090** | 0.035 (5.3), 0.0096 | −0.002 (−0.4), 0.0000 | −0.002 (−0.3), 0.0000 |
| Senate generals 1990–2024 (N=461) | **0.042 (2.5), 0.0115** | 0.040 (2.5), 0.0108 | −0.010 (−0.7), 0.0009 | −0.008 (−0.6), 0.0007 |
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
  unit of extremity (pooled) or to a unit of the member's own party's spread
  (per party): the right unit is the one with the same slope for both
  parties. Neither is distinguishable. Republicans minus Democrats, per
  NOMINATE unit: House +1.44 (t=0.5, p=0.65), Senate −3.31 (t=−0.5, p=0.61).
  Per own-party unit: House −0.15 (p=0.80), Senate −1.32 (p=0.34). Unlike
  the vote part's case, the pooled scale leaves the parties' averages about
  a point apart (over every Senate, unweighted: 50.5 D and 51.6 R pooled,
  50.8 and 50.9 per party). It widens the spread for the party that spreads
  more (unweighted, share at 0: 2.9% D and 10.1% R pooled, 5.8% and 6.8% per
  party; at 100: 1.6% and 6.1% pooled, 3.8% and 2.6% per party). v6.27's
  weights barely change this, since only 2.3% of senator-Congresses are read
  at less than full weight: with them on, the shipped scale's means are 50.6
  D and 51.5 R and its shares at 0 are 2.6% and 10.0%. With no evidence for
  either, the shipped scale stays.

**Thin records: what a position from n votes is worth.** The score should
read a member's position at the strength it predicts where the member
really sits. Voteview's own positions measure that directly, with no model
of how Voteview estimates them. Some members have a thin record in one
Congress and a full one in the next, or the reverse: a special-election
winner, a member who left, or one absent for much of a Congress (illness,
a campaign, a cabinet nomination). They have a thin position and a full
one for adjacent Congresses. Their thin records include the kinds the
score sees (partial service, long absences), but not everyone's
early-Congress record, which is untested. Read each against their party's
center that Congress (the median of its full records), signed toward the
party's flank, every pair keyed by the transition it spans:

    full = drift[chamber, transition] × weight(n) × thin
    weight(n) = min(1, w(n) / w(200)),   w(n) = n / (n + n0)

- **Drift per transition, from full records.** Drift is how far positions
  carry from one Congress to the next. It varies by era (a full record's
  slope on the next Congress's runs from about 0.66 to 1.01,
  `drift_range`), so each chamber and transition gets its own, set by that
  transition's pairs of full records (both sides at least 200 scaled
  votes).
- **Full records count in full.** Full records show no gradient in their
  count: their slope relative to the drift is 0.99 to 1.01 from 200 to
  over 1,100 votes (`full_slope_by_votes`). So a full record counts 1, and
  n0 is the least-squares fit of the thin pairs alone. Where a full record
  starts, 200 votes, is a convention: the data show no gradient from there
  up, but nothing chose it over a lower count.
- **What is left out:**
  - Members from outside the 50 states: the House's delegates have thin
    records because they vote only in the Committee of the Whole, not
    because they served part of a Congress.
  - Members who switched parties during a Congress. Voteview lists them
    under a new ICPSR id with the same bioguide id, so each id's record
    covers part of the Congress: the old id reads as a member who left,
    the new one as a member who arrived, and the change of party would be
    booked as a thin record's noise. (A switch between Congresses changes
    the id too, so it never forms a pair.)
  - Transitions with no full pairs.
  - A Congress still thin by the calendar.

`scripts/calibrate_position_confidence.py` runs this over Congresses
101–119: 8,079 full pairs, 95 thin and 25 with no count on the thin side
(and a career DW-NOMINATE position, as the score requires of such a
position; see below). Thin records arise here from members who arrived
or left mid-Congress and from members absent for much of one (illness, a
campaign, a cabinet nomination), as many of the score's do, so every thin
pair counts; whether absences make a thin record say less is tested
below (attendance). The intervals are reproducible: members are resampled
in a fixed order, 1,000 times.

- **The latest era's curve, chosen by predicting forward.** The weight is
  always applied to a Congress the calibration hasn't seen, so the
  structure is chosen by how well it predicts the next one
  (`forward_test`): each transition's thin pairs are predicted from a fit
  (n0) on the earlier transitions only, with the drift, shared by every
  structure, from the predicted transition's own full pairs, rolling
  forward one transition at a time (from the fourth with thin pairs:
  `MIN_TRAIN_TRANSITIONS`, a convention), and the squared errors are
  summed by member. One curve for both chambers ships unless a structure
  the score can apply (one per chamber, or the latest era's, split at the
  110th) predicts better by more than the standard error of the paired
  difference. Over 82 members:

  | n0 fitted on the earlier transitions | Forward squared error | Against one curve |
  |---|---|---|
  | once for both chambers | 1.320 | |
  | once per chamber | 1.359 | 0.038 worse (standard error 0.040) |
  | **the latest era's, since the 110th (shipped)** | **1.254** | 0.066 better (0.034) |
  | a time trend (reported only) | 1.251 | 0.069 better (0.089) |

  The latest era's curve predicts the next Congress better by about two
  standard errors and ships, but the decision is weakly settled, for
  four reasons, all in the script's output:
  - **Where the gain is.** It comes from the transitions predicted from the
    112th on; at the 111th the two curves predict alike
    (`gain_by_transition`: 0.0). Most of it is at the 112th and 114th (0.024
    each), when the one curve it beats was fitted on about two thirds and
    half pre-110 records (65% and 48% of its thin pairs,
    `training_before_split`). At the last three transitions, whose training
    data look most like today's, it is 0.009 with a standard error of 0.009
    (`last_three`).
  - **Members.** One member supplies 44% of it (`most_helped_share`). Left
    out of the comparison (fits unchanged), the one, two and three members
    it helps most leave it better by 0.037, 0.031 and 0.026 (standard
    errors 0.017, 0.016, 0.016); left out of the data and refitted, by
    0.180, 0.180 and 0.151 (0.077, 0.078, 0.072). The sign holds; the size
    doesn't.
  - **The split.** At every split from the 103rd to the 117th the era curve
    predicts better, but the rule would adopt it at 11 of these 15, not at
    the 108th, 109th, 115th or 116th (at the 118th the two curves can't
    differ: no later transition has thin pairs). The 110th is a convention
    (roughly the middle of 101–119), fixed before the forward comparisons,
    though era results at it had been reported before; it is kept at the
    110th on reruns, never re-centred.
  - **Recency or a break.** A window of the last six transitions
    (`FORWARD_WINDOW`, a convention), with no split, does as well (0.0775
    better, 0.0361), and against the era curve it is 0.011 better (0.012,
    `window_against_era`): these tests can't tell recency from a break at
    the split. The time trend predicts about as well over every transition.
    Its forward fits sit at the ends of their search grids through the
    112th, where it predicts worse than one curve; from the 113th, with
    every fit inside the grids, it beats one curve by 0.142 (standard error
    0.065) and the era curve by 0.100 (0.053) (`trend.inside_grids`, a range
    picked after seeing where the fits sit, and one that drops the stretch
    where it did worse overall, through the 112th). Over every predicted
    transition, the range the rule judges, it is 0.069 better than one curve
    (standard error 0.089), short of the rule's bar, so the rule would not
    choose it even as a candidate; it is reported, not chosen (that it was
    added after the other tests is a further reason, a stated choice). It
    suggests, without testing, a curve still slower than the shipped one.

  So the evidence supports recent thin records saying less than one curve
  over every Congress credits; how much less is not settled, and the
  shipped half point (46) and one curve's (36) each lie inside the other's
  interval (26–98 with the era structure held fixed; 21–77 for one curve,
  `half_weight_votes_pooled_interval_90`). The rule, and the
  one-standard-error bar it uses (a convention, with two candidates and no
  adjustment for that), were adopted in review, after a forward check had
  been run once and had already shown the era curve winning: the third
  rule in this change, after two that left one member out (below). A rerun
  with more recent pairs decides again.
- **Leave-one-member-out comparisons, reported.** Each thin member's
  pairs predicted from a fit (n0 and drift) made without that member, on
  all 95 thin pairs:

  | n0 fitted | Held-out squared error |
  |---|---|
  | once for both chambers | 1.336 |
  | once per chamber | 1.368 |
  | once per era (101–109, 110–119), each era's curve on its own members | 1.279 |
  | once per direction (which side of the pair is thin) | 1.362 |
  | once per attendance (attended arrivals and departures, the rest) | 1.327 |
  | once per era, plus a party-line term | 1.322 |

  A curve per chamber predicts no better (fitted apart, 25 votes for the
  Senate and 55 for the House). Direction and attendance can't be known
  for a sitting member's record; against one curve, direction is worse
  (0.025 above it, standard error 0.014) and attendance within the noise
  (0.009 below, 0.037). The direction split is which side of the pair is
  thin (the earlier, mostly members who arrived; the later, mostly members
  who left or were absent at the end of a Congress), and the attendance
  split is mostly the arrivals against the departures. Left out one member
  at a time, the era structure's gain over one curve is all in the earlier
  era (whose n0 runs to the search grid's lower limit, 1): judged on the
  latest era's members alone (`era_test`), the latest era's curve does no
  better than one curve, 0.0025 worse with a standard error of 0.0195 over
  67 members. That comparison lets one curve learn from the very
  Congresses it is judged on, which the forward test doesn't; the two
  disagree, and the forward test is the one that matches the use. Repeated
  at every split (`era_split_test`), a recent era's curve beats one curve
  at 4 of 16 splits (113th–115th, 117th) by 1.0 to 1.5 standard errors,
  each at the slowest curve the search grid allows (half point 98); that
  extreme rests on one member at the 113th to 115th (leaving one out, n0
  falls to 279–475), but without them the slower curve is still adopted at
  the 113th and 114th (half points 75.5 and 82.6) and no single member
  moves the 117th's (n0 stays 5,000 with each left out). A time trend (one
  line, log n0 linear in decades since the 110th, `trend_test`) beats the
  shipped grouping by 0.0975 (standard error 0.0769) over every member and
  0.0818 (0.0755) over the latest era's (0.036, 0.064, without its most
  influential member), with its half point by the 118th at the slowest
  curve the form allows (about 100 votes). Left one member out, the latest
  era does better at the 113th to 117th splits (the 116th barely) and in
  the trend, as in the forward test; at the 103rd to 112th it does
  slightly worse, and at the 118th worse by 0.14 (standard error 0.15, 10
  members). The early era's n0 sits at the grid's floor, fitted on only 4
  thin pairs under 50 votes (of its 24, `thin_pairs_by_era`), so "thin
  records were reliable before 2007" is not a finding: the contrast rests
  on a small early base.

| | Estimate | 90% interval (members resampled, 1,000 times, the era structure held fixed) |
|---|---|---|
| Votes at which a position counts half | **46** | 26–98 |
| n0 | 86 | 36 to the search grid's limit of 5,000 |
| Weight of a position with no count | **0.21** | 0.01–0.39 |

- **The curve is weakly determined.** The half point is n0 written
  another way, so it is no better determined; it is only bounded. The
  curve can't put it above 100 votes (as n0 grows, w(n) / w(200) falls
  to n / 200, which is 0.5 at 100), so the upper end, 98, is where the
  n0 search stops: the data allow anything up to the linear curve. The
  no-count weight is measured over every era (too few no-count pairs to
  split).
- **A party-line term was tested and is not used.** That would make n0 a
  line in the share of roll calls on which the parties' majorities split,
  since in a more party-line Congress each vote might say less about a
  member's place within their party. Added to the era structure it
  predicts held-out members worse (1.322 against 1.279, 0.043 worse with a
  standard error of 0.024), and its slope isn't stable: +2.75 here, and
  negative in an earlier run with the party switchers counted (on a
  narrower grid). An earlier draft of this change found a positive slope
  by forcing one drift on every Congress, which loaded the era differences
  in drift onto n0.
- **Positions with no count carry some information.** They are members
  Voteview has barely scaled (newly sworn in, or with very few scalable
  votes). Measured only where the member also has a career DW-NOMINATE
  position, they count 0.21 from 25 pairs, but a few members carry it:
  leaving out one member at a time moves it between 0.13 and 0.26. A
  no-count position with no career position at all (a member just sworn
  in, with nothing yet to anchor it) is not measured by these pairs, and
  it counts nothing, so the part sits at 50.
- **Each pair is predicted in its own direction.** A member whose thin
  record is the earlier one (an entrant) is predicted with the
  transition's forward drift, the slope of the later full position on the
  earlier. A member whose thin record is the later one (a leaver) is
  predicted with the reverse slope, of the earlier on the later.
- **Limits.** Centering on the party median rather than the seat's
  expectation understates the noise slightly, by the share of within-party
  position that seat lean explains (about 9.5% for House Republicans in
  the 119th, under 1% for Senate Republicans). The thin records here come
  from members who arrived or left mid-Congress or were absent for much of
  one. Every member's record early in a Congress is thin too, on a
  different agenda; whether those records behave like these is untested.

**Nine approaches replaced.** Each appeared in a draft of this change and
was replaced after review; the fourth, fifth and sixth were not shown to
help rather than shown not to, and a rerun with more pairs tests them
again. Every figure below except the career-gap proxy's and those quoted
from the current output (the best switch of 101 votes at 36%,
`switch_test.all`; one curve's half point of 36,
`half_weight_votes_pooled`) comes from that draft's own run and is not
reproduced by the current scripts.

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
the noise of the comparison. The one-standard-error rule now decides.

*Flank-rule switches chosen in sample.* The fifth draft switched the
flank rule at 53 votes, on pairs centered on the median of each party's
full records rather than as the rule centers, with noise and drift
assumed independent everywhere. The sixth switched at 92, the best
switch over all thin pairs, without testing it out of bag or on the
pairs shaped like the rule's case; there it can't be shown to help: too
few pairs have the rule's shape to measure one, and over all thin pairs
the best switch (101 votes) saves sides in only 36% of resamples
(below).

*Thin pairs restricted to arrivals and departures.* The seventh draft
kept only members absent the Congress before or after, taking that to
make every thin record a run of consecutive votes. It doesn't: many such
records are mostly absences (illness before a death or retirement, a
campaign for another office, a cabinet nomination), and the score's thin
records include absences too. The weight uses every thin pair; only the
flank rule's switch test is restricted, to attended records.

*Party switchers counted.* The eighth draft counted a member who
switched parties during a Congress as two thin records, one who left and
one who arrived. Four of its 99 thin pairs were such switches, and three
of the six pairs it took for the flank rule's case. Leaving them out
moved the one curve's half point from 50 votes to 36 and turned the era
structure from the worst left one member out (1.597 against one curve's
1.456, an earlier run) to the best. Four pairs moving it that far is
itself a sign of how little these data settle.

*Choosing by leaving one member out.* Later drafts chose the structure
by leaving one member out: first over every member, which picked the era
structure on a gain that was all in the earlier era; then, judging the
era curve only on the latest era's members, one curve (half point 36).
Both let a curve learn from the Congresses it is judged on. Predicting
forward, the use the weight is put to, the latest era's curve does
better (above).

**Shipped (v6.27).**
- The member's extremity is scaled by weight(n) before it is read against
  the saturation point. A position from 46 votes counts half, and one
  from 200 or more counts in full; n is the member's scaled roll calls
  this Congress (Voteview's `nominate_number_of_votes`).
  - A position with no reported count is weighted 0.21 when the member
    has a career DW-NOMINATE position, and read as no votes (50) when
    not.
  - One curve serves both chambers: the latest era's, fitted on the
    transitions since the 110th Congress. The split is a fixed Congress,
    so nothing in it follows the sitting Congress. A new Congress needs
    no rerun and no setting. Rerunning the calibration (its Congress
    range follows the clock, and a Congress still thin by the calendar is
    skipped) only adds the newest pairs, to the latest era.
  - A member Voteview lists twice in one Congress (a party switch during
    it) is read on the record since the switch, and only that one enters
    the seat fits. That is a choice, not a measurement: the score is
    about the current term, and the record since the switch is the one
    of who the member now is. The evidence is consistent with it but
    can't settle it (`switcher_test`). Over the 9 such member-Congresses
    (8 people) with a full record in the next Congress, the record since
    the switch is nearer that record (squared gap 0.039) than the longer
    record (0.083) or the vote-weighted mean of the two (0.060). Counting
    each person once, it beats the longer by 0.050 (standard error 0.032)
    and the mean by 0.030 (0.022); in 4 of the 9 the record since the
    switch is also the longer, and of the other 5 it is nearer in 4.
    Consequences of the choice:
    - Just after a switch the record since is short, so it counts little
      and the part sits near 50; the longer record from before the
      switch isn't read.
    - Until Voteview places the record since the switch, the member has
      no position (50), never the old one.
    - The record since the switch is the id the last Congress's export
      didn't have, or, when both ids are new (a switch in a member's
      first Congress), the one whose first roll call comes last in the
      vote export (two ids with no roll call yet settle nothing).
  - Two related choices, stated as such (nothing measured them):
    - Once the new Congress's section is in, the flank rule never reads
      a last-Congress record cast in another party: a member who
      switched during the new Congress, or whose party differs between
      the two sections (a switch between Congresses gives a new id in
      each; a section written before this change records no parties, so
      that check starts with the next Congress's section). With no
      usable position this Congress such a member has none: their
      breaks are classified on the other defectors' positions, and
      count when no defector has one.
    - If the exports needed to tell a switcher's records apart can't be
      read or don't settle it (two new ids with no roll call yet), the
      ingest keeps the previous section, as it does for any export that
      fails its gates, rather than guess, and raises an ops alert (kept
      data stops being current at the next Congress).
- The seat fits are taken over every member (a stated choice): carried
  back by the drift, a full position differs from its thin one by 0.009 on
  average (standard error 0.019; 95 pairs, `thin_offset`; full pairs give
  0.006 by the same measure, `full_baseline`), so no constant offset is
  detected, though one up to 0.046 toward the flank (the 95% upper bound,
  `upper_95`) can't be ruled out, and these pairs can't tell a
  proportional distortion from noise.
- The saturation scale is the 90th percentile of full records'
  extremities. A thin record's noise would widen a scale taken over
  everyone, and a scale taken over weighted extremities would cancel the
  weights whenever every record is equally thin.
  - Early in a Congress, with fewer than 40 full records (a convention,
    the floor the ingest already used), the chamber's last scale is
    carried. It describes the chamber's seats, not any member's record.
  - So the weights do pull thin positions toward 50: with every record at 5
    votes, a position at saturation scores about 46 instead of 0 (computed
    from the shipped curve, n0 86).
- Voteview's placeholders are dropped. A member a current section has no
  position for (those, or anyone Voteview hasn't placed yet) counts
  nothing, so the part sits at 50, instead of having the component
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
  - The Senate's roll calls name a voter only by surname and state, and the
    roster lists sitting senators only, so a senator who left during the
    Congress (and, on a filtered run, every senator not being scored) had no
    position: their votes were left out of both the party's and the
    defectors' means. The rule now reads the whole chamber, adding a stored
    senator who has left by the one surname among their state's voters that
    is a whole word of their stored name, and only when their first name
    matches exactly one voter under that surname whom no sitting senator's
    first name matches (otherwise left out, as before). The House's roll
    calls carry the member's id and need nothing added. In either chamber, a
    member no longer stored counts in their party's center by the party the
    section records.
  - Weighting means cannot help a lone defector, whose side is its own
    position's sign against the party's. So once the new Congress's
    section is in, the rule reads a member's last-Congress full record
    (kept beside the new section for this rule only, never scored) until
    their new record is full (200 votes). Each Congress's positions are
    read from their own party's weighted mean, so a party-wide shift
    between the two can't move a member against a party read from the
    other. Only the Congress just before is kept, and only a full last
    record replaces a new one: that is the case the pairs measure. Two
    stated choices, not measured:
    - A member whose new position counts for nothing yet (no votes), or
      who has none, is read on their last position however short; the
      alternative, classifying the break on the other defectors alone,
      wasn't compared.
    - A position a section records under the other major party (a
      switch since) is left out of that party's mean and never read for
      the member, in any section: before the new Congress's section is
      in too, when everyone is read on the last one.
  - The pairs, centered as the rule centers, are the evidence. A full
    record is on the same side of its party as the next Congress's full
    record 86% of the time. Thin records, against their pair's full
    record:

    | Votes | Pairs | Same side |
    |---|---|---|
    | 1–25 | 19 | 68% |
    | 26–50 | 24 | 54% |
    | 51–100 | 20 | 70% |
    | 101–150 | 20 | 85% |
    | 151–199 | 12 | 92% |

    Those pairs compare a thin record with the other Congress's full
    record; the rule compares within one Congress, so one Congress's drift
    has to be taken out. Both flip a side mostly near the party's center
    (full records agree 62% within 0.02 of it, over 99% beyond 0.2), so
    the model (`switch_model`) works within three strata of distance from
    the center (under 0.05, 0.05–0.1, beyond), noise and drift independent
    within each, agreement a logistic in log votes with an intercept per
    stratum, implied rates clipped to [0, 1].
  - Whether to switch before a full record is tested out of bag
    (`switch_test`): a switch is chosen, as the one misplacing the fewest
    sides over counts 1–199, on members resampled 1,000 times, and judged
    by the same model fitted to the members that resample left out,
    against keeping the last full record. A switch would be adopted only
    if it saved sides in at least 95% of resamples.
  - The test belongs on pairs shaped like the rule's case: a full record,
    then a member's first, attended votes of the next Congress, from a
    member who left during it (absent the Congress after) and missed no
    more of the roll calls in their span than nine in ten of that
    Congress's full records do (a convention; a roll call with no row
    counts as missed, as for a Speaker who doesn't vote). Most leavers
    missed more of their span than the convention allows. Only 3 pairs
    qualify, none with over 100 votes: too few to fit the model at all.
    Counting every member absent the Congress after, attended or not (33
    pairs, 9 with over 100 votes; this includes members who served the
    whole Congress with many absences and then retired or lost), no
    switch short of a full record does better even in sample, and one
    saves sides in 3% of resamples out of bag; over all thin pairs, the
    best switch is 101 votes and 36%. None comes near the bar, so the
    attendance convention doesn't decide it.
  - So the switch rests on its conventions: keeping the last full record
    until the new one is full is the rule as designed, the one count at
    which the new record needs no model to be trusted, and nothing in
    these data is shaped closely enough like the rule's case to replace
    it. The 95% bar is a convention too, and strict: any resample whose
    own best is a full record saves exactly nothing. The stakes are small;
    a rerun with more attended leavers could find a switch that clears the
    bar.
  - At very low counts the model's logistic in log votes extrapolates
    below a coin flip; flooring it at one changes nothing measurable.
    Early in a real Congress the party's center is itself measured on
    thin records, so the pairs, centered on a whole Congress, flatter a
    new record; that points toward keeping the last full record too. This
    first applies in the 120th Congress: a 119th section written before
    v6.27 keeps no earlier positions.
  - On the 119th Congress's party-unity roll calls (majorities opposed,
    Voteview's votes) the weighting alone reclassifies none of 2,351
    Senate breaks and 2 of 6,055 House breaks.

**Effect on the 119th Congress** (Voteview exports of 2026-10-03, through
the pipeline's own build with Cook PVI seat lean). This compares v6.26,
which read placeholders as positions and weighted nothing, with v6.27:

| | Senate | House |
|---|---|---|
| Saturation | 0.258 → 0.268 | 0.224 → 0.221 |
| Mean absolute change in the component | 1.69 | 0.58 |
| Mean absolute change in Constituent Alignment, through the position part | 0.51 | 0.17 |

These are the change through the position part (30% of Constituent
Alignment). The vote part's change is not in them: weighting the flank
rule's means reclassifies 0 of 2,351 Senate and 2 of 6,055 House breaks
(above), and reading departed senators' positions is not measured here.

- Full records move only through the scale and the refit seat lines. The
  Senate's mean change comes from dropping its placeholder, which moves
  the Republican slope from +0.0033 to +0.0018, and from its scale now
  being measured on full records.
- Records under 200 votes, with no count or with no position:
  four in the Senate and eleven in the House.
  - Two recently sworn-in representatives with no count and no career
    position move from 23.3 and 31.1 to 50.
  - Representatives with 77 and 72 votes move from 93.2 to 79.7 and from
    75.3 to 66.7.
  - A representative far beyond saturation (135 votes) stays at its end;
    the others with 116–171 votes move about 1 to 4 points.
  - Senators with 53 and 187 votes move by under 1.
  - A representative who left their party during the Congress is read on
    their record since the switch, as before; with no major party there,
    the score reads it against the caucus the pipeline infers from their
    votes, which this comparison doesn't have, so they are left out of it.
  - Members scored from 1 to 39 votes or a placeholder, all since departed,
    move by up to 50 points.

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
- **The thin-record weight rests on 95 thin pairs.** Few members have a
  thin record next to a full one, so the half point has a wide interval
  (26–98 votes, the upper end where the n0 search stops), and the
  structure rests on a forward test of about two standard errors that
  leaving one member out doesn't show; a rerun with new pairs could return
  to one curve. Whether recent Congresses follow a slower curve still is
  open: left one member out, later splits lean that way, and the time
  trend, from the 113th where its fits are inside the search grids (a
  range picked after seeing the fits), predicts better than the era curve
  by 0.100 (standard error 0.053); over every transition it predicts about
  as well as the era curve (1.251 against 1.254), and the forward sweep
  adopts the era curve at neither the 115th nor the 116th. The thin
  records mix arrivals, departures and long absences, as the score's do;
  whether everyone's early records in a Congress behave like these is
  untested. The no-count weight rests on fewer still (25 pairs), and the
  flank rule's switch can't be measured at all: only 3 pairs have the
  rule's shape. Reading a party switcher on their record since the switch
  rests on 8 people, and beats the longer record by about one and a half
  standard errors. Rerunning the calibration adds each Congress's new
  pairs, and decides the structure and the switch again.
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
