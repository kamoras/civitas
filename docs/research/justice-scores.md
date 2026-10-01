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

**Not party alignment either.** "Under other presidents" mixes presidents
of the appointer's party with presidents of the other party, so the gap could
in principle be a lean toward same-party administrations rather than toward
the one president. Epstein & Posner code that split themselves
(`same_partyExcludeInOffice`; from 2015 the research script derives it from
the presidents' parties). Pooled over the 20,737 votes coded both ways, with
justice fixed effects and petitioner or respondent controlled, against
presidents of the other party:

| Under | Points more often with the government | t |
|---|---|---|
| The appointing president | +8.4 | 3.3 |
| Other presidents of the appointer's party | −1.2 | −0.5 |

Justices side with same-party administrations no more often than with the
other party's; the loyalty is to the appointing president. Against same-party
presidents alone the appointing president's edge is +9.7 points (t = 2.0), so
comparing with every other president, as the score does, if anything
understates it. Per justice the same-party effect is not distinguishable
from zero for any sitting justice except one, and that one leans the other
way (fewer votes for other same-party administrations). For four of the nine
it can't be measured at all: no other president of their appointer's party
has held office since they joined the Court. A score split
into appointing president, same party and other party was considered on this
evidence (2026-10-01) and not adopted: its middle term measures nothing on
average and is undefined for nearly half the current Court.

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
