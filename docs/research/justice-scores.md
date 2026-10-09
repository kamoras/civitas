# Justice scorecard: what each measure actually measured

Two studies. The first (v6.13) cut the Supreme Court scorecard from four
measures to two; the second (justice v2, September 2026) found those two
ranked justices by distance from the Court's center and replaced them with
loyalty to the appointing president.

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

**Extended through 2024** with the Supreme Court Database (2025 Release 01),
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
hold, the two codings agree on the vote for the president 98.99% of the time.

**Pooled, the effect holds.** 31,506 votes, 1937-2024, justice fixed effects,
petitioner or respondent controlled (the Court reverses more often than it
affirms): a justice sides with the government **6.5 points** more often
while the appointing president is in office (t = 3.0, clustered by justice).

**Per justice, it is noisy.** Each justice's own estimate, shrunk toward the
mean by its noise (DerSimonian-Laird random effects over 42 justices: mean
+4.9 points, between-justice sd 8.6):

| Justice | Loyalty (points) | Votes, appointing president / others |
|---|---|---|
| Alito | +14.5 ± 5.0 | 56 / 383 |
| Roberts | +11.7 ± 5.1 | 62 / 388 |
| Sotomayor | +7.2 ± 4.4 | 168 / 192 |
| Kagan | +5.4 ± 4.6 | 129 / 189 |
| Barrett | +4.6 ± 6.6 | 27 / 91 |
| Thomas | +1.3 ± 5.8 | 40 / 843 |
| Gorsuch | +1.2 ± 5.2 | 87 / 92 |
| Kavanaugh | −0.3 ± 5.8 | 62 / 92 |
| Jackson | −0.6 ± 6.8 | 50 / 27 |

Only Alito and Roberts are clearly above zero. Split-half (odd against even
terms, 33 justices), the estimates correlate at 0.52, a reliability of 0.68
for the full record: moderate, which is why the uncertainty is shown with
every estimate rather than left out.

**Not distance from the median.** Across the 40 justices with Martin-Quinn
positions, loyalty's rank correlation with a justice's mean distance from the
Court's median is 0.28 (p = 0.09), where the old measures ran −0.75 and
−0.82. Roberts, nearest the median, and Alito, among the furthest, show the
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
Database after, 31,506 votes: 8,179 under the appointer, 11,529 under other
presidents of the appointer's party and 11,798 under the other party.

- **A**, the score: appointer against every other president.
- **B**: appointer against other presidents of the appointer's party only.
- **C**: every vote, with the appointer and same-party indicators side by
  side, so the appointer's edge is net of the party's.
- **D**: the party effect itself, same-party against other-party presidents,
  leaving out the appointer's own terms.

| | Justices measurable | Of the current nine | Mean (points) | Between-justice sd (tau) | Spearman with A |
|---|---|---|---|---|---|
| A | 42 | 9 | +4.9 | 8.6 | 1 |
| B | 33 | 5 | +6.3 | 7.0 | 0.96 (33) |
| C | 33 | 5 | +6.4 | 6.8 | 0.97 (33) |
| D | 29 | 5 | +1.3 | 5.1 | −0.09 (29) |

Pooled, with justice fixed effects and clustered by justice, against the
other party's presidents:

| Under | Points more often with the government | t |
|---|---|---|
| The appointing president | +7.0 | 2.6 |
| Other presidents of the appointer's party | +1.0 | 0.7 |

The appointer's edge over other same-party presidents is +6.0 points
(t = 3.5). For comparison, Epstein & Posner's Table 9 has 66.2% against
61.2% in raw rates.

The current Court, shrunk effect in points and score, with votes in/out in
brackets. Under D the sides are same-party and other-party votes.

| Justice | A | B | C | D (party effect) |
|---|---|---|---|---|
| Alito | +14.5, 16 (56/383) | +13.4, 4 (56/100) | +13.3, 3 (56/100) | −0.7 (100/283) |
| Roberts | +11.7, 32 (62/388) | +13.7, 2 (62/101) | +13.7, 0 (62/101) | −3.8 (101/287) |
| Sotomayor | +7.2, 58 (168/192) | +6.8, 52 (168/92) | +6.8, 50 (168/92) | +1.4 (92/100) |
| Kagan | +5.4, 69 (129/189) | +5.0, 64 (129/92) | +5.1, 63 (129/92) | +1.9 (92/97) |
| Barrett | +4.6, 73 (27/91) | not measurable (27/0) | not measurable | not measurable |
| Thomas | +1.3, 93 (40/843) | +1.7, 88 (40/297) | +2.1, 85 (40/297) | +2.7 (297/546) |
| Gorsuch | +1.2, 93 (87/92) | not measurable (87/0) | not measurable | not measurable |
| Kavanaugh | −0.3, 98 (62/92) | not measurable (62/0) | not measurable | not measurable |
| Jackson | −0.6, 96 (50/27) | not measurable (50/0) | not measurable | not measurable |

