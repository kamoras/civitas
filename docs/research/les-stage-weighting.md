# Legislative Effectiveness: does it measure what Volden & Wiseman measure?

Legislative Effectiveness's main component (60% of the dimension) has always
been described as following Volden & Wiseman's Legislative Effectiveness Score
(LES; Volden & Wiseman 2014, *Legislative Effectiveness in the United States
Congress*). This note records a test of that claim against their own published
scores, what it found, and what v6.14 changed as a result.

Every number here is printed by
[`backend/scripts/research_les_stage_weighting.py`](../../backend/scripts/research_les_stage_weighting.py).

## What the LES does

V&W count each member's sponsored bills at five stages: introduced, action in
committee, action beyond committee, passed the chamber, became law. A bill is
counted at every stage it reaches, weighted by significance (commemorative 1,
substantive 5, substantive and significant 10). At each stage the member's
weighted count is divided by the chamber's weighted total at that stage. The
five shares are summed and scaled by N/5, so the average member scores 1.0.

The per-stage division is the core of the measure. Few bills reach the later
stages, so those totals are small, and a bill there is a large share of them.

## What Civitas did before v6.14

It credited each bill its significance weight times the number of stages it
reached. There was no division by stage totals, so an enacted bill counted as
four introductions.

## The test

**Data.** The Center for Effective Lawmaking publishes per-member counts for
every stage and significance tier, alongside the official LES, for the 93rd to
118th Congresses (thelawmakers.org; the two spreadsheets are fetched by the
script). The test uses the 110th to 118th: 4,039 House and 920 Senate
member-congresses.

**Check that the reconstruction is right.** Rebuilding LES 1.0 from the counts
reproduces the published column exactly (Pearson r = 1.0000 in every congress).
Without that, the comparisons below would mean nothing.

**Comparison.** For each variant: Spearman rank correlation with the published
LES within each congress and majority/minority group, since Civitas compares
each member only with their own status. Also reported is what each variant
tracks: its rank correlation with bills introduced and with laws.

| House | ρ with LES (median) | min | ρ with bills introduced | ρ with laws |
|---|---|---|---|---|
| Civitas credit before v6.14 | 0.764 | 0.680 | **0.954** | 0.328 |
| Stage-normalized, four Civitas stages | 0.933 | 0.895 | 0.598 | 0.747 |
| + content-based commemorative weight | 0.981 | 0.962 | 0.606 | 0.634 |
| *(the published LES itself)* | | | 0.591 | 0.589 |

| Senate | ρ with LES (median) | min | ρ with bills introduced | ρ with laws |
|---|---|---|---|---|
| Civitas credit before v6.14 | 0.785 | 0.554 | **0.976** | 0.493 |
| Stage-normalized, four Civitas stages | 0.967 | 0.921 | 0.693 | 0.845 |
| + content-based commemorative weight | 0.984 | 0.966 | 0.686 | 0.838 |
| *(the published LES itself)* | | | 0.673 | 0.813 |

The old credit was almost exactly a count of bills introduced (ρ 0.95–0.98). The
published LES correlates with introductions at only 0.59–0.67. Under stage
normalization, one enacted bill is worth a median 47 introductions in the House
and 67 in the Senate, against 4 before.

**The whole scorer.** The same data was fed through the pipeline's own
`compute_les_reference` and `_les_component_score`, with each member's bills
rebuilt from their stage counts. The v6.13 scorer was reproduced exactly for
comparison.

| | v6.13 ρ with LES | v6.14 ρ with LES | majority − minority median score |
|---|---|---|---|
| House | 0.710 | 0.895 | 0.0 (both) |
| Senate | 0.763 | 0.963 | 0.0 (both) |

## Decisions

1. **Stage normalization, adopted.** `_les_normalized_credit` divides by the
   chamber's weighted total at each stage, measured each run in
   `compute_les_reference` (`stage_totals`, `n_members`).
2. **Bill-count shrinkage, removed.** v6.13 pulled a member's score toward 50
   by min(bills / 10, 1). With stage normalization in place, that shrinkage
   alone cost 0.06 of rank agreement in the House (0.90 to 0.84 in an ablation
   of the scorer on the same data). The credit is a total over every bill the
   member sponsored, all of them observed, so there is no sampling noise to
   shrink. It also already grows with volume, so the shrinkage penalised low
   volume twice: two bills with one enacted ranked below twenty that never
   moved.
3. **Zero bills is a credit of 0.** Removing the shrinkage broke v6.13's
   "inaction never beats an attempt" rule, which a fixed 42.5 for zero bills
   had kept only because every one-bill record was also pulled toward 50. A
   member with no substantive bills after half a year in office now scores a
   credit of 0 on the same scale, which is V&W's LES for that record. The rule
   now holds by construction, since credit rises with every bill and every
   stage.
   *Speakers.* By custom the Speaker sponsors few bills, so this scores a
   Speaker low. That matches the standard being cited: V&W's own benchmarks
   expect as much of Speakers as of the average member or more (0.99–1.48
   in the 109th–118th Houses, against an average of 1.0), and rate recent Speakers well below expectations
   (LES/benchmark 0.02–0.53, except Pelosi in the 110th and Ryan in the
   114th). No exemption was added. A record of zero bills is rare: 0.1% of
   House member-congresses in the data.
4. **A failed download is not zero bills.** `fetch_member_sponsored` returned
   an empty list when Congress.gov failed, and that run scored the member as
   "confirmed inactivity". It now falls back to the last cached list, or
   reports the list as unknown, and the component stays neutral.

## What is still different, and why

- **Commemorative bills — now detected.** Bill type can't tell a
  post-office naming from any other H.R. bill, so v6.14 classifies titles
  with an embedding classifier (`analyze/commemorative.py`, on the
  similarity model): best similarity to three commemorative prototypes
  minus similarity to a substantive one, over a calibrated threshold.
  [`calibrate_commemorative.py`](../../backend/scripts/calibrate_commemorative.py)
  fits the threshold against V&W's own per-member commemorative counts for
  the 118th Congress, joined to GovTrack's bill titles. Where the two
  sources agree on a member's total, the bill sets are identical, which
  gives two measures: the false-positive rate on bills of members V&W
  credit with no commemorative bills, and the error in each member's count.

  | 118th Congress | members | FPR | count MAE (always 0) | exact count (always 0) |
  |---|---|---|---|---|
  | House (fitted) | 326 | 0.10% | 0.150 (0.491) | 87.4% (64.4%) |
  | Senate (held out) | 52 | 0.09% | 0.288 (0.865) | 73.1% (51.9%) |

  End to end, rebuilding every member's stage-normalized LES from GovTrack
  statuses and weighting predicted commemorative bills 1x raises rank
  agreement with V&W's published LES from 0.902 to 0.944 (House) and 0.936
  to 0.955 (Senate). The threshold and these numbers live in
  `app/data/commemorative_calibration.json`, with a hash of the prototypes
  they were fitted to; a test fails if the prototypes change without a
  recalibration.
- **Substantive and significant (10x).** V&W assign this tier from CQ Almanac
  coverage, which has no source here. Its effect is inside the gap between the
  last row and 1.0.
- **Four stages, not five.** Congress.gov's action codes give committee action
  and reporting as one stage (IN_COMMITTEE).
- **Benchmark.** Each member is compared with the median member of their own
  majority/minority status in their chamber (v6.13). The majority − minority
  gap stays at zero.
