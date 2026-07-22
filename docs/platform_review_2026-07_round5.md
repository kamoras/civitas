# Platform review, round 5 — 2026-07 (DB/transaction integrity, numerical robustness, API↔frontend contract)

Fifth audit pass, over three subsystems earlier rounds only touched
incidentally: SQLite transaction safety / pipeline partial-failure
recovery, the numerical robustness of the four scorers, and the
API↔frontend data contract. Same environment caveat as before (live site
+ most external sources unreachable; embedding-dependent tests can't run
in the sandbox — those failures are pre-existing and unrelated).

Findings **fixed in the accompanying PR** are marked ✅; those deferred for
a larger change or live calibration are marked ⏭ with the reason.

---

## DB & transaction integrity

- ✅ **House per-rep loop had no `db.rollback()` on failure → cascading loss
  of the whole batch (HIGH).** The exact `PendingRollbackError` bug class
  round 3 fixed for the stock/Senate pipelines was never back-ported to
  House. `upsert_representative` commits internally, so one rep's DB error
  (IntegrityError, or "database is locked" under concurrent writes) left
  the session poisoned; the next rep's first query raised
  `PendingRollbackError`, and so did every remaining rep. One bad rep
  silently wiped out the rest of the run. Added `db.rollback()` as the
  first line of the handler (`house_pipeline.py`), mirroring Senate.
- ✅ **House outer handler didn't roll back before recording FAILED → run
  wedged in RUNNING, blocking stock ingestion indefinitely (MED-HIGH).**
  When the failure was a DB error, the outer handler's own `db.commit()`
  also raised (poisoned session), so `house_run.status` was never
  persisted as FAILED — and since House has no stale-timeout self-heal, the
  row stayed RUNNING forever, which makes the stock pipeline's "is House
  running?" guard skip every future stock run until an admin clears it by
  hand. Added `db.rollback()` before the FAILED write.
- ✅ **Supplementary pipeline never rolled back a failed sub-pipeline's
  partial writes (MEDIUM).** The explore/justice/president phases run on
  one shared session and only commit at their end; a mid-phase failure
  left partial rows staged, and the next phase-boundary `db.commit()`
  persisted them — a "failed" justice refresh could still write incomplete
  justice rows. Added `db.rollback()` to each inner handler and the outer
  one.
- ✅ **`bill_analyzer._record_if_possible` swallowed a DB error without
  rolling back (LOW-MED).** `record_classification` issues a Core execute
  with no internal rollback, so a failed write poisoned the shared session
  and the next bill's `lookup_exact` raised `PendingRollbackError`. Added a
  guarded rollback.
