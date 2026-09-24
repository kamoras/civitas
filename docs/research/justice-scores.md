# Justice scorecard: what each measure actually measured

This note records why v6.13 cut the Supreme Court scorecard from four measures
to two. Every number is printed by
[`backend/scripts/research_justice_scores.py`](../../backend/scripts/research_justice_scores.py),
which runs this repository's own `analyze_justice_votes` over real and
simulated votes.

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
