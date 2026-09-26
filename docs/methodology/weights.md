# Score weights: decision records

Moved verbatim from the comments above `SCORE_WEIGHTS` and
`PRESIDENT_SCORE_WEIGHTS` in `backend/app/config_definitions.py` (fix 18,
2026-09). The code keeps only the current rule. "Above", "below" and "this
dict" refer to that file as it was.

## Member weights (`SCORE_WEIGHTS`)

```text
Weight rationale (2026-07 composite-validity audit): fundingIndependence
and fundingDiversity correlate at r=0.72 across the live Senate population
— both driven by the same underlying funding-profile signal (grassroots
small-dollar money scores well on both; PAC/large-donor-heavy fundraising
scores poorly on both), not the "distinct dimension" each is meant to
measure independently. A prior fix (2026-07, this dict's v6.0-era history)
rebalanced the two to a combined 25% rather than let the redundant pair
dominate (the audit's reference case: the sitting Senate Majority Leader
ranked 2nd from last Senate-wide, driven almost entirely by this pair,
despite above-median scores on the other three dimensions) — but a
rebalance still measures the same signal twice under two labels. v6.5
(2026-07) folds fundingDiversity into fundingIndependence outright: one
dimension, weight = sum of the two prior weights (0.20 + 0.13 = 0.33).
fundingDiversity's two signals (source breadth, industry concentration)
are now components inside fundingIndependence's own score, at internal
weights equal to each component's PRIOR contribution to the overall
score divided by the merged weight — a linear renormalization, not a new
judgment call — the continuous math is provably identical, so the only
effect is where rounding happens (clamp() rounds each dimension to an
int; previously FI and FD rounded independently before being weighted,
now the merged dimension rounds once), bounded at roughly half a point
either way (see score_calculator.py's v6.4->v6.5 changelog note for the
math). score_funding_diversity keeps being computed and stored, same
"kept independently visible, excluded from the weighted sum" pattern as
promisePersistence below — it just no longer has its own SCORE_WEIGHTS
entry or top-level scorecard panel.
v6.13: source breadth left fundingIndependence again (a second copy of
the small-donor share, R^2 0.79-0.86 on FEC data — see
docs/research/funding-independence.md); industry concentration stays.

promisePersistence removed entirely (2026-07, ALGORITHM_VERSION v6.0):
a live measurement across all 100 senators found 0 of 100 reached even
"medium" confidence per calculate_confidence()'s own thresholds (mean
0.3 evaluable promises, 76% with zero) — real campaign promises are
generic platform language that embedding-based matching against specific
vote/bill text structurally can't bridge (see
policy_alignment.compute_promise_vote_alignment's docstring, which
documents three prior fix attempts that didn't resolve it, and the
historical MIN_STDEV notes in ground_truth.py's git history documenting
the same collapse — the gate's point-mass check now catches it). The
underlying promise extraction/alignment pipeline and its "kept/broken/
partial" display keep running unchanged — only the scoring weight is
gone. Its 25% redistributed proportionally (each remaining weight ×4/3)
across the three dimensions confirmed empirically distinct in the audit
above (pairwise |r| < 0.31): constituentAlignment and legislativeEffectiveness
absorb the largest shares, fundingIndependence a smaller share consistent
with its correlated-pair status.
```

## President weights (`PRESIDENT_SCORE_WEIGHTS`)

