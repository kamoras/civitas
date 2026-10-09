# Justice scorecard: what each measure actually measured

Three studies. The first (v6.13) cut the Supreme Court scorecard from four
measures to two; the second (justice v2, September 2026) found those two
ranked justices by distance from the Court's center and replaced them with
loyalty to the appointing president; the third (justice v3, October 2026)
found that measure could not be told apart from career timing for an
individual justice, and retired justice scores.

The first study's numbers were printed by `backend/scripts/research_justice_scores.py`,
which ran the repository's then `analyze_justice_votes` over real and
simulated votes. Both are gone with justice v2; the script is in git history
at commit 7df407c8.

**Data.**
- **Real votes:** the Rehnquist Court's 485 formally decided, non-unanimous
  cases from the 1994–2004 terms. They come from Spaeth's Supreme Court
  Database, as packaged in the R package MCMCpack, and give 99
  justice-terms.
- **Position:** each justice's one-dimensional position per term is the
  first principal component of that term's votes. From it, each justice's
  distance from the term's median justice.
- **Simulation:** a simulated 6–3 Court (one-dimensional spatial voting,
  appointing party assigned at random), run once with party carrying no
  information and once with symmetric partisanship. An unbiased measure
  should score both blocs alike.

## Judicial Restraint measured distance from the Court's median

The score was a hand-set curve over dissent frequency. It peaked at 8–15%
dissent and fell steeply above 25%.

- **Dissent tracks distance from the median.** Across justice-terms, dissent
  rate correlates with distance from the term's median justice at
  ρ = 0.83. The median justice dissented in 17.7% of split cases, against
  33.7% for everyone else. That is structural: in a one-dimensional Court the
  median justice is in every majority (Martin, Quinn & Epstein 2005). So the
  score measured where a justice sits relative to the Court's current
  membership. In the literature, judicial restraint means deference to the
  elected branches, which dissent frequency does not measure.
- **It penalized the smaller bloc.** Under symmetric partisanship on a 6–3
  Court, the curve scored the 3-member bloc **21 points lower** than the
  6-member bloc, purely because it is outvoted more often. With party
  carrying no information the gap was under a point. On today's Court this
  is a structural penalty on one party's appointees.
- **Its calibration didn't check out.** The authored-dissent penalty cited
  Haynie (1992) for "authored dissents above 8% signal vocal ideology". That
  paper is about Chief Justices' leadership and the Court's historical
  consensus norm, and sets no such threshold.

**Removed.** Dissent rate is still shown as a plain statistic.

## Bipartisan Agreement repeated Independence

| Spearman across justice-terms | Consistency | Independence | Bipartisan | Dissent | Distance from median |
|---|---|---|---|---|---|
| Consistency | 1.00 | 0.19 | 0.04 | −0.67 | −0.70 |
| Independence | 0.19 | 1.00 | **0.86** | −0.10 | 0.04 |
| Bipartisan | 0.04 | **0.86** | 1.00 | −0.07 | 0.24 |

