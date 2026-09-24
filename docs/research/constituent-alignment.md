# Constituent Alignment: what the evidence supports

This note records how Constituent Alignment's v6.13 design was chosen. Before
v6.13, several of its rules rested on arguments that were never tested: the
claim that party loyalty is "unreadable", and discounts for safe seats. Each
rule below was kept or changed based on how well it predicts real election
results.

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
90th-percentile deviation.

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
- **The Senate is underpowered.** The 2006 and 2010 Senate incumbents (N=47)
  give a loyal-side slope of 3.07 (t=0.7), in the same direction but far from
  significant. The Senate uses the same design by extension, not on Senate
  evidence.
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
  break rates on Civitas's own key and recent roll calls, weighted by party
  alignment, not on CQ party-unity votes. The expectation is measured on that
  same statistic every run, so scores stay internally consistent, but the
  magnitudes above do not carry over one-for-one.