- ⏭ **`HousePipelineRun` (and the other non-Senate run tables) have no
  stale-timeout self-heal (MED).** Senate marks runs STALE after a timeout
  in `_acquire_pipeline_lock`; House/supplementary/stock don't, so a
  process that dies entirely (OOM/kill) between creating the run row and
  the exception handler leaves it RUNNING with nothing to clear it. The
  rollback fixes above close the *exception* path; this closes the
  *process-death* path and is a larger change (porting Senate's lock/stale
  logic) — flagged for a focused PR. (`ops_alerts` already alarms on a
  stuck run, so it's visible in the meantime.)
- ⏭ **`ProgressTracker._flush()` commits unrelated pending session state
  (LOW-MED).** Because the tracker shares the pipeline's session, every
  progress checkpoint commits whatever ORM state is staged — the mechanism
  that turns a failed phase's partial writes into committed data. Its
  defensive `rollback()` on a failed commit is load-bearing. The clean fix
  (a short-lived separate session for progress rows) is a structural
  change; the rollback fixes above remove the current triggers.
- ⏭ **`record_pulse_vote` counter is a read-modify-write race; dedup is
  per-process (LOW).** Two concurrent votes on separate connections can
  lose an increment; the fix is an atomic `UPDATE ... SET concerned_count
  = concerned_count + 1`. Best-effort by design (the docstring says so);
  low impact.
- ⏭ **`score_snapshots` grows unbounded (LOW).** ~580 rows/run with no
  retention cap (~210k/year). Intentional trend history; wants a retention
  or downsampling policy — a data-policy decision.

Verified sound: stock-pipeline idempotency and its round-3 per-chamber
rollback; the `api_cache_set` empty-payload guard; `record_classification`
upsert on the correct unique key; Senate per-loop and outer rollbacks;
delete-then-insert snapshot writes are single transactions (no
momentarily-empty window); action-center persist is atomic with dense
rank renumbering; session lifecycles close in `finally`.

---

## Numerical robustness (four scorers)

- ✅ **Coalition-breadth normalizer used a wrong "median" (MEDIUM).**
  `sorted(rates)[len//2]` is the upper-middle element, not the median, for
  even-sized cohorts (the normal case for ~100 senators / ~435 reps),
  biasing the normalizer's anchor high — which pushed members whose
  crossing rate sits just above the true midpoint below the 0.5
  Coalition-Breadth threshold and handed them the v6.8 below-median
  seat-safety discount they shouldn't get. Now uses `statistics.median`.
- ✅ **Constituent-alignment data gate compared a confidence-WEIGHTED sum
  against a raw-COUNT threshold (LOW-MED).** `party_total` accumulates
  per-vote weights in ~(0.5, 1.0], so a member with 5 genuine multi-area
  votes could sum below 3.0 and be dropped to a flat neutral 50 and labeled
  "fewer than 3 votes." Now gates on a raw usable-vote count (`n_party`),
  using the weighted sums only for the rate itself. Regression test added.
- ⏭ **PAC-dependency hard floor collapses the entire high-PAC tail to 0
  (MEDIUM).** `max(0, 1 − pac_ratio·3.2)·100` zeroes every senator past
  ~31% PAC ratio identically, losing all rank information across the
  most-PAC-dependent decile — the same hard-clamp-flattens-a-tail pattern
  the president PR replaced with tanh. The fix (soft `tanh` saturation, or
  a gentler slope with a small floor) **reshapes a calibrated scoring
  curve** and shifts the distribution, so it needs a live pipeline run +
  ground-truth range re-check to ship safely — deferred rather than
  changed blind. Documented as the top calibration follow-up.
- ⏭ **Estimator/rounding inconsistencies at gate margins (LOW).**
  `ground_truth.check_score_distribution` gates on `pstdev` while
  `score_calibration._dim_stats` reports sample `stdev`; a run on a floor
  could pass one and fail the other. `clamp()` uses banker's rounding.
  Both are margin-only; pick one estimator (pstdev for a population floor).

Verified sound: every reachable division is guarded; no NaN/inf source in
the hot path (donor totals are non-negative rounded ints, embeddings are
unit-normed, PageRank/SVD operate on `identity + nonneg`); all shrinkage
weights stay in [0,1]; weight renormalization never zero-denominators;
PageRank/SVD degenerate-matrix edges are guarded; senator/rep
`compute_overall_score` is not vulnerable to the present-but-None path
(score columns are non-nullable with `default=50.0`).

---

## API↔frontend contract

- ✅ **Justice peer-agreement names rendered garbled ("SoniaSotomayor") on
  every justice profile (HIGH).** The justice detail endpoint already
  serializes camelCase via `model_dump(by_alias=True)`, but `fetchJustice`
  *also* recursively camelized the payload, rewriting the `agreementMatrix`
  map's ID keys (`sonia_sotomayor` → `soniaSotomayor`); `AgreementRow`'s
  `split("_")` then rendered them run-together. Dropped the redundant
  `camelize` from `fetchJustice`.
- ✅ **`ideologyScore` / `leadershipScore` of exactly 0.0 rendered as "no
  data" (MEDIUM).** `round(x, 1) if x else None` in the My-Reps and
  Elections builders fired on a legitimately-computed 0.0 (ideology 0.0 =
  most-progressive member; leadership 0.0 = lowest centrality), hiding the
  true extreme. Changed to `is not None` (5 sites in `action.py`, plus the
  same pattern in `action_center._make_entry`).
- ✅ **Per-senator contact links never appeared on Action Center issue
  cards (MEDIUM).** `action_center._make_entry` already stores
  `contact_form_url` / `website_url` in the related-senator blob, but the
  `RelatedSenator` schema didn't declare them, so `RelatedSenator(**s)`
  stripped them and the card's "CONTACT" button always fell back to a
  generic senate.gov link — wrong chamber for House members in the list.
  Added the two fields to the schema (existing stored blobs already carry
  the data).
- ✅ **President responses were double-camelized (LOW, latent).** Same
  anti-pattern as the justice bug but currently harmless (no map field);
  removed `camelize` from both president fetches for parity, before a
  future dict field silently breaks.
- ⏭ **`CampaignPromise.relatedBills` emitted but absent from the TS type
  (LOW).** Backend sends it; the type omits it, so promise-linked bills are
  never surfaced. Wants a type field + a render alongside `relatedVotes`.
- ⏭ **Donor `type: "SKIP"` missing from the TS union (LOW).** If a SKIP
  donor reaches `top_donors` it would print the literal "SKIP"; the correct
  fix is to confirm whether SKIP is filtered from `top_donors` and either
  filter it or give it a display label.

Verified sound: all `score_*` dimensions are non-nullable with defaults
(no NaN/0-bar/500 from null scores); president nullable stats are
`!= null`-guarded; score-breakdown dimension keys match on both sides;
explore 200-with-error-body and the comments error shape are correctly
typed; bill-stage enum drift falls back gracefully; leaderboard sorts push
null ideology/leadership last in both directions; pagination counts are
internally consistent.

---

## PR #218 re-review (president real-data rebuild)

Re-reviewed at head `396e11d`. The PR's internal logic re-verified sound
(GDP peak-CAGR/z-score/tanh math, all-null renormalization guards, the
`bush-41 → ghwbush-41` migration, `is_current` drop-and-rebuild,
Cleveland/Trump/Garfield handling; both newest commits check out). The
blocker is now a **rebase**, not the code: `main` advanced (#224/#225) and
rewrote the same president files, producing a real conflict in
`president_scorer.py` (take the PR's competence-removed/renormalized side),
a **silent** auto-merge in `config_definitions.py` (confirm the surviving
`compute_president_overall_score` and `PRESIDENT_SCORE_WEIGHTS` share a key
set), and a `PresidentClient.tsx` conflict that must *combine* main's
`getPresidentLabel` with the PR's not-yet-calculated guard. Full detail
posted on the PR. Genuinely-open internal items (election-margin scale
mixing, hardcoded C-SPAN legacy mean/stdev vs its docstring, fragile
Cleveland election-margin match) are all minor or already deferred.
