# Platform review, round 2 — 2026-07 (data science + political science)

Follow-up to `platform_review_2026-07.md` (round 1, merged in #221). Round 1
covered pipeline mechanics; this round audits what round 1 could only
surface-check: the **content validity of the human-authored prototypes and
seed corpora** that the "non-partisan by construction" claim ultimately
rests on, the **currency of the checked-in political calibration data**,
and the **justice / partisan-depth / grounding statistics**.

Same environment caveat as round 1: the live site and most external data
sources are unreachable from this sandbox, so findings that need a live
measurement are marked as such rather than asserted.

---

## Calibration-data currency (audited directly)

### C1. Senate seat expectations run on a stale PVI vintage (2016+2020); House uses 2020+2024 — MEDIUM-HIGH

`app/data/state_pvi.json` is computed (per its `_source`/`_method` and
`scripts/fetch_state_pvi.py`, whose pinned dataset ends at 2020) from the
**2016 & 2020** presidential elections and gated against Cook's **2022**
published PVIs. `app/data/district_pvi.json` was scraped from Wikipedia
infoboxes on **2026-07-10**, which carry Cook's current (2020+2024-based)
PVIs. Two consequences:

1. **Cross-chamber inconsistency**: a senator and a representative from the
   same state are measured against seat expectations derived from
   different election windows.
2. **Post-2024 realignment is missing from every senator's seat
   expectation.** Between the 2016/2020 and 2020/2024 windows several
   states moved 2–3+ PVI points (e.g. FL, OH, TX toward R; parts of the
   Northeast toward D on the margin-of-shift). At the ±15-point
   normalization in `_signed_state_alignment`, a 3-point shift moves the
   alignment signal by 0.2 — enough to move a seat between the
   "swing" and "aligned" regimes of the expected-break-rate curve and the
   seat-safety discounts (v6.6–v6.8 all scale on this signal).

**Fix (turnkey, needs network):** update `scripts/fetch_state_pvi.py` —
`CYCLES = ("2020", "2024")`, swap `SOURCE_URL` for a 2024-inclusive
official-returns dataset (MEDSL's "U.S. President 1976-2024" on Harvard
Dataverse is the successor of the pinned file), replace `COOK_ANCHORS`
with hand-verified values from Cook's 2025 published state list (same
role: fidelity gate, not scoring input), rerun, commit the regenerated
JSON. Then re-verify `GROUND_TRUTH` IV ranges (the checked reference
senators' seat classifications — GA/PA/SD — happen to be stable across
vintages, so failures would indicate real drift elsewhere). Regeneration
was attempted from this environment but every 2024-inclusive source
(Dataverse, Wikipedia) is blocked by the network policy; per the
project's own "calibrated constants are generated data" rule, no numbers
were hand-edited.

Checked and current: `committee_membership.json` / `leadership_roles.json`
(2026-07-13, unitedstates/congress-legislators), `small_donor_baseline.json`
(2026-07 fit; spot-checked — predicts ~10% for WY, ~29% for CA, consistent
with the documented tercile averages), `state_population.json` (2020
census, per-census cadence documented), `CURRENT_CONGRESS = 119` (correct
for 2025-26), `_SENATE_MAJORITY`/`_HOUSE_MAJORITY` through 119 (correct),
FEC PAC caps (2025-26 cycle values with a documented recalibration note).

### C2. Two embedding-similarity regimes; thresholds must be audited per-regime — NOTE

Round 1 flagged several "abstain" thresholds as unreachable because raw
cosines in the asymmetric-prompt regime floor at ~0.74 (the codebase's own
recorded measurements in `industry_classifier.py` / `bill_analyzer.py`).
The action center's news-relevance filter (0.22, `action_center.py`
`POLICY_RELEVANCE_THRESHOLD`) lives in a **different** regime —
plain-document encodings (`normalize_embeddings=True`, no query prompt),
where similarities run much lower, and its own comment records a
"news-headline floor" consideration. So 0.22 is *not* automatically a
no-op the way the round-1 thresholds are, and conversely, the round-1
findings should not be "fixed" by copying thresholds across regimes. Any
live threshold-calibration pass should first print the per-regime score
distributions (a 20-line script against the production model) before
touching any constant.

