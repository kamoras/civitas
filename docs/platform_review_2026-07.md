# Platform review — 2026-07 (data quality focus)

A full-platform review of Civitas from three perspectives — computer science
(correctness, concurrency, pagination, caching), data science (statistics,
embedding-space calibration, self-training dynamics), and political science
(construct validity, attribution, comparability). The live site was not
reachable from the review environment, so live-data cross-referencing relies
on the recorded audits in `SCORE_AUDIT.md`, `docs/v6.6_live_verification.md`,
and the extensive measurement notes embedded in `score_calculator.py` /
`ground_truth.py`.

Findings are split into **fixed in the accompanying PR** (deterministic,
clearly-correct changes) and **open findings** (changes that would alter
classifications or scores platform-wide and therefore — per this project's
own rule that calibration constants are fit against live data before
shipping — need a live calibration/validation pass this environment cannot
run, or genuine design decisions for the maintainer).

---

## Fixed in this PR

### Scoring / data integrity

1. **`validate_senator` dropped `committeeType` from every donor**
   (`assemble/validator.py`). The Senate save path persists donors from the
   *validated* record, so `Donor.committee_type` was always NULL: the
   score-breakdown ("show the math") endpoint rebuilt Funding Independence
   via the dollar-based fallback while the stored score used the
   PAC-utilization path — the displayed derivation could not match the
   stored score. (House was unaffected; its path bypasses the validator,
   which is how this stayed invisible.)

2. **Senate recent roll calls were deduplicated by `documentName`**
   (`senate_pipeline.py`), but the Senate votes on the same document
   repeatedly — cloture + confirmation for one nomination, motion-to-proceed
   + passage for one bill, and re-votes across sessions. Only the newest
   such roll call survived; in a nomination-heavy session this silently
   discarded a large share of the recent-vote sample feeding party-loyalty
   rates and Constituent Alignment. Roll calls are now deduplicated and
   joined by a unique `congress-session-rollNumber` key (`rcKey`), threaded
   through `classify_recent_votes` → `recent_rc_map` → `senator_votes` →
   `normalize_votes`/`normalize_recent_votes`; `billId` keeps its display
   semantics.

3. **House upsert dropped `party_alignment_weight` and `policy_areas` from
   `RepKeyVote`** (`representative_service.upsert_representative`). The
   pipeline scored Constituent Alignment with fractional multi-area weights,
   then persisted votes without them — so the recomputed breakdown panel
   used weight 1.0 per vote and could disagree with the score bar next to
   it, and `get_rep_votes` always returned `partyAlignmentWeight: 0`.

4. **`compute_score_trend_map` required a snapshot dated exactly "today"**
   (`services/score_trends.py`): any day the pipeline hadn't run yet (early
   UTC hours) or had failed, every leaderboard trend silently vanished. The
   documented 1-6-day fallback was dead code (`min(target, yesterday)`
   always chose the 7-day target). Now works off the most recent snapshot
   date with a real fallback; regression-tested.

5. **House `ScoreSnapshot` upsert didn't refresh `algorithm_version`** on
   same-day re-runs, mislabeling snapshots after a same-day deploy.

### Fetch-layer data quality

6. **Federal Register EO counts had no president filter** and both term
   endpoints inclusive — the ~26 executive orders signed on inauguration day
   counted toward *both* the outgoing and incoming president (a
   double-digit-percent error on per-term totals of ~50–220). EO queries now
   filter by president slug; rulemaking windows end the day before the next
   term starts.

7. **BLS payroll fetch was hard-capped at year 2026**
   (`economic_data.py: min(end_year, 2026)`) — from January 2027 the jobs
   series would silently freeze for any in-progress term. Now capped at the
   current year.

8. **GovInfo bill-text version list had `is`/`rs` (Senate) but no
   `ih`/`rh`** — a House bill that hadn't passed a chamber never resolved
   any text package, a systematic chamber asymmetry in classifier input.

9. **A single malformed RSS `pubDate` dropped an entire feed**: RFC-2822
   `-0000` zones produce naive datetimes, the aware-vs-naive comparison
   raised TypeError, and the caller's blanket except logged "Failed to
   fetch feed". Naive datetimes are now coerced to UTC; regression-tested.

10. **`find_house_roll_call` fell back to hardcoded year 2025** when the
    Clerk URL didn't match `/evs/(\d{4})/`. House roll numbers restart
    yearly, so a wrong year resolves to a *real, different* vote and
    attributes wrong positions to all 435 members. Year now derives from
    the vote/action date, else the record is skipped; regression-tested.

### Serving layer / API contract

11. **House comparison was entirely broken from the picker**: the compare
    page requests `per_page=60` (CA has 52 districts) but the API capped
    `per_page` at 50 — FastAPI 422'd every House state selection and the
    frontend's `.catch()` rendered an empty list. Cap raised to 60.