The full 42-justice table is printed by the research script (section 5).

**The same-party effect is not real on average, and it accounts for none of
A's loyalty.** Justices side with other administrations of their appointer's
party about one point more often than with the other party's (t = 0.7). So
mixing both into A's comparison group shades A down by about half a point,
not up. B and C order justices almost exactly as A does (Spearman 0.96 and
0.97). Their scores fall mainly because their tau is smaller (7.0 and 6.8
against 8.6), which stretches the same effects over a shorter scale. The
material moves are scale, not reordering. Roberts goes from 32 to 2 and
Alito from 16 to 4, because against same-party presidents alone (Trump's
first term) their edge is larger than A's (raw +22.6 and +22.0, against +15.4
and +19.5). Among historical justices the same pattern moves Breyer (24 to 0),
Souter (41 to 9) and Robert Jackson (28 to 8); Warren goes the other way
(70 to 93, because only 30 of his votes were under another Republican). D
does vary between justices (tau 5.1): Douglas +11.8, Black +7.8 and Burger
+6.3 favored their party's other administrations, while Brennan (−5.3),
Souter (−4.5) and Reed (−4.5) disfavored them. But it averages about zero.
No sitting justice's D is distinguishable from zero (the largest is Roberts,
raw −9.6 ± 5.5, in the direction opposite to partisanship).

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

**Loyalty or era: an open question (found 2026-10-09).** The appointer's
time in office is always the start of a justice's career. The government's
share of votes has fallen: 0.66 in the 1980s, 0.63 in the 1990s, 0.57 in the
2000s, 0.47 in the 2010s and 0.50 in the 2020s (Epstein & Posner 2018 report
the same decline in win rates). So comparing a justice's early years with
their later ones partly measures that decline. Three ways of holding the era
fixed (research script, section 7), pooled with justice fixed effects:

| Specification | Points | t |
|---|---|---|
| A as scored | +6.5 | 3.0 |
| + a fixed effect per term | +0.5 | 0.4 |
| The justice's vote minus their colleagues' on the same case (E) | +4.1 | 2.3 |
| 1953-1980, + term fixed effects | +4.1 | 2.7 |
| 1981-2024, + term fixed effects | +4.3 | 3.2 |
| 1937-1952, + term fixed effects | −9.2 | −5.4 |

The near-zero pooled term-fixed-effects estimate comes entirely from
1937-1952. In those years nearly every justice was an FDR or Truman
appointee, so the within-term comparison rests on a handful of holdovers.
Since 1953, and in the same-case comparison, loyalty survives at roughly
two-thirds of A's size (about +4 points against +6.5).

Per justice, E keeps all 42 justices and the current nine and orders them
much like A (Spearman 0.79). The same-case comparison lowers Roberts and
Alito's effects (+8.4 and +10.1 against +11.7 and +14.5), and Thomas turns
slightly negative (−4.7). But E's split-half reliability is 0.41, against A's
0.68. Part of A's reliability may itself be the era: each half of a career
has the same early-high, late-low timing. So the era-controlled estimate is
noisier, and A's is steadier partly because it carries the confound.

This is not resolved here. Switching the score to E is a judgment between
validity and reliability that changes several current scores by 15 to 25
points (Roberts 32 to 45, Alito 16 to 34, Thomas 93 to 69, Gorsuch 93 to 75).
It needs its own decision and its own pipeline input: the bundled votes carry
no case identifier.

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

## The score

The pipeline refits every justice at each run: Epstein & Posner's votes
through 2014 (bundled, `backend/app/data/justice_president_votes_1937_2014.csv.gz`,
written by the research script) and the newest Supreme Court Database
release from 2015 on, with each appointing president taken from the Federal
Judicial Center's nomination dates and the presidents' own terms. The table
above is the research run; the scorecard shows the pipeline's.

**Score = 100 × (1 − |loyalty| / (2 × sd))**, floored at 0, where sd is the
random-effects spread between justices' true effects (8.6 points in the
research run). 100 is no favoritism either way. Favor against the appointing
president's government lowers the score the same as favor toward it: both
are a departure from treating every president's government alike. The zero
point, two between-justice sds (17 points in the research run), is a design
choice: it puts the scale in units of how much justices actually differ,
rather than a number typed in, so it moves with the data. Every estimate is shown with its standard
error, since per-justice reliability is moderate (0.68, above).

Research-run scores: Kavanaugh 98, Jackson 97, Gorsuch 93, Thomas 92,
Barrett 73, Kagan 69, Sotomayor 58, Roberts 32, Alito 16.

A justice not yet in the Database, or without 10 votes both under the
appointing president and under others, is unscored, never given a neutral
number. Martin-Quinn positions are shown beside the score, for context,
and not scored.