---

## Political-science content audit (prototypes and seed corpora)

The "non-partisan by construction" claim rests on the natural-language
prototype texts humans wrote to seed the classifiers. The mechanisms
around them are genuinely strong (see "Done well"); the residual partisan
exposure is concentrated in the *content* of `party_platform.py`'s seed
positions. Ranked:

### P1. Abortion/reproductive rights is absent from the entire construct space — HIGH

Neither `POLICY_TAXONOMY` (`bill_analyzer.py`) nor either party's platform
seeds (`party_platform.py`) contains an abortion, reproductive-rights, or
social-issues category — the single most party-predictive issue domain of
the 2022–26 era. Abortion bills get shoehorned into HEALTHCARE or JUSTICE,
whose centroids mention nothing relevant, so party alignment on exactly
the bills where partisanship is most legible is essentially embedding
noise (unpredictable direction per bill — which is itself the failure).
The same gap recurs in the action center's 18 news prototypes. **Fix:**
add an ABORTION/social-policy category with charitable both-party seeds,
then let the pipeline's fingerprint-clearing rebuild classifications.

### P2. DEFENSE seeds encode a pre-2016 hawk/dove alignment — HIGH

R seed: "counter China and Russia… support arms sales to allies"; D seed:
"diplomacy first… close overseas bases, end forever wars." In the 119th
Congress, Ukraine aid / NATO support / Russia sanctions are majority-D
positions with substantial R skepticism — a Ukraine supplemental embeds
squarely on the R centroid, so mainstream D votes for it register as
cross-party defections and R votes against it as loyalty. The D seed
describes the 2019 progressive flank, not the median 2025 Democrat.

### P3. R TECH seed now inverts the party's actual position — MEDIUM-HIGH

"Reduce tech regulation, **maintain platform liability protections**…"
— current R politics (§230 repeal, tech antitrust, KOSA) is substantially
pro-regulation of platforms, and the D seed owns "section 230 reform."
R-sponsored tech-regulation bills classify D-aligned and their sponsors
appear to vote cross-party.

### P4. Opponent-framed planks appear only on the R side — MEDIUM

R JUSTICE includes "**expand executive authority**" (a critic's
description, not a platform plank — and since the JUSTICE taxonomy
explicitly covers executive authority, any bill touching executive power
leans R by construction); also "keep cash bail" and LABOR's "**reduce
union power**" (Rs say "worker freedom"). No comparably uncharitable
phrasing exists in the D seeds. This converts contested characterizations
into classifications.

### P5. Election policy has a D anchor and no R anchor — MEDIUM

D JUSTICE includes "expand voting rights"; no seed anywhere mentions
voter ID / election integrity / mail-ballot policy. Election-
administration bills have exactly one party anchor, so R election bills
misroute — issue-ownership bias in a domain both parties actively
legislate.

### P6. GUNS/ENVIRONMENT taxonomy recall asymmetry — MEDIUM

The GUNS *taxonomy* description is ~9 parts gun-control vocabulary to a
brief "gun rights, and the Second Amendment" — a gun-rights bill has
weaker lexical anchoring and is likelier to fall below the confidence
threshold and drop out of GUNS analysis entirely (a recall asymmetry, not
an alignment one — the GUNS *party seeds* are exemplary, see below).
ENVIRONMENT has the same one-sided structure, partially mitigated by
drilling/permitting living in ENERGY.

### P7. General seed staleness (~2019–20 snapshot) — MEDIUM

"Repeal ACA mandates," "Green New Deal" (twice), "rejoin Paris
Agreement," "universal basic income" (never a D platform position — a
flank item dragging the WELFARE centroid), "tariffs on China"
(understating the 2025 universal-tariff R position). The Bayesian blend
(`_PRIOR_WEIGHT = 3`) decays seed influence where vote-labeled data
accumulates, but sparse policy areas stay pinned to 2019 — and the
self-training data-centroid loop recycles seed-derived labels as "data"
precisely in the areas that rarely reach roll calls.

