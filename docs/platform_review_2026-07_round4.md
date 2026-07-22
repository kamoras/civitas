# Platform review, round 4 — 2026-07 (frontend/cross-view, temporal/determinism, test quality)

Fourth audit pass, over three subsystems the first three rounds left
mostly untouched: the frontend rendering + same-number-everywhere
consistency, temporal correctness / reproducibility, and the test suite's
own ability to catch the bugs it's supposed to. Same environment caveat as
the earlier rounds — the live site and most external data sources are
unreachable from the audit sandbox, and the sentence-transformer model
can't be downloaded, so embedding-dependent tests can't run here.

Findings **fixed in the accompanying PR** are marked ✅; those that need a
larger refactor, live calibration, or a design decision are marked ⏭ with
the reason.

---

## Frontend / cross-view consistency

- ✅ **Justice `overall` never rounded → float-garbage decimals (HIGH).**
  `justice_service._build_score` returned the raw weighted sum, so the
  SCOTUS leaderboard and profile card rendered headline scores like
  `62.165000000000006`. Every other scorer rounds its serialized overall;
  justice now does too (`round(..., 2)`).
- ✅ **Explore 503 `indexEmpty` contract was unreachable by the client
  (MEDIUM).** `searchExplore` went through `requestJson`, which throws on
  any non-2xx and discards the body — so the round-3 "index still
  building" 503 surfaced as a bare `Explore search failed: 503` instead of
  the intended friendly state. `searchExplore` now reads the 503 body and
  returns `{indexEmpty: true}`, and the page shows an honest "index is
  still being built" message.
- ✅ **President profile used constituent-representation labels (LOW-MED).**
  `getScoreLabel` returns senator labels (`STRONGLY REPRESENTATIVE` /
  `DEEPLY CAPTURED`) — wrong vocabulary for a presidential-performance
  score. Added `getPresidentLabel` (STRONG / EFFECTIVE / MIXED / WEAK
  PERFORMANCE / FAILING) and used it on the president header.
- ✅ **Justice "Bipartisan Agreement" tooltip described the wrong quantity
  (MEDIUM).** The tooltip said "fraction of cases decided unanimously,"
  but the backend computes cross-bloc agreement rate with opposing-party
  justices. Reworded to match what's scored.
- ✅ **`formatCurrency` mishandled negatives (LOW).** A negative value
  skipped every magnitude threshold and fell through to
  `$${amount.toLocaleString()}` → `$-1,000,000`. Now operates on the
  magnitude and re-attaches the sign outside the `$` → `-$1.0M`.
- ⏭ **Same senator/rep overall renders at 4 precisions across views
  (MEDIUM).** Header badge `.toFixed(0)` (`74`), the big RepresentationScore
  number (`73.64`), the public API (`74`), and the Bluesky spotlight
  (`73.6`) all differ. The fix is a single shared `formatScore()` used by
  every renderer — but it's a site-wide precision decision (which
  convention wins) best made with the page in front of you, not shipped
  blind from a sandbox with no browser. Documented for a focused frontend
  PR.
- ⏭ **ScoreTrend version + congress-boundary markers can overlap (LOW).**
  Both marker sets key on snapshot index; a snapshot that is both draws
  two coincident vertical lines. Cosmetic; a 1px offset or merged legend
  is a visual-tuning change.
- ⏭ **Client cache TTLs can briefly desync trend/breakdown from the
  headline after a nightly run (LOW).** Partly by-design (data changes
  ~daily); the right fix (invalidate on a detected pipeline-run timestamp
  vs. just lowering the history TTL) is a judgment call.

Verified sound: unified color tiers (`getScoreColor`/`getScoreBgColor`
share cutoffs), president competence tooltip is accurate, weight captions
match config, MetricTooltip a11y is solid, score bars carry numeric
`aria-label`s with color as redundant (not sole) encoding, and the
frontend never recomputes `overall` from sub-scores.

---

## Temporal correctness / reproducibility

- ✅ **`CURRENT_CONGRESS` is a hardcoded time bomb (HIGH).** The Senate
  roll-call window is pinned to the `CURRENT_CONGRESS` config constant
  while the House window is derived from the wall-clock year. Once a new
  Congress convenes (Jan 3 of each odd year) and the constant isn't
  bumped, the two chambers score against *different* Congresses and the
  Senate keeps scoring a dead one — silently, with no guard. This fires
  for real after 2027-01-03. Added `expected_current_congress()` (the
  date-derived inverse of `congress_first_year`) and a
  `check_current_congress_staleness()` ops guard run at the start of each
  nightly pipeline — a loud, deduped operator alert that names the
  Congress to bump to. It deliberately does **not** auto-advance the
  constant: the scored windows key off config precisely so an archived-DB
  re-run stays reproducible, so bumping it stays a one-line operator
  action.