Bipartisan Agreement (agreement with the other party's appointees) and
Independence (siding with the other bloc against your own in split
decisions) correlate at 0.86. Bipartisan Agreement also averaged in unanimous
cases, which say nothing about partisanship, since everyone agrees.
**Folded into Independence**, keeping its weight: Independence 0.30 + 0.15.

## What remains

- **Weights:** Consistency 0.35/0.80 and Independence 0.45/0.80,
  renormalized with no new weighting judgment.
- **Distinct measures:** the two correlate at only 0.19, so they capture
  different things.
- **No bloc-size bias:** both stayed within 2 points between the 6- and
  3-member blocs in the simulation, with and without informative party.

## Limits

- The real-data test is one natural court, from 1994–2004, using
  non-unanimous cases only. The simulation covers today's 6–3 split.
- Both remaining measures define blocs by appointing president's party. On
  the Rehnquist Court that scored Stevens and Souter (Republican appointees
  who voted with the liberal wing) as highly independent. That is what the
  measures say they measure, but "unpredictable from appointing party" is a
  narrower claim than "impartial".

## What the two remaining measures measured (September 2026)

Consistency and Independence both define blocs by the appointing
president's party. On today's Court, party and ideology coincide: every
Republican appointee sits right of every Democratic appointee (Devins &
Baum 2019). So "agrees with the other party's appointees" is "sits near the
Court's center," the same thing Judicial Restraint was removed for measuring
(it tracked distance from the median at ρ = 0.83). Against Martin-Quinn
positions for the 2024 term:

| Justice | Distance from the median | Consistency | Independence | Overall |
|---|---|---|---|---|
| Sotomayor | 4.71 | 42.4 | 11.9 | 25 |
| Jackson | 3.32 | 48.8 | 14.4 | 29 |
| Thomas | 2.55 | 43.2 | 8.7 | 24 |
| Kagan | 2.36 | 51.0 | 25.7 | 37 |
| Alito | 1.98 | 42.9 | 10.2 | 25 |
| Gorsuch | 0.57 | 59.8 | 28.5 | 42 |
| Roberts | 0.19 | 71.7 | 39.9 | 54 |
| Kavanaugh | 0.03 | 70.5 | 38.5 | 53 |
| Barrett | 0.00 | 65.6 | 33.6 | 48 |

Spearman with distance from the median: Consistency −0.82, Independence
−0.75, the overall score −0.75. The scores ranked justices by how close they
sit to whoever else is on the Court, and a justice who votes a consistent
legal philosophy (the attitudinal and legal models disagree about how much
of that is ideology; Segal & Spaeth 2002, Bailey & Maltzman 2011) reads as
"partisan" wherever that philosophy lines up with the appointing party.
"Consistency" also meant the opposite of its name in the literature, where
it is a justice's position staying put over time (Epstein, Martin, Quinn &
Segal 2007), and "Martin-Quinn-style" was cited for measures that used no
Martin-Quinn estimate.

## Loyalty to the appointing president

Epstein & Posner (2016) measure independence from the appointer directly:
does a justice vote for the government more often while the president who
appointed them is in office than under other presidents? It compares a
justice with themselves, so a justice's ideology, and how often they side
with any government, cancel out. Every number below is printed by
[`backend/scripts/research_justice_loyalty.py`](../../backend/scripts/research_justice_loyalty.py).

**Data.** Their data set: every vote in an orally argued, signed decision of
1937-2014 in which the president's position was at stake (the United States,
an agency or an executive officer a party, the Solicitor General's position
the tiebreaker), 70,633 votes. Their Table 4 reproduces exactly from it
(Alito 58.43% of 166 votes for other presidents, 75.00% of 56 for George W.
Bush; Roberts 50.89% and 64.52%; Thomas 59.62% and 57.50%).

**Extended through 2025** with the Supreme Court Database (2026 Release 01),
since four of today's justices were appointed after their data ends. The
Database names each case's lead parties, not the Solicitor General's
position, so a case is a president case when exactly one lead party is the
federal government. Which party codes are the government was learned from
the cases Epstein & Posner coded, 1946-2001 (codes 1, 27 and the federal
block; every learned code fell in 300-414, and the rule takes the block,
300-420), and checked on 2002-2014, held out: precision 1.00 and
recall 0.76 as petitioner, 0.97 and 0.94 as respondent (the misses are cases
where the government is not a lead party, or the Solicitor General sided
against the lead party it represents). On the 23,470 votes both data sets
hold, the two codings agree on the vote for the president 98.95% of the time.

**Pooled, the effect holds.** 31,650 votes, 1937-2025, justice fixed effects,
petitioner or respondent controlled (the Court reverses more often than it
affirms): a justice sides with the government **6.6 points** more often
while the appointing president is in office (t = 3.1, clustered by justice).
The placebo and career-curve studies below show that much of it is career timing, not loyalty.

**Per justice, it is noisy.** Each justice's own estimate, shrunk toward the
mean by its noise (DerSimonian-Laird random effects over 42 justices: mean
+5.1 points, between-justice sd 8.5):

| Justice | Loyalty (points) | Votes, appointing president / others |
|---|---|---|
| Alito | +14.3 ± 5.0 | 56 / 399 |
| Roberts | +11.8 ± 5.1 | 62 / 404 |
| Sotomayor | +7.8 ± 4.3 | 168 / 208 |
| Kagan | +6.0 ± 4.6 | 129 / 205 |
| Barrett | +4.7 ± 6.2 | 43 / 91 |
| Jackson | +3.7 ± 6.5 | 50 / 43 |
| Gorsuch | +2.8 ± 5.1 | 103 / 92 |
| Thomas | +1.4 ± 5.7 | 40 / 859 |
| Kavanaugh | −0.7 ± 5.6 | 78 / 92 |