### P8. Gun-rights advocacy money is industry-anchored; gun-control advocacy money is not — MEDIUM

`INDUSTRY_DESCRIPTIONS` GUNS anchors manufacturers **plus** gun-rights
advocacy orgs (NRA, GOA); the mirror-image orgs (Everytown, Giffords,
Brady) have no anchor and fall to POLITICAL/OTHER. Downstream, the
donor-vote connection gate (industry↔policy similarity ≥0.75) can fire
for NRA money on gun votes but structurally cannot for Everytown money —
the donor-influence penalty is reachable for one side's donor base on
this issue and unreachable for the other's. Fix: either anchor both
advocacy sets or neither (manufacturers only).

### P9. Seat-expectation curve: 20% opposed-seat endpoint is empirically high, but its bite is already gone — LOW-MEDIUM

As political science: the 3% base matches CQ/Voteview unity; 8% swing is
defensible; a sustained 20% break rate exceeds even peak Collins/Manchin
behavior in the nationalized Senate. But the feared failure mode (every
opposed-seat member below 50) was already removed by v6.6's loyalty
floor; the residual effect is a *credit ceiling* — opposed/swing-seat
moderates must clear an inflated bar to earn above-50 credit, compressing
genuine crossers toward neutral, symmetrically for both parties. Also
the −1.0 endpoint is nearly vacuous today (no sitting senator holds a
≥15-PVI opposed seat). Recalibration candidate for the next live pass,
not urgent.

### P10. Action-center news prototypes: mild D-agenda issue-selection tilt — LOW

Justice appears only as "civil rights, discrimination, equality, justice
reform" (no crime/law-enforcement/border-crime prototype); climate gets
two prototypes with no energy-production counterpart; abortion absent
(P1). Mitigated by max-over-prototypes at a permissive threshold and by
the actionability *ranking* having already abandoned authored prototypes
for data-derived signals.

### P11. Disclosed-exception documentation rigor is uneven — LOW

`derive_stance`'s tier-0 check is fully measured as claimed (n=2979,
1.5% of outcomes, all rescues). The hotel-brand tier cites a mechanism
and a source but records **no numbers** despite the README's "measurably
score as MEDIA… verified anomaly"; the PAC/payment-processor tier is
qualitative. No undisclosed keyword tiers were found beyond the already-
flagged round-1 items. Documentation-integrity only, politically neutral.

### Done well (calibration — this is not a uniformly biased system)

- Party seeds are mostly charitable in both directions; the GUNS platform
  pair is a model of balanced specificity (matched length, each side's
  own vocabulary, matched granularity).
- Donor/industry prototypes are valence-free (no "predatory lenders" /
  "Big Pharma" loading); LABOR_UNIONS is treated symmetrically with
  corporate sectors; Emily's List and Club for Growth both anchor to
  POLITICAL; ActBlue and WinRed are both skipped as processors.
- The stance word sets are symmetric, apolitical legislative verbs.
- `refine_with_vote_data` always lets real roll-call splits override
  content alignment, so seed errors cannot contaminate party-loyalty
  computation for voted bills.
- The scoring module's audit culture (v6.6–v6.9 changelogs, refusing to
  ship unfit constants) is exceptional, and the actionability ranking
  already replaced its authored prototypes after its own audit found the
  gate passed 125/125 articles.

**Bottom line:** a single refresh pass on `party_platform.py`'s seed
texts against the parties' actual 2024–26 positions, plus an ABORTION
category and the Everytown/NRA symmetry fix, addresses the top findings.
Per AGENTS.md, prototype text is the *input* to the classifier — updating
it is seed maintenance, not a formula change — but it shifts
classifications platform-wide, so it should ride through the fingerprint
cache-clearing mechanism and a ground-truth + stdev re-check, ideally
with the same before/after shadow measurement the v5.x changes used.

---

## Data-science deep pass (justice, partisan depth, grounding, bill stage)