- ✅ **Analysis-cache fingerprint didn't cover the generative-model
  identity (MEDIUM).** `_compute_analysis_code_hash` hashed pipeline
  `.py` + `config_definitions.py`, but `OLLAMA_MODEL` / `LLM_BACKEND` live
  in env-driven `config.py`, which isn't hashed — so swapping the LLM
  left every cached classification labelled by the previous model
  indefinitely. The resolved model id + backend are now folded into the
  fingerprint.
- ✅ **SVD ideology sign-anchoring had an unguarded fallthrough (LOW-MED).**
  The axis is oriented so the Republican-cohort mean is positive, but if
  that cohort was empty or its mean ~0 the sign was left unnormalized — a
  degenerate/party-balanced projection could flip the whole axis between
  environments, flipping the v6.7 position-mismatch discount. Added a
  deterministic fallback (first bioguide-sorted member with a non-zero
  coordinate) so orientation is always pinned.
- ⏭ **Wall-clock time leaks into scored values (MED-HIGH).**
  `years_in_office = datetime.now().year - first_year` feeds Legislative
  Effectiveness, so the same DB row scores differently on a different
  calendar day (and a member crossing the 6-year full-credit threshold
  shifts). The clean fix — snapshot `years_in_office` at ingest and thread
  an explicit `as_of` date through scoring — is an invasive pipeline
  change touching every scored transform; deferred to a dedicated PR
  rather than bolted on here.
- ⏭ **Timezone convention is split three ways (MED).** Scored transforms
  use server-local `datetime.now()`, snapshot dates use UTC, action-center
  deadlines use ET. Determinism holds within a UTC day; the risk is at
  boundaries. The fix (route every "what day is it" through `time_utils`
  with one convention per concern) pairs naturally with the `as_of` work
  above.
- ⏭ **Current-term window differs between dimensions (LOW).** Votes use
  `== CURRENT_CONGRESS`; LES bills use `>= CURRENT_CONGRESS`. Interacts
  with the staleness item; wants a shared "scored congresses" helper.

Verified sound: kNN classification is exact (full-corpus numpy dot-product,
not approximate HNSW), so no index non-determinism enters scores;
ScoreSnapshot slot mapping is consistent across all writers/readers;
president score slots don't average phantom zeros; PVI/data-file refreshes
leave no stale classifications; SVD/PageRank are LAPACK on deterministic
matrices.

---

## Test-suite quality

- ✅ **`_migrate_columns` was exercised by zero tests (HIGH).** The only DB
  fixture builds the *current* ORM schema, so the ADD/DROP COLUMN path —
  the exact path behind the #220 president crash-loop — could never be
  caught. Added `test_db_migrations.py`: builds tables at an old schema
  with raw SQL, runs the migration, and asserts columns move, defaults
  apply, dropped columns vanish, and legacy rows survive (plus
  absent-table and idempotency cases).
- ✅ **`compute_president_overall_score` had zero tests + a null-path
  crash (HIGH).** `getattr(entity, field, 0)`'s default only fires for an
  *absent* attribute, so a present-but-None dimension raised
  `None * weight` and 500'd the endpoint. Fixed (`getattr(...) or 0`) and
  added direct value + null-path + absent-attribute tests.
- ✅ **Newly-reachable LES stages were untested (MEDIUM).** The 2026-07
  max-over-history change made `IN_OTHER_CHAMBER` / `TO_PRESIDENT` /
  `VETOED` actually storable, but `_les_bill_stage` was only tested on
  `IN_COMMITTEE` / `ENACTED`. Added a test covering every `BillStage`
  value, with a guard that fails if a stage is added to the enum without a
  rank.
- ✅ **Vacuous assertions hardened (LOW-MED).** `algorithm_version is not
  None` → `== PRESIDENT_ALGORITHM_VERSION` (a wrong stamp now fails);
  the president "do-not-crash" test now also asserts competence actually
  moves with the live `eo_count`, so a regression collapsing it to the
  pure seed is caught.
- ⏭ **`*_core_matches_calc` tests give false confidence (MED-HIGH).** They
  compare two wrappers over the *same* formula, so a bug *inside*
  `_les_component_score` moves both sides together and still passes. The
  fix (assert a hand-computed value for a fixed multi-congress bill list)
  requires carefully reproducing the divisor / confidence-blend math by
  hand — worth doing, but risky to get subtly wrong from a sandbox that
  can't run the full scoring path; deferred rather than shipped
  approximate.
- ⏭ **`test_ignores_older_actions_uses_latest_only` is now vacuous (LOW).**
  Its fixture happens to make latest == max after the max-over-history
  change, so it no longer discriminates. Wants a case where latest ≠ max;
  bundled with the LES-test work above.

Verified strong: `TestConstituentAlignment` is thorough (real seat types,
both surplus/deficit branches, the v6.7 discount with its missing-data
guards, and a monkeypatched non-zero crossing-quality value); PVI data is
locked against hand-verified Cook anchors; the president slot-mapping test
uses four distinct values so a slot swap fails.