Only Alito and Roberts are clearly above zero. Split-half (odd against even
terms, 35 justices), with each justice counted by the precision of their two
halves, the reliability for the full record is 0.76. Unweighted it is 0.33,
because one justice with a few dozen votes a side, whose halves read −21 and
+35 points, decides an unweighted correlation of 35 alone (0.67 without
them). Either way the uncertainty is shown with every estimate rather than
left out.

**Not distance from the median.** Across the 40 justices with Martin-Quinn
positions, loyalty's rank correlation with a justice's mean distance from the
Court's median is 0.33 (p = 0.04), where the old measures ran −0.75 and
−0.82: weak, and of the opposite sign. Roberts, nearest the median, and Alito, among the furthest, show the
most loyalty; Thomas, far out, and Kavanaugh, at the median, show almost
none.

**Not party alignment either (re-measured 2026-10-09).** "Under other
presidents" mixes presidents of the appointer's party with presidents of the
other party. A justice who simply agrees with the legal positions of
same-party administrations would then read as loyal. Epstein & Posner raise
this themselves. After their main test (appointer against every other
president, their Section 4.1, the score's design), they run what they call a
"tougher test" (Section 4.2): appointer against other presidents of the same
party only. It can be run for 29 of their 39 justices, since the rest never
served under a second president of their appointer's party. The edge
survives pooled, in logits clustered on justice with petitioner, salience,
ideological distance, executive-branch experience and approval controlled
(Table 10: 0.261, t = 3.4, without the controls; 0.173, t = 2.3, with them).
Democratic appointees drive it; for Republican appointees it is positive and
not significant. Their footnote 12 adds that FDR's appointees favored FDR over
Truman, his same-party successor. An earlier, cruder study (Ducat & Dudley
1989, all federal judges pooled, raw rates) found the same ordering:
appointing president, then other presidents of the same party, then the
other party. Justices' ideological agreement with an administration is
measured separately in the literature (the Solicitor General's advantage is
largest with justices ideologically close to the president: Bailey, Kamoie &
Maltzman 2005). That is why Epstein & Posner control for ideological
distance rather than party. Their later paper (2018, *U. Penn. L. Rev.* 166)
shows the government's win rate peaking under Reagan and falling since, which
matters below.

The version of this check first published here (2026-10-01) misread Epstein
& Posner's `same_partyExcludeInOffice`. It is not a same-party indicator: it
is 1 under the appointer, 0 under other same-party presidents, and missing
under the other party (the sample of their Tables 8-10). Used as a same-party
indicator, it dropped every other-party vote before 2015 and labelled
same-party votes as other-party, so its numbers (+8.4, −1.2, +9.7 points)
were wrong. The research script now derives the party match from the
presidents' own parties throughout. These are the corrected numbers.

Four specifications, each justice fitted, shrunk and scored with the
pipeline's own code (`justice_loyalty.shrink`, `MIN_VOTES_EACH_SIDE` = 10).
The data is Epstein & Posner's votes through 2014 and the Supreme Court
Database after, 31,650 votes: 8,227 under the appointer, 11,577 under other
presidents of the appointer's party and 11,846 under the other party.

- **A**, the score: appointer against every other president.
- **B**: appointer against other presidents of the appointer's party only.
- **C**: every vote, with the appointer and same-party indicators side by
  side, so the appointer's edge is net of the party's.
- **D**: the party effect itself, same-party against other-party presidents,
  leaving out the appointer's own terms.

| | Justices measurable | Of the current nine | Mean (points) | Between-justice sd (tau) | Spearman with A |
|---|---|---|---|---|---|
| A | 42 | 9 | +5.1 | 8.5 | 1 |
| B | 33 | 5 | +6.2 | 6.9 | 0.96 (33) |
| C | 33 | 5 | +6.3 | 6.8 | 0.96 (33) |
| D | 29 | 5 | +1.3 | 5.0 | −0.06 (29) |

Pooled, with justice fixed effects and clustered by justice, against the
other party's presidents:

| Under | Points more often with the government | t |
|---|---|---|
| The appointing president | +7.1 | 2.6 |
| Other presidents of the appointer's party | +1.1 | 0.8 |

The appointer's edge over other same-party presidents is +6.0 points
(t = 3.5). For comparison, Epstein & Posner's Table 9 has 66.2% against
61.2% in raw rates.

The current Court, shrunk effect in points and score, with votes in/out in
brackets. Under D the sides are same-party and other-party votes.

| Justice | A | B | C | D (party effect) |
|---|---|---|---|---|
| Alito | +14.3, 16 (56/399) | +12.9, 7 (56/116) | +12.8, 6 (56/116) | −0.1 (116/283) |
| Roberts | +11.8, 31 (62/404) | +13.5, 3 (62/117) | +13.5, 1 (62/117) | −3.6 (117/287) |
| Sotomayor | +7.8, 54 (168/208) | +6.7, 51 (168/92) | +6.8, 50 (168/92) | +1.9 (92/116) |
| Kagan | +6.0, 65 (129/205) | +5.0, 64 (129/92) | +5.1, 63 (129/92) | +2.5 (92/113) |
| Barrett | +4.7, 73 (43/91) | not measurable (43/0) | not measurable | not measurable |
| Jackson | +3.7, 79 (50/43) | not measurable (50/0) | not measurable | not measurable |
| Gorsuch | +2.8, 84 (103/92) | not measurable (103/0) | not measurable | not measurable |
| Thomas | +1.4, 92 (40/859) | +1.7, 88 (40/313) | +2.1, 84 (40/313) | +2.7 (313/546) |
| Kavanaugh | −0.7, 96 (78/92) | not measurable (78/0) | not measurable | not measurable |

The full 42-justice table is printed by the research script (section 5).

**The same-party effect is not real on average, and it accounts for none of
A's loyalty.** Justices side with other administrations of their appointer's
party about one point more often than with the other party's (+1.1, t = 0.8). So
mixing both into A's comparison group shades A down by about half a point,
not up. B and C order justices almost exactly as A does (Spearman 0.96
both). Their scores fall mainly because their tau is smaller (6.9 and 6.8
against 8.5), which stretches the same effects over a shorter scale. The
material moves are scale, not reordering. Roberts goes from 31 to 3 and
Alito from 16 to 7, because against same-party presidents alone their edge
is larger than A's (raw +21.7 and +20.6, against +15.5 and +19.2). Among
historical justices the same pattern moves Breyer (24 to 0), Souter (40 to 8)
and Robert Jackson (28 to 8); Warren goes the other way (70 to 94, because
only 30 of his votes were under another Republican). D does vary between
justices (tau 5.0): Douglas +11.8, Black +7.8 and Burger +6.3 favored their
party's other administrations, while Brennan (−5.2), Souter (−4.5) and Reed
(−4.4) disfavored them. But it averages about zero. No sitting justice's D
is distinguishable from zero (the largest is Roberts, raw −8.8 ± 5.2, in the
direction opposite to partisanship).

**Decision: no change to the score.**
- B and C lose four of the nine sitting justices to the 10-vote rule
  (Gorsuch, Kavanaugh, Barrett and Jackson: no other president of their
  appointer's party has served since they joined) and five historical ones.
- They buy no cleaner estimate in return, because the confound they remove
  measures at about zero.
- D is not shown as context. It averages nothing, it is undefined for four
  of the nine, and for the five where it exists none is distinguishable from
  zero.

If a sitting justice's appointer's party returns to office with another
president, the research script will show whether that changes.

**Voting against one's own side is not a fairness measure.** The intuition
is that a principled justice should sometimes vote against their usual side.
Measured on divided decisions in the Supreme Court Database (each justice's
own vote direction, against the side of the Court's median their Martin-Quinn
position puts them on), the rate tracks distance from the median at Spearman
−0.77 (p = 2e-7, 32 justices with 300+ divided votes): the justice nearest
the median votes against their side about twice as often as those furthest out. It would
rank justices by how centrist they are, the artifact that removed
Consistency and Independence, so Martin-Quinn positions stay context, not
score. A justice near the median is pivotal in close cases by construction,
so "voting both ways" there is where the median is, not evidence of
impartiality.

**Loyalty or era (2026-10-09): tested, and the score is not changed.** The
appointer's time in office is always the start of a justice's career. The
government's share of votes has fallen: 0.66 in the 1980s, 0.63 in the
1990s, 0.57 in the 2000s, 0.47 in the 2010s and 0.49 in the 2020s (Epstein &
Posner 2018 report the same decline in win rates). So comparing a justice's
early years with their later ones could partly measure that decline. Every
number here is printed by section 7 of the research script.

*Holding the era fixed.* Pooled, with justice fixed effects, clustered by
justice:

| Specification | Points | t | Votes |
|---|---|---|---|
| A as scored | +6.6 | 3.1 | 31,650 |
| + a fixed effect per term | +0.8 | 0.6 | 31,650 |
| 1937-1952, + term fixed effects | −9.2 | −5.4 | 7,556 |
| 1953-1980, + term fixed effects | +4.1 | 2.7 | 11,825 |
| 1981-2025, + term fixed effects | +4.8 | 3.7 | 12,269 |
| E: the justice's vote minus every colleague's on the same case | +4.2 | 2.4 | 31,590 |
| E-excl: minus the colleagues the sitting president did not appoint | +4.9 | 3.3 | 28,531 |

The near-zero pooled term-fixed-effects estimate comes entirely from
1937-1952, when nearly every justice was an FDR or Truman appointee.

E-excl is E with one fix. When a president has several appointees on the
Court, plain E compares each of them with the others, so a loyalty they share
cancels out. E-excl compares a justice only with colleagues the sitting
president did not appoint. Where no such colleague voted, the vote has no
baseline: 3,119 votes, all from FDR's terms, because Epstein & Posner's data
starts with FDR's appointees.

*Per justice*, fitted, shrunk and scored as the pipeline does:

| | Justices | Current | Mean | tau | Spearman with A | Split-half reliability |
|---|---|---|---|---|---|---|
| A | 42 | 9 | +5.1 | 8.5 | 1 | 0.76 (unweighted 0.33; 35 justices) |
| E | 42 | 9 | +3.7 | 7.5 | 0.78 | 0.76 (−0.03; 35) |
| E-excl | 34 | 9 | +4.9 | 5.9 | 0.68 | 0.57 (−0.17; 27) |

Reliability is the precision-weighted split-half (section 4). E-excl loses
eight FDR-era justices and is less reliable than A. DerSimonian-Laird
compensates by shrinking harder: tau falls to 5.9, and the weight on a
sitting justice's own estimate, tau² / (tau² + se²), runs from 0.28
(Jackson) to 0.68 (Kagan). The current Court, shrunk effect in points and
score:

| Justice | A | E | E-excl (weight on own estimate) |
|---|---|---|---|
| Alito | +14.3, 16 | +9.8, 35 | +10.1, 15 (0.56) |
| Roberts | +11.8, 31 | +8.4, 44 | +9.8, 18 (0.61) |
| Sotomayor | +7.8, 54 | +5.2, 65 | +5.7, 52 (0.67) |
| Kagan | +6.0, 65 | +3.6, 76 | +4.4, 63 (0.68) |
| Barrett | +4.7, 73 | +3.7, 76 | +3.4, 72 (0.50) |
| Jackson | +3.7, 79 | +4.1, 73 | +5.1, 57 (0.28) |
| Gorsuch | +2.8, 84 | +5.5, 64 | +6.1, 49 (0.53) |
| Thomas | +1.4, 92 | −4.7, 69 | −2.5, 79 (0.51) |
| Kavanaugh | −0.7, 96 | −0.4, 97 | +0.1, 99 (0.61) |

*The gate: a placebo.* For each justice, the real appointer votes are dropped.
A fake "appointer in office" window of the same number of terms is then
placed k terms into the rest of the career, and each specification is
refitted. A measure free of the career-timing confound should find nothing
at every k. Pooled effect in points (t):

| k | Justices | A | E | E-excl |
|---|---|---|---|---|
| 0 | 31 | +4.1 (2.5) | +2.8 (1.6) | +2.9 (1.5) |
| 1 | 30 | +2.7 (1.6) | +1.6 (0.8) | +2.0 (1.0) |
| 2 | 27 | +0.9 (0.5) | −0.2 (−0.1) | +0.5 (0.3) |
| 3 | 26 | −0.5 (−0.3) | −0.8 (−0.5) | +0.0 (0.0) |
| 5 | 24 | −2.5 (−1.5) | −1.8 (−1.0) | −1.3 (−0.8) |
| 8 | 22 | −2.4 (−1.6) | −1.9 (−1.3) | −2.1 (−1.1) |
| 11 | 17 | −5.6 (−4.0) | −2.8 (−1.9) | −2.8 (−1.3) |
| 14 | 12 | −1.1 (−0.9) | −2.0 (−1.8) | −4.0 (−3.2) |
| 17 | 10 | +2.5 (1.5) | +0.8 (0.6) | −0.4 (−0.3) |
| 20 | 8 | +1.9 (0.9) | −0.2 (−0.1) | −1.3 (−0.7) |

Over all 25 shifts (k = 0 to 24, printed in full by the script):

| | Mean | sd | Shifts with \|t\| > 1.96 | Real effect |
|---|---|---|---|---|
| A | −1.2 | 2.8 | 20% | +6.6 |
| E | −1.4 | 1.8 | 12% | +4.2 |
| E-excl | −1.5 | 1.9 | 16% | +4.9 |

**E fails the gate.** The confound is not a smooth trend over the decades,
which a same-case baseline would cancel. A's placebo is not positive across
shifts (mean −1.2). It is positive only in the window right after the
appointer's, k = 0 and 1: the same position the real window holds against
the rest of the career. There, E-excl's placebo is +2.9 points, about
three quarters of A's +4.1 and three fifths of its own real effect. The
same-case comparison removes under a third of the early-window effect, at
lower reliability (0.57 against 0.76) and eight fewer justices. It does not measure loyalty more
cleanly, so it is not shipped as justice v3.

**What the placebo says about A itself.** A placebo window in the real
window's position, k = 0, already shows +4.1 points against A's real +6.6.
How often a justice sides with the government seems to fall over a career,
whoever is president. So part of what A reads as loyalty is probably a
justice's early years, not the appointer. On this evidence about +2.5
points of the pooled +6.6 remain (A's real effect minus its k = 0 placebo;
+2.0 for E-excl). Epstein & Posner's footnote 7 found no acclimation effect
within appointers' terms. They did not test the placebo above.

This finding bears on the score as it stands. A replacement has to pass
this same placebo before it is shipped; the career-curve study below tests the next candidate.

**Limits.**
- Epstein & Posner's hand coding follows the Solicitor General; after 2014
  the lead parties stand in, which misses about a quarter of the government's
  petitions (above).
- An appointing president's time in office can be short or long ago: Thomas
  has 40 votes under the first President Bush, Roberts and Alito 60 under the
  second, 20 years back. The estimate is for the career, as in the paper.
- The effect is a comparison of rates, not a reading of any one vote. A
  justice can vote for the appointing president's government for reasons
  that have nothing to do with loyalty; what the measure shows is a pattern
  across hundreds of votes.

## A career curve (candidate F, 2026-10-09): fails the placebo, not shipped

Every number here is printed by section 8 of the research script.

**The idea.** If votes for the government drift with years on the Court for
everyone, model that drift directly. Fit one career curve, f(years on the
Court), pooled across justices, alongside the appointer indicator and the
petitioner control, with justice fixed effects. Then fit each justice with
the curve held at its pooled shape, and shrink with DerSimonian-Laird as the
score does.

**What the literature expects.** It gives no expected direction for
deference to the government, only that early careers are unstable and that
justices drift.
- Hagle (1993, *AJPS*) found "freshman" instability in most justices' first
  term relative to the rest of their careers.
- Epstein, Martin, Quinn & Segal (2007, *Northwestern U. L. Rev.* 101)
  found that 22 of the 26 justices who served ten or more years since 1937
  drifted ideologically, most within a few terms of joining, in either
  direction.
- Epstein & Posner's own footnote 7 compared first, second and third years
  under the appointer and found no acclimation effect there.

So a pooled curve's shape and size have to come from the data.

**Identification.** The appointer's window is always at the start of a
career. What separates the appointer from tenure is how long the window
lasts:
- 2 terms for 8 justices, 3 for 8, 4 for 8, 5 for 4, 6 for 1, 7 for 7 and
  8 for 7;
- 7 of 43 windows start after the first term (a second appointment as Chief
  Justice, a return after non-consecutive terms, or a commission in the
  middle of a term).

At a given year of tenure, some justices are still under their appointer
and others are not. Votes under the appointer / under others by year on the
Court: year 0, 1,083 / 18; 1, 1,724 / 194; 3, 1,181 / 716; 5, 731 / 932;
7, 198 / 1,316; 8, 27 / 1,370. Years 3 to 7 carry the comparison.

**Shape, by 10-fold cross-validation by case.** Every tenure term beats
none: out-of-sample error is 0.00054 lower, about 4 standard errors. Among
the shapes (linear, quadratic, cubic, natural splines with 3, 4 and 6
degrees of freedom, a dummy per year), none is distinguishable from the
best. The one-standard-error rule picks the simplest, linear: −0.38 points
a year, about −11.5 over 30 years. A 3-df spline gives nearly the same curve
(−11.4 at 30 years).

**F's estimate.**

| | Pooled appointer effect (t) | Per-justice mean | tau | Spearman with A | Split-half (weighted) |
|---|---|---|---|---|---|
| A | +6.6 (3.1) | +5.1 | 8.5 | 1 | 0.76 |
| F, linear (chosen) | +2.7 (1.7) | +1.8 | 7.6 | 0.97 | 0.72 |
| F, spline df3 | +3.2 (2.2) | +2.3 | 7.6 | 0.98 | 0.72 |

The current Court, shrunk effect and score, A then F (linear):

| Justice | A | F |
|---|---|---|
| Alito | +14.3, 16 | +10.0 ± 4.8, 34 |
| Roberts | +11.8, 31 | +7.6 ± 4.9, 50 |
| Sotomayor | +7.8, 54 | +4.3 ± 4.2, 72 |
| Kagan | +6.0, 65 | +2.8 ± 4.4, 82 |
| Barrett | +4.7, 73 | +3.3 ± 5.8, 78 |
| Jackson | +3.7, 79 | +1.5 ± 6.1, 90 |
| Gorsuch | +2.8, 84 | +1.4 ± 4.9, 91 |
| Thomas | +1.4, 92 | −2.8 ± 5.4, 82 |
| Kavanaugh | −0.7, 96 | −1.7 ± 5.3, 89 |

**Gate 1, the placebo, fails.** This is section 7's placebo refitted with
the career curve. The curve is estimated on the placebo data, so the test is
not circular.

| k | 0 | 1 | 2 | 5 | 8 | 11 | 14 | 17 | 18 | 20 | 21 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F placebo (points) | +1.6 | 0.0 | −1.8 | −3.8 | −2.4 | −5.0 | +0.5 | +5.4 | +6.2 | +6.0 | +5.7 |

- **Centred, as hoped.** Over 25 shifts the mean is −0.1, where A's is −1.2.
  At k = 0 the placebo is +1.6 (t = 1.0), where A's is +4.1.
- **But wide.** The sd over shifts is 3.4 points, 28% of shifts reach
  |t| > 1.96 (5% if the standard errors were right), and 44% of shifts are
  at least as large as F's real +2.7.
- **Per justice, the same.** Fake windows in the k = 0 position still show
  between-justice spread (tau 5.3, against F's real 7.6). A justice's fake
  estimate tracks their real one (Spearman +0.37, 31 justices). Justices'
  own career paths differ, as Epstein et al. found, and a pooled curve can't
  remove a path that is one justice's own.

Once a career curve is fitted, the appointer effect is the size of what a
window of the same length placed anywhere else in a career produces. That
is the opposite of what a loyalty score needs.

**Gate 2, the non-consecutive terms, corroborates nothing either way.** The
first-term appointees of the president who served non-consecutive terms sat
under him, then under his successor, then under him again from January
2025, four to eight years into their careers. This uses case and justice
fixed effects, compares them with the justices who sat on both sides
(leaving out the successor's own appointee) and clusters by case.

| Switch | Cases before / after | The appointees | The others | Appointer effect (95% CI) |
|---|---|---|---|---|
| The return, January 2025 | 92 / 43 | 0.465 → 0.481 | 0.511 → 0.500 | +2.7 (−6.2 to +11.6) |
| The departure, January 2021 | 74 / 92 | 0.337 → 0.424 | 0.417 → 0.505 | +1.9 (−8.4 to +12.3) |

Both point the loyal way and match F's +2.7. Both are consistent with zero
and with A's +6.6: 43 cases cannot tell them apart. The window runs through
the Database's 2025 term (2026 Release 01, decisions to June 2026).

**Decision: F is not shipped.** Gate 1 fails, and gate 2 cannot decide. So
no candidate has yet passed the placebo:
- A's own loyalty estimate is mostly career timing (about +2.5 of +6.6
  points survive the k = 0 placebo).
- F, which models the timing, leaves an effect no larger than fake windows
  produce.
- Per justice, every version tracks each justice's own career path.

The next section measures the score itself the same way, and the decision
follows from it.

## Per justice: the score itself under the placebo (2026-10-09), and justice v3

The F study found that fake windows reproduce F's differences between
justices. The same test was run on A, the score as it stood. For each shift
k, fake windows were placed, each justice was fitted, and DerSimonian-Laird
shrinkage was applied as the score does. Section 8 of the research script
prints it.

| | Real tau | Fake tau, k = 0 | Fake tau, median of 25 shifts | Spearman, real with own k = 0 fake | Real outside own placebo band |
|---|---|---|---|---|---|
| A | 8.5 | 6.0 | 4.9 | +0.34 (31 justices) | 11 of 25 (44%) |
| F | 7.6 | 5.3 | 4.8 | +0.37 (31) | 8 of 25 (32%) |

A justice's placebo band is the mean ± 1.96 sd of their own fake estimates
over every shift. It is defined for the 25 justices with five or more
shifts.

- **Fake windows reproduce most of A's spread between justices:** 6.0 of
  8.5 points of between-justice sd at k = 0, 4.9 at the median shift.
- **A justice's fake estimate tracks their real one.**
- **For more than half of the testable justices, the real estimate is
  inside what their own fake windows produce.**
- **The real estimate does sit outside the band for 11 justices**,
  including Roberts (+15.5 against fake −0.6 ± 9.0) and Alito (+19.2 against
  −1.3 ± 11.5). But this check favours the real window: it is always the
  first years of a career, which no fake window can reach, and the k = 0
  placebo shows those years run high for everyone.
- **Six of the nine sitting justices can't be tested at all.** Sotomayor 2
  shifts, Kagan 3, Barrett 2, Gorsuch, Kavanaugh and Jackson 0: their
  careers after the appointer are too short.

**The Court-level effect that survives the placebo.** Take A's pooled effect
less its own k = 0 placebo, with a bootstrap over justices (200 draws): **+2.5
points, 95% interval −0.7 to +5.6**. F's +2.7 against its placebo sd of 3.4
gives a wider −4.0 to +9.5. Either way, it is not distinguishable from no
effect.

**Decision: justice v3, no justice scored or ranked.**
- A's per-justice differences are substantially reproduced by fake
  windows, as F's were. For most sitting justices the data can't test them
  at all.
- So the data can't support ranking individual justices on this measure.
- Per-justice 0-100 scores are retired. Each scorecard says "not scored":
  no method yet separates loyalty to the appointing president from career
  timing for an individual justice.
- Each justice's own estimate, unshrunk, is shown with its 95% confidence
  interval as information, unranked. It isn't shrunk because shrinkage
  toward the other justices only made sense to place justices on a common
  scale.
- The Court-level finding above is stated on the justices leaderboard and
  the About page.

Scores retired, from the justice v2 research run (Supreme Court Database
2026 Release 01): Kavanaugh 96, Thomas 92, Gorsuch 84, Jackson 79, Barrett
73, Kagan 65, Sotomayor 54, Roberts 31, Alito 16. All are now not scored.

## What is shown (justice v3)

The pipeline refits every justice at each run:
- Epstein & Posner's votes through 2014 (bundled,
  `backend/app/data/justice_president_votes_1937_2014.csv.gz`, written by the
  research script);
- the newest Supreme Court Database release from 2015 on;
- each appointing president taken from the Federal Judicial Center's
  nomination dates and the presidents' own terms.

Each justice's own least-squares estimate of the appointing president's
effect and its HC1 standard error are stored (`justices.appointer_effect`,
`appointer_effect_se`). The API adds the 95% interval (estimate ± 1.96 se)
and serves every score as null ("not scored", never 0).

A justice not yet in the Database, or without 10 votes both under the
appointing president and under others, has no estimate. Martin-Quinn
positions and the voting record are shown beside it, for context.

Justice v2's score, for the record: 100 × (1 − |shrunk effect| / (2 ×
between-justice sd)), floored at 0. The research script still computes it
(`shrink`) to compare specifications.
