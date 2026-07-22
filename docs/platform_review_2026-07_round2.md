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

_Findings from the dedicated prototype/content audit — see the section
appended below._

---

## Data-science deep pass (justice, partisan depth, grounding, bill stage)

_Findings from the dedicated statistics audit — see the section appended
below._