```text
Independence (15%) and Follow-Through (20%) removed entirely (2026-07):
both were always 100% hand-set editorial values with no live formula and
no realistic path to one — Independence's obvious source (OpenSecrets'
revolving-door API) was discontinued in 2025, and Follow-Through would
need the same platform-text-vs-action embedding match already tried and
abandoned 4x for senators' Promise Persistence (config_definitions.py's
v6.0 note, above). Same precedent as that removal: rather than keep
presenting a hand-set number as a computed score, drop it. Their
combined weight first redistributed proportionally across the
remaining four (publicMandate 15->23%, effectiveness 20->31%,
competence 15->23%, agencyAlignment 15->23%), then a fifth dimension —
historicalLegacy — was added (2026-07) to cover what none of the other
four can: crisis leadership, moral authority, and similar historical-
consequence judgments that don't reduce to GDP growth, approval
polling, EO rate, or rulemaking volume (see president_scorer.
calc_historical_legacy's docstring — sourced from C-SPAN's Presidential
Historians Survey, a real external expert-consensus survey, not a
hand-set number).

historicalLegacy's weight went through two revisions after the initial
equal-fifths 20% (both 2026-07, both verified against the real
47-president dataset, not picked by eye):

1. Raised to 50%: the other four dimensions were never going to
   reconstruct "historical greatness" on their own (a booming economy
   or high EO-activity rate doesn't reliably track what historians
   actually weigh), so at 20% four dimensions that don't individually
   track greatness could outvote the one that does — Coolidge,
   McKinley, and Harding all landed in the top 10 while Lincoln and
   Eisenhower fell out of it.
2. Brought back down to 35%: at 50%, the Spearman rank correlation
   between this platform's overall ranking and a pure 100%-
   historicalLegacy ranking (i.e. just C-SPAN's own answer) measured
   0.958 — the other four dimensions were contributing almost nothing
   of their own. The four mechanical dimensions ALONE (0% weight)
   correlate only 0.172 with C-SPAN — near-zero, meaning they measure
   something genuinely different from historical-greatness judgment,
   not a noisy/broken attempt at the same thing, so drowning them out
   entirely wasn't defensible either. 35% is the point where the top
   of the ranking is already recognizable (FDR, Washington, Lincoln,
   Theodore Roosevelt, JFK, Eisenhower) while the four mechanical
   dimensions still meaningfully move the rest of the ranking
   (correlation to pure C-SPAN only 0.886, not 0.96) — a real,
   disclosed compromise between two only loosely correlated kinds of
   judgment, not a weight tuned until the result looked acceptable.
   Coolidge and McKinley still edge into the bottom of the top 10 at
   this weight; that's an honest, arguable disagreement with C-SPAN's
   own ranking, not something further weight-tuning should paper over.

This has no effect on how the currently-serving president is scored:
historicalLegacy is null for anyone without a completed, C-SPAN-rated
term, so compute_president_overall_score's renormalization already
falls back to the other three dimensions entirely in that case.

Competence (EO-activity-rate) removed entirely (2026-07), same
"no defensible live signal" standard as Independence/Follow-Through
above. A Coolidge-ranking review found EO-activity-rate — Competence's
only ever-populated component (court-success-rate and cabinet-turnover
have no fetch source, see president_scorer._competence_core's removed
docstring) — has essentially zero relationship with real administrative
competence: Spearman correlation of 0.097 (p=0.53, statistically no
different from noise) against C-SPAN's own "Administrative Skill"
category score across the same 44 historians-rated presidents. Coolidge
and Harding make the gap concrete — nearly identical EO-rates (~216/yr
each) but historians' actual administrative-skill judgment rates them
596 vs. 334 (of 1000), almost as far apart as two presidents get.
Swapping in C-SPAN's Administrative Skill score directly (rather than
just disclosing EO-rate's weakness) was considered and rejected: it
isn't an independent data source, it's literally one of the ten
categories C-SPAN itself sums into the same Final Score already driving
historicalLegacy at 35% — folding it into a second, separate dimension
would push this platform's true historian-derived weight toward ~51%
(35% + Competence's share), undoing the exact over-reliance-on-C-SPAN
problem the 50%->35% revision above was calibrated to avoid. Competence's
16.25% is redistributed evenly across the three remaining mechanical
dimensions (21.67% each) rather than reopened as a fresh full weight
search — verified this still hits the same qualitative target that
justified 35% (Lincoln and Eisenhower both stay in the top 10; Coolidge
drops from top-10 to #12, Harding to #26, McKinley to #17).

Renormalization redesigned (2026-07, v4): the weights below are nominal
— until this fix, compute_president_overall_score renormalized flatly
over whatever dimensions were present, which let historicalLegacy's
EFFECTIVE weight balloon well past 35% for anyone missing mechanical
data: ~44.7% for the ~36 presidents missing only agencyAlignment
(everyone before Clinton), ~61.8% for the four non-elected successors
(Tyler, Fillmore, Arthur, Andrew Johnson) missing agencyAlignment AND
publicMandate too. 35% was the operative number for only 4 of 47
presidents. Now historicalLegacy is held at exactly the configured
weight whenever >=2 mechanical dimensions are present (see
compute_president_overall_score's docstring for the full mechanism and
the flat-renormalization fallback below that floor — needed because a
single mechanical number otherwise dominates: Fillmore's Effectiveness
is 100/100 purely from a Gold-Rush-era GDP boom he had little to do
with, which would swap a near-bottom 19/100 historian rating for a
top-10 placement if held to a flat 65% share). Re-verified against the
real dataset under this new scheme: 35% still lands Lincoln/Eisenhower
in the top 10 and Coolidge/Harding/McKinley out of it, so the number
itself didn't need to change — only how it's applied.
```