Ranked. F1 and F3 are concrete scoring bugs (fix-ready, though both shift
scores platform-wide and so belong in their own measured pass per the
project's calibration discipline); the rest are construct-validity and
robustness findings.

### F1. Partisan-depth `overallLean` mixes two incompatible scales — HIGH

`party_platform.analyze_partisan_depth`: vote-derived leans are Yea/Nay
ratios in [−1, +1], aggregated to ONE entry per policy area; promise-
derived leans are raw embedding cosine margins (empirically |m| ≲ 0.15),
one entry **per promise** — and both go into one unweighted average. A
senator with 25 promises and 4 vote-areas has their reliably partisan
voting record (±0.8 leans) numerically dragged toward 0 by 25 near-zero
promise margins and reads "centrist." The docstring's stated hierarchy
("promises cannot override the vote signal") is not implemented anywhere
in the math. Fix shape: aggregate promise margins per area, rescale to
the vote-lean range, and weight votes ≥ promises explicitly.

### F2. Justice consistency clamp + double-counted construct at 65% weight — HIGH

Beyond round 1's clamp finding: `max(0, own − opp)` makes a
counter-partisan justice (agrees with the *opposing* bloc more) score a
perfect 100 — identical to a genuinely balanced justice — and the
breakdown panel reports the clamped value, hiding the inversion, under a
tooltip reading "follows law, not party." Additionally, consistency
(bloc-agreement differential) and independence (cross-bloc vote rate)
are two transformations of the same cross-bloc behavior, jointly 65% of
`JUSTICE_SCORE_WEIGHTS` — one construct measured twice dominates the
overall score.

### F3. Latest-action-only bill-stage classification regresses stages, and LES consumes the regression — HIGH

`bill_stage.classify_bill_stage_from_actions` examines only `actions[0]`.
The *normal* path for every bill that passes one chamber is: pass origin
chamber (PASSED_CHAMBER) → "Read twice and referred to the Committee" in
the second chamber → reclassified IN_COMMITTEE — a stage regression.
`_les_bill_stage` maps IN_COMMITTEE→2 vs PASSED_CHAMBER→3, so sponsors
are docked one cumulative-credit stage in Legislative Effectiveness at
the exact moment their bill advances to the other chamber. A vetoed bill
whose latest action is a failed override vote falls through to
INTRODUCED (stage 1, from 3). Fix shape: classify from the **max** stage
reached across the full action history (monotone stages; `is_law` already
short-circuits correctly).

### F4. Grounding layer is purely lexical — no semantic entailment — HIGH (disclosure), MEDIUM (fix)

The LLM-output verification catches fabricated digit groups, ungrounded
titled surnames, electoral phrasing, repetition, and hedging — nothing
checks that a claimed fact is *entailed* by the sources. "Collins voted
against the bill" when the source says she voted for it passes every
check. Verified false-negative modes: surname check is a bare substring
("Rep. Ford" grounded by "affordable"); untitled full names never
checked; numbers-as-words bypass the digit check (and cause the mirror
false positive). The electoral-claims check is additionally near-vacuous
(F10): it disarms if the source contains "constituents"/"poll"/"race"
anywhere. Recommendation: disclose the guarantee honestly (the About page
should not imply fact-checking), tighten surname matching to word
boundaries, and consider an NLI-style entailment gate for the Bluesky
path specifically.

### F5. "Judicial Restraint" measures dissent-frequency conformity, not restraint — MEDIUM

The score is a curve over dissent rate plus an authored-dissent penalty;
it never examines votes to strike down statutes vs. defer — the actual
restraint construct in the literature the docstring cites. The API
tooltip honestly says "balanced dissent patterns," but the dimension
*name* (20% of the overall) asserts a construct the data cannot support.
Rename or re-derive.

### F6. Bipartisan-agreement tooltip describes a different metric than computed — MEDIUM

Tooltip: "fraction of cases decided unanimously or near-unanimously."
Code: case-weighted pairwise agreement with opposing-bloc justices. For a
transparency platform, disclosed methodology must match code.

### F7. Justice scores: unanimous-case floor, 4-term window undisclosed, no small-N shrinkage — MEDIUM

Unanimous cases (~40% of docket) are correctly excluded from consistency
and independence but included in bipartisan agreement — a high common
floor that compresses a 15%-weighted dimension into a narrow band.
`fetch_case_votes` windows to the last 4 terms but the UI presents
career-level framing. And with no shrinkage anywhere, one cross-bloc vote
in one split decision yields independence = 100 (vs neutral 50 with zero
split decisions) on a 30%-weighted dimension. Port the senator-side
Beta-Binomial/confidence-grade pattern.

### F8. Recusal/multi-decision handling in justice votes — MEDIUM (conditional on Oyez shape)

The fetch appends every vote entry unfiltered; a non-participation value
deflates dissent rates, and two justices sharing the same non-vote value
compare as agreeing — recusal counted as agreement. Multi-decision cases
share one case_id and are merged, so pairwise comparisons can
double-count. No guard exists either way; needs a live Oyez data-shape
check.

### F9. Partisan-depth confidence denominator mismatch + snapshot-frozen thresholds — MEDIUM

`data_confidence = min(partisan_vote_count/15, 1)` counts every Yea/Nay
— including votes on bipartisan bills and areas dropped by the 2-vote
minimum — none of which contribute observations to the lean. The SVD
prior can zero out while the lean rests on 2–3 real observations.
Separately, the depth-label thresholds (0.20/0.10/0.02) are hard-coded
against one 2026 snapshot whose own comment documents an asymmetric
distribution (D: −0.30..−0.05, R: +0.10..+0.43) — at |lean|>0.20 most
Democrats structurally cannot be labeled "deep" while a large share of
Republicans can. Thresholds should be party-relative or regenerated per
run (and the asymmetry disclosed).

### F10. Electoral-claims grounding check disarms on common political vocabulary — MEDIUM

See F4 — `_ELECTORAL_CONTEXT_RE` includes "constituents," "poll(s)," and
"race"; one such word anywhere in the source silences the whole check.

### F11. Bipartisanship score is raw edge rate — the edge-weighting fix never reached it — MEDIUM-LOW

`compute_bipartisanship_scores` re-counts cosponsorships flat, ignoring
the ENACTED/ADVANCED/STALLED edge weights added (v6.2) precisely to stop
message-bill farming — which therefore still works for Coalition Breadth.
The 2×-median cap also pins everyone ≥2× median at exactly 1.0, and
`min_interactions=5` allows a 3-of-5 record to hit the cap with no
shrinkage.

### F12. Party-relative ideology labels encode rank, not position — LOW

Within-party terciles guarantee ~⅓ of each party reads "centrist" every
run regardless of absolute movement. Defensible and documented in code,
but not disclosed at the label surface.

### Verified sound (calibration)

Fisher-style case weighting math checks out (correct IRT
item-information form; 5-4 vs 8-1 ratio exactly 2.50 as documented);
veto/enactment ordering in bill_stage is correct (`is_law`
short-circuits before action inspection); the SVD sign-orientation fix
is genuine outside a measure-zero degenerate case; tenure shrinkage on
leader/follower labels is right; `refine_with_vote_data`'s vote-wins
rule is well-reasoned; justice blocs are data-derived (the hardcoded
Roberts-as-swing special case was correctly removed); and the displayed
justice breakdown reuses the identical analyzer, so shown math matches
stored scores on an unchanged bench.

---

## Recommended sequencing

1. **Fix-ready, own PR each, with shadow measurement**: F3 (bill-stage
   max-over-actions — LES correctness), F1 (partisan-depth scale fix),
   the P8 donor-anchor symmetry fix, and the C1 PVI regeneration (needs
   network).
2. **Seed-content refresh** (P1–P7): one deliberate pass updating
   `party_platform.py` to the parties' 2024–26 positions + ABORTION
   category, shipped through the fingerprint cache-clear with
   ground-truth + stdev re-checks and a before/after alignment-shift
   measurement.
3. **Justice scorer redesign** (F2/F5/F6/F7/F8 + round-1 O8): one
   coherent pass — unclamp or sign the consistency differential, rename
   or re-derive restraint, fix the tooltip/metric mismatches, add
   shrinkage and window disclosure, and verify Oyez recusal shape live.
4. **Grounding disclosure** (F4/F10): tighten cheap lexical holes now;
   scope an entailment gate for outbound (Bluesky) content separately.