12. **Public API `?state=X&party=Y` filtered party after pagination**, so
    page sizes varied arbitrarily while `total`/`totalPages` described the
    unfiltered set. Party is now pushed into the query before pagination.

13. **Vote and stock-trade pagination sorted on non-unique keys**
    (date-only). Rows could duplicate or vanish across pages; all four
    paginated queries now carry an `id` tiebreaker. Leaderboard sorts
    likewise gained a name tiebreaker (tied ranks could swap between
    requests).

14. **`SponsoredBill.stage` was persisted but dropped by both read paths**,
    permanently disabling the frontend's stage-based advancement display in
    favor of the free-text heuristic. Both chambers now emit `stage`.

15. **Public API rate limiter never evicted idle IPs** (unbounded memory on
    an unauthenticated endpoint). Now evicts stale entries every 2,000
    requests, mirroring `rate_limit.py`.

### Action center / auxiliary pipelines

16. **Monitor merge could destroy a monitor's update history**: after a
    keep/absorb swap deleted the outer monitor, the inner loop kept using
    the deleted row as a merge target — a third similar monitor could be
    re-parented onto the deleted parent and cascade-deleted. The loop now
    breaks when the outer monitor is absorbed.

17. **"Nightly" explore ingestion actually ran at most once per 72 h**: the
    `seed_version` gate rode on ApiCache's TTL, so 2 of 3 nights no new
    executive orders, rules, or floor speeches were ingested while the run
    reported success — and the action center's civic-document boost scored
    against a corpus up to ~3 days stale. The gate is removed (bootstrap is
    already guarded by `main.py`'s emptiness check); the embed step is now
    incremental (only new/refreshed documents are encoded) so nightly runs
    stay cheap on the Pi. The "docs ingested" metric now counts new
    documents instead of the whole embedded corpus.

18. **Monitor updates deduplicated on `source_urls[0]`**, which reorders
    between hourly runs — the same story accrued duplicate same-day updates
    whenever its leading source changed. Dedup now checks all source URLs.

19. **Week summaries keyed on (calendar year, ISO week number)**: late
    December belongs to ISO week 1 of the *next* year, colliding with
    January's row so the year-end week was never summarized. Now keyed on
    the full ISO (year, week) pair.

20. **`_generate_full_story` passed `db_session=None` with a `cache_key`**,
    silently disabling the first-attempt LLM cache it was built for.

### Documentation

21. **README displayed the pre-v6.0 five-dimension overall formula**
    (25/20/20/15/20) even though `SCORE_WEIGHTS` has been three dimensions
    (33/33/34) since v6.0/v6.5 — a user-facing methodology misstatement.
    The scoring section and `SCORE_AUDIT.md`'s Step-1 SQL now match
    `config_definitions.py` and note that Promise Persistence / Funding
    Diversity are still computed and displayed but unweighted.

---

## Open findings — need live calibration, or maintainer decisions

These are ordered by expected impact. None are fixed here because each
either changes classifications/scores platform-wide (and this project
rightly requires such changes to be fit and validated against the live
population first) or is a genuine design tradeoff.

### Classification / embedding subsystem (data science)

- **O1. Several "reject/abstain" thresholds sit far below the embedding
  model's real similarity floor and can never fire.** The codebase's own
  measurements put raw cosines in this model at ~0.55–0.87 even for
  unrelated text, yet gates exist at 0.10 (`bill_analyzer.py` neutral
  fallback), 0.20/0.25 (`nn_classifier.py` min similarity), 0.30
  (`bill_learning.py`), 0.35 (`donor_classifier_ai.py` semantic type;
  `policy_alignment.get_related_policies`). Consequences: donor types are
  always force-assigned at confidence 0.9; every kNN neighbor always
  "passes"; `get_related_policies(0.35)` admits essentially every
  (industry, policy) pair, so `select_key_votes`' "+2 donor-industry
  overlap" signal fires uniformly and key-vote selection degenerates to
  "voted against party, then arbitrary". Fix requires re-measuring each
  gate against the live score distributions (the same discipline
  `detect_donor_vote_connections`' 0.75 gate already went through).

- **O2. `_CORRECTION_THRESHOLD = 0.25` in `donor_classifier_ai.py` is
  compared against raw cosine (~0.74 baseline), not the centered scale its
  comment assumes** — the "only override the learning store on a strong
  signal" gate is vacuous, so any top-1 disagreement overwrites stored
  labels (including FEC-metadata 1.0-confidence rows) at confidence 0.92
  every run. Related: `_store_donor_learning`'s docstring promises
  confidence-gated upserts but the SQL overwrites unconditionally — note
  AGENTS.md §2 explicitly *endorses* unconditional overwrites, so the code
  and the two documents disagree; reconcile intent before changing either.

- **O3. Self-training loops lack confidence gating.** Every classified
  bill (including low-confidence guesses) is upserted into the kNN
  reference corpus and learning store, then treated as exact-match truth
  forever (`lookup_exact` short-circuits re-classification). The corpus's
  audited 55%-PROCEDURAL skew is the signature. Two mechanical
  contributors: the procedural gate (0.74, inside the measured noise band)
  returns confidence 1.0 for its matches, and `bill_learning.py` loads the
  reference corpus with `limit=5000`, unordered — an arbitrary subset once
  the collection outgrows it (also biasing the imbalance gate computed
  from it). Recommended shape: gate corpus ingestion on confidence and
  source, version the exact-match store, and page the corpus load.

- **O4. Unbounded inverse-frequency kNN weight** (`nn_classifier.py`):
  weight `sqrt(total/count)` gives a count-1 class (e.g. the always-seeded
  SKIP prototype) a vote multiplier of ~sqrt(N) — one marginal SKIP
  neighbor can outvote six genuine matches and knock a real donor out of
  finance totals. Needs a cap, chosen against live classification quality.

- **O5. `_normalize_category` encodes queries without
  `prompt_name="query"`** (unlike `classify_industry`, which documents the
  asymmetric-model requirement) and its 0.25 floor never binds — the
  "ghost class deletion" path is dead code, and junk labels are laundered
  into valid industries instead of purged. Fixing the encoding changes the
  similarity scale, so the threshold must be recalibrated with it.

- **O6. SVD ideology sign correction can silently no-op** in sparse
  windows (`sponsorship_analysis.py`: no "R"-party match or `|r_mean| ≈
  0` leaves the SVD's arbitrary per-run sign), and nothing validates that
  the second singular vector is actually the partisan axis. Since
  `ideology_score` now feeds the v6.7 position-mismatch discount and
  partisan-depth priors, consider a hard guard: if the R/D means on the
  chosen dimension don't separate (e.g. |r_mean − d_mean| below a floor),
  withhold ideology scores for the run rather than publish coin-flip signs.

- **O7. Keyword classification beyond the three disclosed exceptions**:
  `_NOMINATION_NAME_RE` (bill_analyzer), `_KEPT_RE`/`_BROKEN_RE`
  keyword-flips in `promise_quality.py` (which can force a correctly-KEPT
  promise about *opposing* something to UNCLEAR/BROKEN — "voted against
  the bill, consistent with his promise" matches both lists), and
  `_ADVANCEMENT_ACTION_KEYWORDS` deciding PageRank edge weights. Either
  promote these to documented exceptions with before/after measurements
  (the AGENTS.md bar) or replace with embedding checks.

### Political science / methodology

- **O8. Justice consistency clamps the negative differential**
  (`justice_analyzer.py`: `max(0, own − opp)`): a justice systematically
  voting *against* their appointing bloc scores the same 100 as a
  perfectly balanced one — the metric is one-sided by construction. New
  justices also get extreme scores from a handful of pairings with no
  small-N shrinkage, ranked on the same axis as 200-case veterans. The
  senator-side fix (Beta-Binomial shrinkage toward 50, confidence grades)
  is the established in-repo pattern to port.

- **O9. Presidential jobs attribution contradicts the pipeline's own lag
  doctrine**: GDP excludes year 1 (Blinder & Watson) but payroll change is
  attributed from inauguration-January with no lag — two components of
  one score use opposite attribution rules. The sitting president's jobs
  component is also silently absent (needs end-of-term January), so the
  incumbent is scored on a different basis than every predecessor in the
  same ranking. Related disclosure question: the leaderboard mixes 2021
  historians'-survey editorial seeds (pre-Clinton), live-computed scores
  (Clinton+), and neutral-50 defaults on one axis with no provenance
  marker in the ranking itself.

- **O10. Cross-chamber comparison presented as like-for-like** (compare
  page marks per-dimension "winners" between a senator and a
  representative) even though calibration is deliberately chamber-specific
  (PAC multiplier ×3.2 vs ×1.35; chamber-split LES baselines). A 70 in one
  chamber is not the same measurement as a 70 in the other. Recommend a
  UI caveat, or normalizing to within-chamber percentiles for the
  head-to-head view.

- **O11. FI's PAC-utilization caps are per-election but donor totals are
  per-cycle**: `min(total, $5,000)` reads a PAC that gave the legal
  primary+general $10,000 the same as one that gave $5,000 once —
  utilization is systematically overstated toward 100%. Worth splitting
  the cap per election count (or doubling it) next time the FI curve is
  recalibrated; not changed here because it shifts every senator's FI.

- **O12. Funding detail window ≠ totals window** (`fec.py`): totals come
  from the 6-year (Senate) election aggregate, but Schedule A detail /
  top donors / industry breakdown are windowed to 4 years — early-cycle
  donors vanish into `UNCLASSIFIED`; for the House the detail window
  reaches into the *previous* term. Also Schedule A is a single
  100-largest-receipts page treated as the donor universe. Both are
  calibration-absorbed today (concentration curves were fit on this
  data), so widening the fetch requires re-fitting the FI curves at the
  same time.

### Fetch / infrastructure

- **O13. Senate PTR ingestion**: (a) the eFD search never paginates
  (`start=0, length=100`) and the incremental anchor advances past dropped
  rows, so truncated filings are lost *permanently*; (b) electronic PTRs
  get `disclosure_date = transaction_date` (the HTML table has no
  notification-date column and the search row's `filed_date` is discarded),
  so every Senate trade scores as disclosed in 0 days — the STOCK-Act
  timeliness metric is fiction for the Senate; (c) `_find_col`'s first-
  contains-keyword binding can bind "Type" to an "Asset Type" column and
  silently ingest zero trades; (d) the filer→member match has no state
  disambiguation (two Scotts) and uses substring first-name matching.
  This subsystem needs a dedicated pass with live eFD HTML fixtures.

- **O14. FEC candidate matching requires every display-name token verbatim
  in FEC's registered name** — "Katie Boyd Britt" vs "BRITT, KATIE" fails,
  and the member is then *displayed* as "$0 raised" (scores correctly fall
  to neutral, but the UI states a false fact). Minimum fix: display
  "unknown" when the FEC match failed rather than $0; a last+first-token
  fallback tier would need validation against all 535 members.

- **O15. Concurrency**: the hourly action-center refresh has only a
  process-local guard — during blue/green overlap two containers can both
  run it (duplicate Bluesky posts, contended SQLite writes), and the 4-h
  "stale" override starts a second thread without stopping the first. The
  nightly `_acquire_pipeline_lock` is check-then-insert without a unique
  constraint or `BEGIN IMMEDIATE`, so its docstring's atomicity claim
  doesn't hold at the 03:00 tick. The scheduler's `CronTrigger` also has
  no explicit timezone, so the nightly run floats with container TZ while
  the action center dates by `America/New_York`.

- **O16. Prompt injection surface**: third-party RSS text (including
  `feedx.net`, an unofficial AP mirror — a single-point supply-chain risk
  for the highest-volume feed) is interpolated verbatim into every
  action-center LLM prompt; the grounding checks catch fabricated numbers
  and names but not steering of the summary itself, and the role-check
  gate fails open. Recommend delimiter-fencing article text, swapping
  feedx.net for AP's own feed, and failing the role check closed.

- **O17. Action-center matching residuals**: topic-keyed matching only
  considers the argmax recent issue (a second cluster above threshold
  spawns a duplicate row + duplicate Bluesky post); the 2-day lookback is
  keyed on a date that is overwritten on every match, so any topic that
  lapses 3+ days duplicates; an oversized pass-1 cluster skips
  re-clustering entirely and `_largest_coherent_subgroup` then discards
  every secondary topic inside it; `_FORWARD_PHRASES` contains the bare
  substring "continue", dropping true past-tense facts ("protests
  continued through June").

- **O18. Undated RSS items bypass the 48-hour window** (kept regardless of
  age). Deliberately permissive filtering is documented, but an undated
  item is not evidence of recency; consider dropping items with no
  parseable date.

- **O19. `check_ground_truth` matches reference senators with
  `name LIKE '%fragment%'` and `.first()`** with no ordering — fragile to
  same-surname collisions and nondeterministic if two rows match. Consider
  bioguide-anchored references.

### Statistics, minor

- **O20. `score_calibration._spearman_rho`** uses the `1 − 6Σd²/(n(n²−1))`
  formula with average ranks — approximate under heavy ties (clamped int
  scores tie often); the Pearson-of-ranks form is exact. Drift detection
  only, not scores.

---

## Cross-referencing notes (recorded live data vs. algorithms)

- `SCORE_AUDIT.md` Step 1's overall-score SQL used the stale five-dimension
  weights (now fixed) — any audit run since v6.0 that trusted that column
  ranked senators by a formula the platform no longer uses.
- `MIN_STDEV` still gates `score_funding_diversity` (unweighted since
  v6.5) — harmless, arguably still useful since the FD signals feed FI.
- The v6.6/v6.7 verification protocol (`docs/v6.6_live_verification.md`)
  is sound; note that fix #2 above (recent-roll-call dedupe) will change
  measured break rates on the next pipeline run — expect IV movement and
  re-run the ground-truth gate + stdev floors, per that protocol.
