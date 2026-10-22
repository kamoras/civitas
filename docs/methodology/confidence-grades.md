# Data grades (Full / Partial / Thin) and the leadership tenure weight

Measured 2026-10-22. Code: `score_calculator.calculate_confidence`,
`score_calculator.LEADERSHIP_TENURE_FULL_CREDIT_YEARS`.

Each score column shows how much record it rests on, graded Full, Partial or
Thin. The grade is display only: no score reads it.

## Constituent Alignment: measured

The grade asks whether a member's break rate measures the member or the
handful of votes that happened to be cast. That is split-half reliability
(Spearman 1910; Brown 1910): split each member's party-line votes in two at
random, measure the break rate on each half, and correlate the halves across
the chamber. At each record length n (two disjoint draws of n votes per
member with at least 2n votes, 50 draws, a fixed seed) that gives the
reliability of a break rate measured on n votes. Partial starts where it
reaches 0.70 and Full where it reaches 0.80, the conventional bars for modest
reliability and for basic research (Nunnally & Bernstein 1994, *Psychometric
Theory*, 3rd ed., pp. 264-265). Votes are labelled as the scores label them,
housekeeping aside, each measure once.

`scripts/calibrate_confidence_grades.py` writes `app/data/confidence_grades.json`
from the stored roll calls, and `data_refresh` reruns it monthly. On the
119th Congress (production, 2026-10-22):

| | Partial | Full | Reliability at 5 votes | at 20 votes |
|---|---|---|---|---|
| Senate (101 members) | 33 votes | 53 votes | 0.21 | 0.59 |
| House (450 members) | 15 votes | 30 votes | 0.44 | 0.75 |

The hand-set counts were 5 and 20 for both chambers: a senator graded Full on
20 votes had a break rate whose halves agreed at 0.59. Senators break less
often than House members, so the same reliability takes more votes. The
grade is now measured separately from the vote score's shrinkage, which still
saturates at `CONSTITUENT_FULL_CONFIDENCE_VOTES` (20); changing that is a
scoring change, not a grade one.

## The other grades: conventions

No data here can calibrate these counts, so each is a stated convention:

- **Funding Independence, 3 / 10 top donors.** The concentration part reads
  the share of the ten largest donors, so Full is a full top ten. A donor
  list is one election's filings and has no halves to compare.
- **Funding Diversity, 3 / 6 industries.** Entropy over a few industries can't
  spread far; six is where Full starts, three where Partial does.
- **Legislative Effectiveness, 2 / 5 bills sponsored.** These were 3 and 10
  over two or three Congresses and were halved by hand when the window became
  one Congress (2026-07). A member's bills are the whole record, not a sample,
  so there is no reliability to measure, only how much record there is.
- **Promise Persistence, 3 / 8 promises.** No input since promise tracking was
  removed; kept for stored scores that still carry it.

## The leadership tenure weight: a convention, measured

Legislative Effectiveness's leadership part (cosponsorship PageRank) is
pulled toward 50 in proportion to tenure, with full weight from 6 years in
office: one Senate term, three House terms. Measured on the serving members,
a fit of leadership on tenure that rises to a bend and is flat after puts the
bend at:

| | Bend | Bootstrap 95% interval (400 resamples) |
|---|---|---|
| Senate (100) | 7 years | 3 to 11 |
| House (433) | 1.5 years | 1.5 to 6.5 |
| Both, chamber intercepts (533) | 4 years | 1.5 to 7 |

One Congress's cross-section can't place the bend more closely, and the House
fit is pulled by its longest-serving members, whose leadership scores fall
again. Six years is inside all three intervals, so it stays as a stated
convention rather than a fitted value.
