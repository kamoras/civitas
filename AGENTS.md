# AGENTS.md — Civitas Project Guide

## Working Agreement — finish the whole job

**Fix everything you find. Do not defer known problems to a follow-up PR.**

When you are working a bug, the deliverable is the *behavior*, not the diff you
first imagined. If investigating turns up more defects in the same feature —
related bugs, a regression your own fix introduced, an accessibility violation
in the widget you are already editing — those are yours too. Fix them in the
same change. "Known limitation, tracked separately" is not an acceptable way to
close out work here; a follow-up PR that nobody opens is just a bug you decided
to keep.

This applies specifically to:

- **Regressions you introduce.** Review your own change adversarially before
  calling it done, and verify the assumptions your fix rests on rather than
  assuming them. Cheap indirect evidence often isn't evidence: a fix once
  looked correct because no extra network request appeared, when the duplicate
  call was really being served from `cachedFetch`'s client cache.
- **Adjacent defects in the same component.** If the tab bar you are fixing
  also has broken arrow-key navigation, fix that too.
- **Every code path with the same shape.** One `<Link>` corrected out of eight
  leaves the bug live on seven pages. Grep for the pattern and fix the class,
  not the instance — and where the correct form looks odd enough that someone
  might "clean it up" later, put it behind a named constant with a comment
  explaining why (see `ACTION_CENTER_HREF` in `src/lib/routes.ts`).

Verify against a **production build**, not just `next dev` / a dev server —
several of the bugs this rule exists because of were invisible in development
and only appeared under `next build` (see Frontend conventions). State plainly
what you tested and what the result was; if something genuinely cannot be fixed
here, say so explicitly with the evidence, rather than filing it away.

## Project Overview

Civitas is an AI/ML political transparency platform that scores U.S. senators,
House representatives and presidents on how well they represent constituents,
and records Supreme Court justices' votes (justices are not scored: since
justice v3, 2026-10, no measure separates loyalty to the appointing president
from career timing for an individual justice — `docs/research/justice-scores.md`). It aggregates voting records, campaign finance, floor
speeches, judicial opinions, and party platforms from official government
sources, then analyzes them using embedding-based classification, roll-call
party alignment (content-based where no roll call exists), and deterministic
scoring. It also features an Action Center
that surfaces trending civic issues from news feeds, auto-detects ongoing
national concerns as trackable monitors, builds a year-in-review timeline, and
shows each story in its sources' own words with recommended actions. Everything runs
locally on a single self-hosted device with zero cloud AI calls.

## Architecture

- **Frontend**: Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS — port 3000 (not published to the host under Swarm — see Deployment)
- **Backend**: FastAPI (Python 3.13), SQLAlchemy ORM, SQLite — port 8000 (same). In production, two services from one image: the read-only API and the pipeline (see "Performance conventions" under Backend conventions)
- **LLM**: LFM2.5-1.2B-Instruct via llama.cpp (`ghcr.io/ggml-org/llama.cpp:server`, in-stack, overlay-network only) or Ollama (not bundled — bring your own, port 11434)
- **Embeddings**: sentence-transformers, two models in-process — Snowflake Arctic-XS
  (classification) and all-MiniLM-L6-v2 (search index + similarity gates)
- **Vector Store**: sqlite-vec (`vec0` virtual tables in `/data/vectors.db`) — replaced
  ChromaDB in the 2026-07 migration; see `pipeline/vector_store.py`
- **Keyword Index**: SQLite FTS5 (`explore_fts`, external-content over
  `explore_documents`) with BM25F ranking — the second retrieval channel behind
  Explore search; see `pipeline/lexical_index.py`
- **Site search**: one entity index of everything with a page (`/data/site_search.db`,
  SQLite FTS5 + trigram + exact identifier keys), rebuilt by the pipeline
  process when its sources move; the header's autocomplete and `/search`
  read it with no model — see `app/site_search.py`
- **Deployment**: Docker Swarm (single-node), `docker stack deploy` for zero-downtime rolling updates, nginx (in-stack) reverse proxy with caching
- **Branches covered**: Senate (100 senators), House (435 representatives), Presidents (historical + modern), Supreme Court (9 justices)
- **News Feeds**: RSS parsing (NPR, PBS, BBC, The Hill, Politico, Roll Call) + Google Trends + Bluesky trending for Action Center; 41 per-state newsrooms for election races
- **Action Center**: Three tabs — Today (the day's issues, what a reader can do about each, open comment periods), Ongoing (national monitors: auto-detected ongoing concerns, and those closed in the last 60 days), Archive (year-in-review timeline). Elections live on `/elections`, find-your-members on `/politicians`
- **Nominations**: every presidential nomination this Congress from Congress.gov (daily, `fetch/nominations.py`), with status, committee, hearings, time to confirm and each roll call that names it (`/congress/nominations`)
- **Elections**: State index → per-state ballot page (federal contests + statewide ballot measures, quoted verbatim) → race/candidate detail with FEC financials

All services, models, and data run on-device. No data leaves the server.

## Repository Layout

```
civitas/
├── backend/
│   ├── app/
│   │   ├── api/                 # FastAPI route handlers (senators, representatives, presidents, justices, explore, action, admin, health)
│   │   ├── services/            # Business logic (senator_service, representative_service with paginated vote APIs)
│   │   ├── pipeline/
│   │   │   ├── fetch/           # API clients (Congress.gov, FEC, GovInfo, Senate.gov, Oyez, BLS, Federal Register,
│   │   │   │                    #   per-state ballot-measure readers)
│   │   │   ├── transform/       # Data normalization, embedding-based industry classification
│   │   │   ├── analyze/         # Bill analysis, scoring, cross-referencing, action center LLM synthesis, justice appointer estimates
│   │   │   ├── assemble/        # Scorecard builder + validator
│   │   │   ├── senate_pipeline.py, house_pipeline.py  # FETCH→TRANSFORM→ANALYZE→ASSEMBLE+SAVE per chamber
│   │   │   ├── member_lifecycle.py  # Roster reconciliation + removal of departed members (never presidents)
│   │   │   ├── stock_pipeline.py  # STOCK Act trade-disclosure ingestion (sibling phase)
│   │   │   ├── vector_store.py  # sqlite-vec + sentence-transformer model management
│   │   │   └── lexical_index.py # SQLite FTS5 keyword index (BM25F) over explore docs
│   │   ├── forecast/            # The pre-election map's poll ranges — outside pipeline/ and
│   │   │                        #   apart from scoring (see "The Senate and House map")
│   │   ├── live_results/        # Election-night count sync, seat-flip issues, results posts — outside
│   │   │                        #   pipeline/ so it never moves the analysis-code fingerprint
│   │   ├── models.py            # SQLAlchemy ORM (Senator, Representative, KeyVote, Justice, NationalMonitor, TimelineEntry, etc.)
│   │   ├── schemas.py           # Pydantic response schemas (incl. PaginatedVotesSchema)
│   │   ├── database.py          # DB engine + session management
│   │   ├── config.py            # Pydantic settings from .env
│   │   ├── config_definitions.py # Enums, weights, industry codes (single source of truth)
│   │   └── main.py              # FastAPI app with lifespan hooks
│   ├── tests/                   # pytest test suite (see `pytest tests/` for current count)
│   ├── migrations/              # Alembic revisions for the main database (see its README)
│   ├── requirements.txt
│   ├── pytest.ini
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── app/                 # Next.js App Router pages (action [issues/monitors/timeline tabs, labelled Today/Ongoing/Archive],
│   │   │                        #   elections [state index, states/[ST] ballot, [raceId] detail],
│   │   │                        #   politicians [directory + per-member profile], bills, compare, explore, leaderboard,
│   │   │                        #   about, changelog, accessibility, environmental, feedback, admin)
│   │   ├── components/          # React components (action, elections, checker, president, justice, explore, home, effects)
│   │   ├── hooks/               # Custom React hooks
│   │   ├── lib/                 # API client (with paginated vote fetching), utilities
│   │   └── types/               # TypeScript type definitions
│   ├── package.json
│   └── Dockerfile
├── docker-compose.yml
├── docker-compose.swarm.yml     # Swarm-only overlay (production stack deploy)
├── docker-compose.dev.yml
├── check-and-deploy.sh          # Cron poller: builds images + docker stack deploy
├── nginx/                       # Dockerfile + static config (in-stack reverse proxy)
├── .env.example                 # Template for environment variables
├── AGENTS.md                    # This file — project design principles and developer guide
└── README.md
```

## Core Design Principles

### 1. Dynamic learning and mathematical methods — never hardcoded rules

**All classifications and metrics in the data pipeline must be computed
mathematically and via learning.** This is the foundational principle of the
project. Hardcoded text is acceptable only as an absolute last resort for
documented data-format conventions (e.g., FEC form values like
"SELF-EMPLOYED"), never for classification decisions.

When you encounter a classification problem (donor type, industry, bill
policy area, party alignment, junk name detection, skip entity detection,
stance direction, procedural detection, etc.), the solution must use one
of these approaches:

- **Embedding cosine similarity** against natural-language prototype
  descriptions (zero-shot classification; Yin, Hay & Roth 2019)
- **Batch embedding similarity** for filtering large sets (employer names,
  memo texts) against skip prototypes — vectorized for performance
- **k-Nearest Neighbor voting** in sentence-transformer embedding space
  (Cover & Hart 1967), using the learning store as the reference set
- **Fuzzy string similarity** via SequenceMatcher ratio (Ratcliff &
  Obershelp 1988) for name-matching tasks like self-funded detection
- **Margin-based decontextualization** for cases like "[Industry] PAC"
  where a secondary signal (e.g., PAC naming context) can be detected
  semantically and the runner-up classification preferred
- **Self-training** via the learning store (Yarowsky 1995): high-confidence
  classifications become labeled examples for future runs
- **Statistical formulas** with shrinkage toward neutral for scoring metrics
- **LLM inference** for tasks that require natural language synthesis from
  unstructured input: Action Center claim location (verbatim-checked),
  national-monitor titles and descriptions (checked against the issues that
  opened the monitor; whether one opens, joins or merges is similarity alone,
  and its category is the issues' policy area), timeline period summaries, locating the actor and
  predicate in a race's coverage for its post (`election_bluesky.py`,
  verbatim spans), and on-request Explore document summaries
  (`POST /api/explore/{id}/summary`, a write). Spotlight, issue, trend and
  early-signal posts are fixed templates around stored figures or verified
  ledes, and the justice profile carries no generated summary. Never a score, and never ballot content (§7). (Per-senator/rep
  narrative generation and promise evaluation used to be LLM-based; both
  were removed in 2026-07 after live audits found the output unreliable
  regardless of prompting approach — see `cross_reference.py`'s and
  `policy_alignment.py`'s module docstrings.)

**Never add hardcoded keyword lists, regex patterns, suffix checks, or
if/else string-matching heuristics to make classification decisions.** If you
find yourself writing `if name in {"DIRECTOR", "PRESIDENT"}: skip`, stop —
that should be an embedding similarity check against a prototype. If you need
to distinguish corporate PACs from political PACs, that should be the semantic
classifier, not a set of business suffixes. If you need to strip "PAC" from
entity names, that should be margin-based decontextualization using an
embedding-based PAC naming context detector.

Four narrow, disclosed exceptions exist today (bill stance direction's
tier-0 verb check, industry classification's hotel-brand tier, donor
classification's PAC-suffix/payment-processor tier, and the claim
composer's capitalised-actor rule — see README "Classification Strategy"
for the full list and each one's empirical justification). Each earns its
place by the same bar: a *specific, measured* embedding-model failure mode,
documented in code at the point of definition, running ahead of a genuine
embedding classifier that still handles everything the pre-filter doesn't
catch — never a replacement for one. The first three are precision
pre-filters; the actor rule admits named parties ahead of a kNN vote that
decides every span it refuses, and stays only because the vote alone,
measured against it on the same labelled set and the same replay, lost
more real claims than it recovered (`post_composer.is_acting_party`). That bar is deliberately high. "I
think this keyword would help" does not clear it; a comment citing a
concrete before/after measurement does.

The correct way to handle a new classification need:

1. Define a natural-language prototype description that captures the semantic
   signature of the category
2. Add it to the relevant prototype dict (e.g., `_SEMANTIC_PROTOTYPES`,
   `INDUSTRY_DESCRIPTIONS`, `DONOR_TYPE_PROTOTYPES`, `_STANCE_PROTOTYPES`)
3. Let the embedding model do the classification via cosine similarity
4. Calibrate thresholds empirically by checking scores against known examples
5. The learning store will accumulate results over time, improving accuracy

Prototype descriptions are the **input** to the mathematical classification
system, not hardcoded rules. They define what the embedding model searches
for in the same way that training labels define what a supervised model
learns — they are the minimal human knowledge that seeds the system.

#### Classification tier strategy

The tiered strategy follows computational parsimony (Jurafsky & Martin 2023):
use the cheapest sufficient method first, reserving expensive techniques for
the residual.

| Tier | Technique | Used For |
|------|-----------|----------|
| 1 | FEC structured metadata / SEC SIC / learning store | Unambiguous entity types, a PAC's industry where its registration or its sponsor's SEC record states it (`fec.structured_industry`), previously classified entities |
| 2 | Sentence-transformer cosine similarity | Whether a claim's actor span the capitalised-actor rule refuses is a party that can act (`post_composer.ACTOR_PROTOTYPES`, threshold refit daily on data-supplied non-actors), industry, donor type, bill policy, party alignment, stance direction, procedural detection, commemorative detection, skip entity detection, employer filtering, memo transfer detection, category normalization, whether two Action Center claims restate one fact (calibrated on labelled pairs, `claim_restatement_*.json`) |
| 2b | SVD / PageRank on cosponsorship matrix | Ideology scoring (Tauberer 2012), legislative leadership (Brin & Page 1998) |
| 3 | k-Nearest Neighbor in embedding space | Remaining unclassified donors and bills; Action Center relevance (against stored feed articles labelled by their publishers' own filing and by the officials and agencies they name, refit daily: `app/policy_relevance.py`); committee routing (against hand-labelled issues, blended with tier-2 jurisdiction prototypes) |
| 4 | LLM (LFM2.5-1.2B-Instruct) | Natural-language text only — Action Center claims, monitor titles and period summaries, race-post spans, Explore document summaries (full list in the bullet above) |

When FEC metadata is ambiguous (e.g., entity_type "COM" could be a corporate
employee PAC or a purely political PAC), the system defers to tier 2
(embedding similarity) rather than guessing. Each tier can only promote to
the next — never skip tiers or substitute hardcoded rules.

### 2. Self-correcting learning store with version-aware invalidation

The persistent learning store (SQLite `learned_classifications` table)
accumulates labeled classifications across pipeline runs, implementing a form
of self-training (Yarowsky 1995, ACL). High-confidence classifications from
prior runs become labeled examples for future runs, reducing latency and
improving accuracy over time without manual intervention.

**Version-aware artifact management** prevents stale data from persisting
when analysis code changes. At pipeline start, `_compute_analysis_code_hash()`
computes a SHA-256 fingerprint of all analysis-relevant source files
(everything in `app/pipeline/` except `fetch/`, plus `config_definitions.py`).
Each file is hashed as its docstring-stripped AST (`_normalized_source`), so
editing a comment or docstring does not count as a code change — only code,
string constants (prototypes, prompts) and thresholds do. A short, tested
exemption list covers what cannot affect classification or scoring:
`_NOT_ANALYSIS_PATHS` (the holdings and trade ingests, filer matching, the run-coordination
modules, the election run's orchestration, the LDA bill-name matcher
`analyze/lobbying_records.py`, the Explore summary prompt `analyze/prompts.py`,
the votes-with-the-president rate (`analyze/presidential_support.py`,
`analyze/sap_position.py`),
the modules that word and publish posts, the Action Center's hourly run
and the trend posts' gates and record matching (`_ACTION_CENTER_PATHS`),
which keep no learned data) and
`_DISPLAY_ONLY_NAMES` (display-only constants such as `HOLDING_CATEGORIES`),
both in `senate_pipeline.py`. This fingerprint is
compared to the stored hash from the last pipeline run:

- **Same hash** → all learning data is preserved (learning store, analysis
  cache, sqlite-vec reference corpus). The self-training system accumulates
  knowledge across same-version runs.
- **Different hash** → all three persistence layers are cleared so updated
  algorithms start fresh. The API cache (raw Congress.gov / FEC / GovInfo
  responses) is never cleared — it reflects source data, not processing logic.

The learning store upserts always overwrite prior entries (no confidence
guards), ensuring the current run's classifications take precedence. Within
a single pipeline run, this is harmless because learning store lookups
short-circuit re-classification of already-seen entities.

A donor's industry is taken from kNN only when all seven neighbours agree
(`donor_classifier_ai._KNN_K`, `min_agreement`): measured 2026-10-09 on
record-labelled PAC sponsors the prototype tier abstains on, a plurality
vote was right 29% of the time at every similarity floor, and a wrong
industry is worse than an unclassified one.

kNN's own outputs (`source == KNN_SOURCE`) are stored for lookup but are
**never used as kNN reference examples** — only labels from an upstream tier
(FEC metadata, rules, prototype similarity) vote. Otherwise one run's guess
becomes the next run's evidence and errors compound across runs.

The `normalize_learning_store()` function runs at the start of the kNN phase
to fix case inconsistencies. Stale or hallucinated category labels (e.g.,
"LEGAL", "SPORTS") are mapped to valid industries via embedding cosine
similarity against the industry description prototypes — there is no hardcoded
alias table. This prevents label fragmentation from diluting kNN vote weights.

### 3. Deterministic, auditable scoring

The representation sub-scores — Funding Independence, Constituent Alignment
and Legislative Effectiveness (weighted, `SCORE_WEIGHTS`), plus the
informational Funding Diversity (Promise Persistence, which has had no input
since campaign-promise tracking was removed, is published as null) — use
transparent statistical formulas with no LLM input. All formulas include
inline academic citations.

Key mathematical properties:
- **Linear shrinkage**: Scores regress toward 50 when data is sparse (e.g.,
  a senator with 1 campaign promise gets a score near 50, not 0 or 100). Two
  exceptions: Constituent Alignment's vote part shrinks toward the party's
  measured typical score (its scale tops out at the seat's norm, so 50 is
  below average); Legislative Effectiveness's bill component doesn't shrink
  by bill count (a member's bills are the whole record, not a sample) — its
  leadership component is still pulled toward 50 for short tenure. The rate
  is the count confidence below — fixed, not estimated from the population's
  variance, so do not call it Bayesian or empirical Bayes — except in
  Constituent Alignment's position part (v6.27), which shrinks toward 50 at
  a measured rate: it scales a roll-call position's distance from the seat's
  expected position, when the position rests on n votes, by min(1, w(n) /
  w(200)) (a full record at 200 votes, a convention), w(n) = n / (n + n0):
  the measured slope of a member's full-record position on their thin one, a
  full record counting 1. n0 is fitted on Voteview's own adjacent-Congress
  records with drift per transition
  (`scripts/calibrate_position_confidence.py`), one curve for both chambers
  over every Congress since 1989. The structure is chosen by predicting
  forward, each transition's n0 from the earlier ones only (its drift,
  shared by every structure, from its own full pairs), as the weight is
  used, by the one-standard-error rule (Hastie, Tibshirani & Friedman 2009;
  a convention, adopted in review after four other rules had been tried, two
  leaving one member out, then two forward): the simplest structure within
  one standard error of the best's error. The latest era's curve, since the
  110th Congress (a split by convention), has lower error than one curve by
  0.066 (standard error of the paired difference 0.034, under two standard
  errors; over the last three transitions alone about one standard error,
  computed from `last_three`), and a window of the last six transitions
  predicts about as well (0.011 lower error than the era curve, standard
  error 0.012), but both are well within the best's own standard error
  (0.229), a curve per chamber predicts no better, and with the split or the
  window's width chosen from earlier Congresses alone each has lower error
  than one curve only within the noise. A rerun decides again. Nothing in it
  follows the sitting Congress. It is a reliability weight, not a count
  threshold.
- **Count confidence**: `min(n / threshold, 1.0)` ensures minimum sample
  sizes before trusting extreme scores
- **State-adjusted baselines**: Constituent Alignment scores account for Cook
  PVI (partisan lean of the state) so voting with party in a deep-red/blue
  state is not penalized the same as in a swing state
- **Shannon entropy**: Funding diversity uses information-theoretic entropy
  to measure concentration across industry sources

### 3a. Calibrated constants are generated data, never hand-typed

Any scoring constant derived from real data — a regression coefficient, a
population mean, a percentile-based ceiling, a saturation point, a raw
population/count figure — must never be a Python literal a human
copy-pasted from a script's printed output into source code. That exact
pattern drew repeated review pushback (PR #152): the resulting numbers
are opaque ("where does 21.4 come from?"), and duplicating the same
underlying data as a second hardcoded copy in another file lets the two
drift apart silently.

The correct pattern, established by `_district_pvi()` /
`app/data/district_pvi.json` (`scripts/fetch_district_pvi.py`) and
`_state_population()` / `app/data/state_population.json`
(`scripts/fetch_state_population.py`):

1. A one-off script under `backend/scripts/` computes the value(s) from
   real data (a public API, a DB query, a scrape of a stable public
   source) and writes them to a checked-in JSON file under
   `backend/app/data/`, with a `_source` field documenting exactly where
   the data came from and when it was generated.
2. The scoring module reads that JSON file through a small cached loader
   function (module-level `_foo_cache`, lazy-loaded on first call — see
   `_district_pvi()` for the exact shape), never as an inline dict/tuple
   literal typed directly into the `.py` file.
3. Any other file that needs the same underlying data (an audit script,
   a different scoring dimension) reads the same JSON file or calls the
   same loader — never a second hardcoded copy of the same numbers.
4. The data refreshes itself. Register the file in `app/data_refresh.py`'s
   `DATA_FILES`: a daily job (`scheduler._data_refresh`, lease
   `DATA_REFRESH`, outside the nightly chain) runs the generating script
   itself as a low-priority subprocess on the file's cadence (`due`),
   writes the candidate to a scratch file, gates it (`gate`: shape,
   plausibility, drift against the copy in use), and only a candidate that
   passes replaces the copy in use, on the data volume (`/data/<name>`,
   provenance in `_refreshed`: when, the command, the content's sha256). A
   failure keeps the last good copy and opens an ops alert for that file
   (`data-refresh-<file>`), resolved by its next good refresh. Readers go
   through `data_refresh.read(name)` (refreshed copy, else the bundled one,
   re-read when either changes, from either process); the bundled file in
   git stays the pre-first-refresh fallback and the reproducible baseline,
   and committing a regenerated one is still how a method change ships. A
   script the job runs takes an output path and reads no `/data` copy of
   its own output. Files a script can't regenerate unattended — a new
   survey whose question ids change, a model-selection refit that is a
   method decision, a source behind research-only dependencies, a source
   that needs hand-researched corrections — get a `stale` check instead,
   which alerts (`data-stale-<file>`) when a newer version should exist and
   says what a person has to do. A refresh that moves scores is a data
   refresh within the same method (announced by an ops notice and recorded
   in `_refreshed`), not a new `ALGORITHM_VERSION`; a refit that changes a
   method's structure or a classifier's threshold is a version change and
   goes through review, which is why those calibrations alert rather than
   refit themselves.

   `district_pvi.json` is fetched by the pipeline, not only by the
   script, but from **pinned** sources (2026-09):
   `app/data/district_pvi_sources.json` names, per Congress, one
   immutable revision of Wikipedia's "Cook Partisan Voting Index"
   article whose citation states the Cook release and the map it
   describes. `app/pipeline/fetch/district_pvi.py` fetches exactly those
   revisions (Supplementary, weekly; before a House run when the file is
   missing, predates pinning, or is not at the current pins), gates them — every seat of the House
   Clerk's apportionment exactly once, the revision's own prose
   counts must match its table and its stated median must be the table's
   exactly (a pin may declare a `median_tolerance` only with a written
   `_why_median_tolerance`); a redrawn Congress must be identical to its
   base outside the redrawn states, and in each redrawn state differ
   somewhere while keeping the state's mean district lean (a redraw moves
   voters between a state's districts, not out of it) — and writes every
   Congress's table under `congresses`, with the sitting Congress's as
   the top-level `districts` member scoring reads. Member scoring must use
   the lines the member was *elected on*; the elections pages use the
   lines of the Congress the election seats (`district_pvi_for_congress`),
   or — for a Congress nobody has pinned yet, which is every next cycle
   once an election's results window closes
   (`election_phase.active_election`) — the newest pinned lines before it.
   Do not go back to scraping each district's live infobox: it did that
   until 2026-09, and when nine states redrew for 2026 editors swapped in
   new-map values district by district, leaving member scoring on a
   silent mix of two maps (TN-9 read R+9 for a member elected in a D+23
   seat). The Supplementary run compares the live article with the newest
   pin and raises an ops alert on a difference (once per distinct
   difference); it never ingests it.

   "Sitting" is `settings.CURRENT_CONGRESS` — the same value the scored
   windows read (roll-call sessions, bills, Voteview ideal points), so a
   House run can never score one Congress's votes on another's lines. It
   starts as the Congress in office when the process starts (noon ET on
   Jan 3 of an odd year, the 20th Amendment's hand-over — not midnight,
   which gave a process started that morning the new windows on the old
   lines), and `app.config.scoring_congress` advances it to the Congress
   in office at the start of every background job and holds it for that
   job — `app.background.start_writer` (every scheduled job, every trigger,
   the startup rescore) and `writing()` take the hold themselves, so no
   writer can start without one. The hold is a ContextVar that every read
   of the setting in the job's context answers (including asyncio tasks,
   work handed to `asyncio.to_thread` and `contextvars.copy_context().run`;
   a plain `threading.Thread`, `loop.run_in_executor` or
   `ThreadPoolExecutor.submit` does not inherit it and reads the
   process-wide value — hand such work over with `asyncio.to_thread`) — so a process
   running across Jan 3 moves at its next job with no restart, and a job
   running across noon stays on one Congress. The read-only API process
   runs no jobs; it advances the value on its liveness loop. An environment pin is the one thing that stops the switch: it
   freezes the district lines as well as the windows, and
   `check_current_congress_staleness` alerts once it falls behind. It exists only for re-running an archived
   database; **the production `.env` must not set `CURRENT_CONGRESS`**
   (`.env.example` leaves it commented out, and a test keeps it that
   way). Every House run — the nightly
   chain's and each triggered one (`/api/admin/pipeline/trigger`,
   `/trigger-house`, the token trigger) — goes through
   `run_house_on_sitting_lines`, which takes the `DISTRICT_LINES` lease,
   settles the lines (`_ensure_sitting_lines`), and holds the lease until
   the House run returns; the weekly refresh takes the same lease, so the
   lines never change under a House run and a second House trigger is
   refused rather than refreshing twice (a House run that finds a refresh
   holding the lease waits for it, up to `REFRESH_WAIT_S`, rather than
   skipping — and past that, the House link is reported skipped naming the
   refresh (`run_tracker.LINES_HELD_TOO_LONG`), which the chain does not
   wait out and retry as it does another run's hold, so the skip alert
   comes after one `REFRESH_WAIT_S`, not two (`app/pipeline_chain.py`); main's startup Constituent Alignment rescore takes
   the same lease for its House part and waits for a refresh the same
   way, between passes that hold nothing — no lease, no writer — so an
   admin data reset is never refused for the wait). The pipeline process
   releases a lease a killed holder left (`district_pvi.
   release_orphaned_holds`, beside the startup run-row sweep) once it has
   gone `ORPHAN_RECHECK_S` (ten minutes, `lease.STALE_S`) without a beat, so
   a deploy mid-run doesn't block House runs for the lease's hour (a House run, trigger or
   the startup rescore refused by a lease still being checked waits for
   the check, `district_pvi.waits_for`, through its retries while the
   database is locked, `ORPHAN_RETRY_S` × `ORPHAN_ATTEMPTS`, then an ops
   alert — from the startup pass's first failed delete, and only for the
   leases that pass read, so a lease a live House run takes later is never
   waited on); that a leftover holder is
   dead rests on the pipeline service's stop-first update order
   (`docker-compose.swarm.yml`); the missed-beat check is a second guard,
   its window long enough that a live holder whose beats stall behind
   another writer for minutes keeps its lease. A job still holding the outgoing Congress after a
   newer job has moved to the new one (the process value, or the file's
   lines while that Congress is in office by the clock — a file ahead of
   the clock is a removed pin's, and is settled over) neither
   refreshes nor settles the lines, and its House step is skipped
   (`district_pvi._superseded`, `run_tracker.SUPERSEDED`) — otherwise it
   would switch the site back to the old map until the next job. Each stored House score records the Congress
   whose lines it used (`Representative.district_lines_congress`), and the
   score breakdown is recomputed on the same district lines
   (`district_pvi.lines_of`) — while a run is part-way through a switch,
   after one that failed, and for members who left when the lines changed.
   That settles the district table and nothing else: the breakdown still
   reads the Constituent Alignment reference
   (`/data/constituent_reference.json`) and
   `/data/member_ideal_points.json` as they are now, and a House run
   rewrites both before its scoring loop, so for a member it hasn't
   rescored yet (mid-run, after a failed run, or departed) the breakdown
   can still differ from the stored score. That drift predates the
   per-Congress lines and is not fixed by them. So the first House run in a
   job that starts after that noon (with the default 03:00 UTC schedule,
   the nightly chain that starts that evening; a trigger before it would
   be first) switches member scoring to the new Congress's table from what is
   already on disk — no fetch, no restart — *if* the sources file has an
   entry for it, and a pin advanced or a Congress added in the sources
   file (a correction, a court ruling) is fetched by the next House run,
   since the check compares each table's pinned `revid` with the file.
   Without an entry for the sitting Congress, scoring stays on the newest
   pinned lines, nothing is fetched for it, and one ops alert per
   Congress asks for the entry. `scripts/fetch_district_pvi.py --congress N`
   regenerates the bundled pre-first-ingest fallback through the same
   code.

   Beside the pinned Cook figures, `app/data/district_lean.json`
   (`scripts/build_district_lean.py`, 2026-10) is a lean Civitas computes
   itself with Cook's formula from open inputs only — VEST 2020 precinct
   shapefiles (CC BY), MEDSL 2024 precinct returns (CC0), Census blocks
   and BEFs; never Dave's Redistricting (share-alike) or a news-site
   scrape — per Congress's lines, with MEDSL's errors corrected solely
   from certified canvasses listed in
   `app/data/district_lean_corrections.json` (a correction that can only
   come from behind a wall marks the district unavailable). It is display
   only (`app/district_lean.py`): nothing in scoring reads it. Its build
   gates are its contract; when a new error turns up, add a correction
   entry with its official source, not a code path.

   Better still, when the population a value describes is the one the
   pipeline is scoring, measure it in the run itself. Legislative
   Effectiveness's reference (per-stage bill totals, chamber median credit,
   average baseline, spread, current majority party) is computed from the members each run
   is about to score (`compute_les_reference`), persisted to
   `/data/les_reference.json` for the API's breakdowns, with
   `app/data/les_reference.json` (`scripts/calibrate_les_credit_scale.py`)
   as the pre-first-run fallback. Funding Independence's references
   (the Senate's PAC share by state population and the chamber spreads,
   v6.26, the top-donor and industry-concentration deciles, and the Senate's small-donor
   baseline by state population, v6.24) work the same way (`compute_funding_reference`,
   `funding_reference.json`, `scripts/audit_pac_ratio.py`), and so does
   Constituent Alignment's per-party expected break rate by seat lean
   (`compute_constituent_reference`, `constituent_reference.json`,
   `scripts/calibrate_constituent_reference.py`; its swing-seat bend is
   kept only when it predicts the party's members better out of sample,
   `select_opposed_kink`, not by a count of opposed seats); all go through
   `pipeline/analyze/population_reference.py`. A value frozen on one date
   can't track a quantity that accumulates over a congress.

   Measured in the run, but only on enough of the chamber. A step that fails
   part-way leaves the run's inputs for a subset, and a subset a failure
   leaves is not random — a read stops at a point in the roster (2026-10-15:
   the House sponsorship read covered the first 105 of 433 members; the LES
   reference measured on them alone raised 61 members 5–11 points, 133 of
   the 149 who rose Republicans). Each reference records the share of the
   chamber it was measured on (`coverage`, `roster`), and below the floor in
   `app/data/reference_coverage.json` (per chamber and reference, from
   `scripts/calibrate_reference_coverage.py`: the smallest share at which a
   reference measured on a random subset or the roster's first members moves
   neither the 95th-percentile member by more than their own noise nor a
   party's average by more than its standard error) a run carries the last
   reference measured on full coverage instead of measuring, without
   rewriting it, and raises an ops alert naming it and its date
   (`live_references`, `ChamberReference.last_good`). With no full-coverage
   reference on file, LES is measured on every member as scored (the bills
   read plus the stored bills carried for the rest). Regenerate the floors
   after a scoring change.

This also applies to constants that are themselves the *output* of a
fitting script (regression coefficients, saturation points derived from
a residual stdev, min/max clamp ranges) — if a script prints "paste this
value into score_calculator.py," that script should instead write the
value into a JSON file the code reads, exactly like population/PVI data.

A plain hardcoded constant is still fine for a genuine, non-calculated
fact with a clear citable source that doesn't drift from run to run
(e.g. a physical/legal constant like an FEC contribution limit). The
state PVIs were once the example here, hand-typed from Cook; they are
now computed (`scripts/fetch_state_pvi.py` → `app/data/state_pvi.json`,
from the FEC's certified 2020 state totals and MEDSL's 2024 state file, with the per-state
counts stored beside the figures so a test recomputes every one). The
bar is specifically about **calculated** values — anything a regression, an average, or a percentile produced —
which must trace back to a generating script and a data file, not a
comment asserting "trust me, I ran a script once."

A calibration fitted to **hand labels** (the Action Center's relevance,
clustering, claim and actor gates, the trend-post gates, committee routing,
the member-photo check) can't regenerate itself, and doesn't need to when
its labels change: each records the hash of its labels and a test fails
until it is refit. What it can't see is the stream moving away from its
labels. Each such gate reports what it decides (`calibration_drift.observe`,
counted in memory and written after each hourly refresh), and a nightly
check compares the gate's pass rate over the last four weeks with its own
first four weeks under the calibration in use, alerting
(`calibration-drift-<gate>`) when it has moved by 15 points or more and far
outside sampling noise: the cue to label a fresh sample and refit. A new
calibrated gate is added to `calibration_drift.GATES` and observes its
decisions.

### 3b. Search ranking is measured, not asserted

Explore search combines four rankers — semantic kNN, BM25F keyword, recency,
and citation-graph PageRank — with weights in `config_definitions.py` under
"Explore search ranking". Those weights are the tuning surface for search
quality, and the same discipline that applies to scoring constants applies
here: **do not change a ranking weight because a result set looks better.**

`backend/scripts/evaluate_explore_search.py` is the instrument. It reports
MRR and Recall@k for each channel and for the fusion, broken out by query
style (title / paraphrase / identifier / the document's own FR citation,
document number, docket and Record citation / rare-term / passage), and how
often an identifier the corpus does not hold gets documents back, against
the live index. Run it before and after, and say what moved. Every probe is built
from the document's own words (the "paraphrase" style is a bag of its most
frequent words, not a rewording), so keyword leads semantic by construction
and identifier probes are near zero for any encoder: compare the semantic
channel with itself across a change, not with keyword.

Relevance judgments there are derived by known-item retrieval, not
hand-labelled — a document is pulled from the corpus, a plausible query for
*that* document is built from it, and the measurement is where it lands. That
is honest about exactly one question (can the engine find a document someone
is looking for) and deliberately silent about whether a broad topical query
returns a good *set*, which needs real labels. Don't quote it as evidence for
the second question.

The same rule covers the ranking's structure, not just its constants. A new
signal has to be something the corpus can actually supply — the citation graph
earns its place because federal documents cite each other by published
identifier, and it degrades to a no-op when they don't. A signal only one
document type can earn belongs in the fusion as a *partial* ranker (documents
it can't score contribute nothing) rather than as a full ordering, or it
silently demotes every document that had no way to earn it.

The same discipline covers site search (`app/site_search.py`): its scoring
weights and its "straight to the page" thresholds are fitted by
`backend/scripts/calibrate_site_search.py` on known-item probes built from
the index (half the entities fit, half reported) and read from
`app/data/site_search_calibration.json`. A wrong jump is worse than a
results page: the thresholds allow no wrong jump in any probe type on the
fit half. Rerun it when the index gains a kind or aliases.

### 4. Content-based party alignment

A bill's party alignment comes from **how the parties actually voted on it**
whenever a roll call exists, and from its content (embedding similarity to
party platform positions) only when none does (`refine_with_vote_data`). The
consumer is the voted-with-party computation, and "did this member break with
their party" is defined by the parties' real split: a bill whose content
reads partisan but passed with both party majorities must not count as a
party-line vote. (Content used to win over a bipartisan split; a 2026-06
audit found that pinned every House member's score near 87–89.) Content
alignment still drives bills with no roll call. Partisan depth (the lean and
its per-area breakdown) credits each policy area a bill touches with the bill's
own lean, from the roll call where there is one, housekeeping motions aside:
reading each area's content label instead counted a Yea on a bill both parties
passed as a partisan vote.

One procedural exception, read from the chamber's own result field and
never from vote counts: a **majority leader's** Nay on a motion the chamber
recorded as rejected, which their own party supported, is not a break, nor
is their Yea on a motion recorded as carried over their party's opposition. The
leader switches to the prevailing side so that they can move to reconsider (Senate
Rule XIII; House Rule XIX cl. 2). It is scored as no party signal
(`MAJORITY_LEADER_TITLES` in `normalize_votes.py`), and only for votes cast
during the member's tenure in that office (`leadership_tenures.json`). The
Speaker and the minority leader are never exempted.

Partisan depth (how strongly a member of either chamber leans D or R) is computed primarily
from the senator's actual voting record: for each policy area, the ratio of
Yea/Nay votes on D-leaning vs R-leaning bills determines the area's alignment.
Campaign promise text analysis is a secondary enrichment signal.  This follows
Poole & Rosenthal (1985) in using roll-call data as the primary indicator of
ideological position.

When available, the SVD-derived ideology score (from tier 2b sponsorship
analysis) serves as a prior for the partisan depth calculation, in a
linear blend whose weight decreases as the senator accumulates vote data:
`data_confidence = min(partisan_vote_count / 15, 1.0)`. With 15+ votes,
the ideology prior has zero weight; with fewer votes, it regularizes the
estimate toward the senator's revealed cosponsorship ideology. (Shrinkage in
the Efron & Morris 1975 sense, but at a fixed rate rather than an estimated
one.) The prior is first mapped onto the vote-lean scale by a line
fitted over the chamber's full-data members each run, and the depth label
(deep / moderate / centrist) is the member's tercile within their own party
— both in `finalize_partisan_depth`, which runs over the whole chamber after
the per-member pass. No named senator anchors either.

### 4a. Vote matching for multi-word names

Senate.gov roll call XML uses multi-word last names (e.g. "De la Rosa",
"Van Doren", "Hale Morrow").  The pipeline extracts the original last
name from the Congress.gov "LastName, FirstName" format during member
normalization and stores it as `lastNameForVoteMatch`.  Unicode accents are
stripped (NFD decomposition) so "Núñez" matches "Nunez" in the XML.

### 4b. Member ids are slugs, renamed in place

A member's id (the `senators` / `representatives` primary key and the URL
`/politicians/<id>`) is `first-last` from Congress.gov's "Last, First Middle"
name, ASCII-folded (`app/member_ids.member_slug`) — the name the member
goes by where the source marks it: the quoted nickname when the name string
has one (`Doe, Henry C. "Hank"` → `hank-doe`), else the first given name
(initials and Jr./Sr./II–IV passed over); and the whole surname before the
comma, or the member detail's `lastName` when that is a longer multi-word
form ending with it. The detail's `firstName` is not used: it holds a
familiar form for some members without the name string marking it, and
would rename members whose ids are already right. One URL
namespace covers both chambers: the same bioguide id keeps one id in both
tables, and a second person with the same slug gets the state code
appended (then the bioguide id); whoever holds an id keeps it.

Ids are re-derived every run, so `assign_member_ids` runs on each chamber's
roster before anything writes a member by id: it matches stored members by
**bioguide id**, never by id, and renames a changed one in place — the row,
every foreign-key child (found from the metadata, since SQLite has no ON
UPDATE CASCADE), and the references no foreign key covers (the same ones
`member_lifecycle._purge_member_traces` clears) — recording the old id in
`member_id_aliases`. Never let an id change read as a member leaving: that
retires the row and purges its history after `RETIREMENT_GRACE_DAYS`. A new
table or JSON field that stores a member id must be added to
`member_ids._move` and to the purge. The API resolves an old id
(`resolve_member_id`) and serves the member under the current id; the
profile page answers it with a permanent redirect (308).

### 5. Config as single source of truth

All dynamic enums, category codes, industry definitions, score weights, and
policy areas are defined in `config_definitions.py`. The frontend fetches
these from `GET /api/config`. Never duplicate these definitions.

### 6. Current term, not career

Scores are windowed to a member's current term, not their whole career — a
member who did great work a decade ago and has coasted since shouldn't get
credit for it on every run.

"Current term" is defined as **the current congress** (`settings.CURRENT_CONGRESS`,
a 2-year window), for both chambers, for votes/bills/sponsorship/effectiveness
(`fetch_significant_bills`, `_recent_congresses_only`, the Senate roll-call
session list — all in `fetch/congress.py`/`senate_pipeline.py`). This was a
deliberate simplification, not an oversight: Congress.gov's `terms` array is
a list of 2-year congresses served, not real 6-year Senate term boundaries —
verified live against a senator who finished a colleague's term via special
election then won a full term with zero visible seam between the two in the
API response. There's no Senate "class" field either, and deriving true term
boundaries from FEC election history is fragile (a sitting senator can
already be fundraising for their *next* re-election, which would misread as
their current term starting early). Redefining "current term" as "current
congress" sidesteps that fragility entirely and is *stricter* than a literal
6-year term (resets every 2 years, not 6) — it pushes harder on the "no
resting on laurels" goal, not softer.

Besides funding (below), the flank-break rule (`party_line_record`, v6.27)
is a narrower exception that never scores a past position. It reads the last
Congress's positions only to tell which side of their party a defector sits
on: for everyone until the new Congress's Voteview section passes its gates;
after that, for a member whose new record has no count or fewer than
`prior_until_votes` votes (`app/data/position_confidence.json`: 200, a full
record) if their last record was full; and, whatever the last record, for a
member the new section gives no usable position (a stated choice, not
measured). Never, once the new section is in, for a member who switched
parties during the new Congress (`switched` in the section) or whose party
differs between the two sections (`parties`; sections written before this
change record none, so that check starts with the next Congress's section):
their last positions were cast in another party. A position recorded under
the other major party is never read for the member, in any section, nor
counted in that party's mean (a stated choice). The 200 and the bar for
replacing it are conventions: the calibration would adopt a shorter switch
only if it placed members on the right side better out of bag in 95% of
resamples, and too few thin records have the rule's shape to measure one.
Only the Congress just before is kept, and only by a section written since
v6.27, so the prior is first read in the Congress after the one v6.27
shipped in.

**Funding is the main exception**: Funding Independence and Funding Diversity
window to the member's **most recent completed election only**
(`select_recent_elections` in `fetch/fec.py`, `n=1`: general election day
has passed — a re-election campaign still in progress is the *next*
mandate's, not the current one), not the current congress. Itemized donor
detail covers that election's full period (six years Senate, two House —
`election_period_cycles`), and every funding share is taken over
contributions, not receipts (`normalize_finance.summarize_election_totals`).
Both chamber runs pass the chamber with the candidate record, or the
seat-winning bound (`seat_winning_floor`) is off and the window can reach an
old losing run. FEC's candidate totals can omit the race that won a House
seat; for a member sworn in when the Congress convened, the House run reads
it from the principal committees' cycle totals (`fec.with_seat_election`). Senators legitimately raise little money in the 4 non-election
years of a 6-year term — a strict 2-year funding window would go near-empty
most of the time for reasons that have nothing to do with coasting. Tying it
to their current mandate's campaign instead fixes the same staleness problem
without that sparsity trap.

This needed no new schema: since `ScoreSnapshot.date` already exists, the
congress a snapshot falls in is a pure function of its date
(`congress_first_year(n) = 1789 + (n-1)*2`, the 1st Congress convened in
1789 — a fixed historical fact, not a lookup table). The score trend chart
(`ScoreTrend.tsx`) marks congress-boundary crossings the same way it already
marks `ALGORITHM_VERSION` changes, so a score reset at the start of a new
congress reads as intentional, not a bug.

A member who joined mid-congress (a special election) has had less of that
window, so Legislative Effectiveness prorates its bar by the share of the
congress they have served (`congress_exposure`, v6.23), using the sworn-in
date from the House Clerk's member list (`fetch/house_clerk.py`). No source
gives a senator's date, so the Senate is not prorated.

Narrower windows mean less data backs each dimension by design, not because
coverage got worse — `calculate_confidence`'s vote/bill thresholds are
recalibrated accordingly (see `score_calculator.py`), and `ground_truth.py`'s
population-distribution checks are the backstop that would catch a real
collapse. The gate derives every expectation from the current population's
own raw data (rank consistency against FEC/roll-call metrics, point-mass and
snapshot-history distribution checks — no named reference members, no
hand-typed score ranges, per principle 1/3a), so the identical checks run
for both chambers in `senate_pipeline.py` and `house_pipeline.py`.

### 6a. Evergreen: no code is about this cycle, this Congress, this president or this Court

The site has to keep working, unedited, through the next election, the next
Congress (noon ET, Jan 3 of every odd year), the next president (noon ET,
Jan 20 after a presidential election), a vacancy on the Court, a special
election, a party switch and a mid-decade redistricting. Every new feature
answers these before it merges:

- **Which Congress?** `settings.CURRENT_CONGRESS` (from the clock, held per
  job by `scoring_congress`), or `congress_for_election(cycle)` for the
  Congress an election seats. Never a literal `119`, never a default
  argument naming one, never "119th" in copy (the frontend's
  `currentCongress()` / `congressOrdinal`, or the API's own field).
- **Which election?** `election_phase.active_election()` (its `.cycle`,
  `.election_day`, phase), never `next_election_day()` directly and never a
  year or "November 3" literal. Cycle-specific external names (an article
  title, a results URL, an FEC cycle) are built from the cycle by rule.
- **Who holds office?** Read from the official source every run: members
  from Congress.gov's current roster (`member_lifecycle` for departures),
  the president from the roster with the Federal Register cross-check
  (`check_sitting_president`), justices from Oyez's sitting list
  (`retire_departed_justices`, `court_roster_changed`), leadership and
  committees from congress-legislators. Never a typed name, majority party
  or count ("the nine justices").
- **A fact that is law, not a cycle:** store the rule (`{"every": 4,
  "from": ...}`, "28 days after the general"), not this cycle's date, so the
  next cycle computes its own.
- **Data that genuinely needs a new source each cycle** (a calendar, poll
  closing times, a new map's lines): regenerate it automatically on its
  cadence where the source can be found by rule, gate it, keep the last good
  copy, and alert ahead of the cycle when it can't — a file keyed to one
  cycle must never go quietly empty or quietly reuse the last cycle's values.
- **Copy:** labels come from the API or the clock helpers; a measured
  number in prose is dated ("measured October 2026"), which keeps it true.
- **Prove it at future dates:** tests at the next hand-over instants
  (`freeze_utcnow`, and the full fast suite under
  `scripts/frozen_clock_plugin.py`, see Testing): Jan 3 of the next odd year
  either side of noon ET, the next primary and general season, election
  night, Jan 20 after a presidential election, and a Court vacancy.

### 7. Ballot content is quoted, never generated

Anything that describes what is on a voter's ballot — measure titles,
summaries, fiscal statements, yes/no descriptions — is stored and rendered
**verbatim from its source**, with the source linked and its drafter named.
No LLM output reaches this surface at any stage, and `BallotMeasure` has no
model-written column for one to land in. Do not add one.

The reason is specific, not squeamishness. `grounding.py` verifies that the
tokens in generated text appear in the source material. It has no
representation of predicate *direction*, so a summary reading "a YES vote
repeals this tax" — when the official text says approval **retains** it —
passes every check in the module, because every word it used is in the
source. That error class is undetectable by anything in the codebase, and it
is the error class that changes votes. `ungrounded_electoral_claims` is worse
than merely unhelpful here: its context regex only fires when `ballot` /
`voters` are *absent* from the source, and every ballot-measure source
contains both, so it returns `[]` on 100% of these inputs.

Corollaries that follow from the same rule, all enforced in code:

- `yes_means` / `no_means` are lifted from the state's own framing or left
  NULL — **never derived from the title**. The obvious derivation ("yes
  enacts it") inverts on a veto referendum, where approving retains the law
  under challenge.
- Voter deadlines follow the same rule (`app/data/election_calendar.json`): each read from the state election authority's own page or its statute, quoted, and re-checked by `scripts/build_election_calendar.py` — never an aggregator, never a date typed without its rule, and never an item the script could not verify (it refuses to write one; a state with no readable official source is `unread` and lists no dates). Where to register is the same file's `register` link, fetched by the script and kept only on an official host (one behind a bot wall kept, worded unconfirmed; vote.gov where a state has none). A "N days before" date is counted by the script and shown beside the rule; status is computed on the state's own clock; a passed deadline is rendered as passed, never dropped.
- Absence must be able to say *which* absence it is. `MeasureCoverage` carries
  `confirmed_none` vs `not_yet_covered` / `ingest_failed`, because an empty
  measures section on a page about a state's ballot reads as "nothing to
  research" — the same null-is-not-zero discipline `Candidate.last_financials_sync`
  applies per field, applied at the collection level.
- Removed measures are rendered as removed for a grace window, not deleted.
- The same line holds for the weekly source crawl's robots.txt: a bot
  wall's challenge there is a wall, never "no rules" (RFC 9309 would read a
  plain 4xx that way), so nothing is crawled past it
  (`state_source_crawler.WALLED`). Walls already investigated go in
  `state_candidate_sources.json`'s `_robots_walls` with their evidence, so
  the crawl's ops alert names only a new wall or an unreachable file.
- The source is the state's own document, but not always the state's own
  server. Where a state's whole site sits behind a bot wall (Georgia's
  Cloudflare, Nevada's Imperva), the reader raises `SourceBlocked` and the
  document is read from a county election office's republication of it
  (`republished_by` in the registry). That copy gets every check the
  state's own would; a state page that was read and refused never falls
  back to one, since that would publish past the refusal; and the card
  names both ("as republished by Eureka County Clerk-Recorder"). Getting
  past the wall itself is not an option: it is the state saying no.
  A wall is not a dead link, though. A state's voter lookup the nightly
  link check is refused on (`ballot_lookup._check` → `blocked_at`) is still
  linked, worded as unconfirmed ("This site blocks automated checks, so we
  can't confirm it's up") with USAGov's directory beside it; only a link
  that answers 404, or lands on its site's homepage, falls back alone. That
  is enforced in the HTTP layer for every source: the client forgets the
  cookies a challenge response sets (`http_client.is_bot_challenge`), and
  `fetch_with_retry` never retries a challenge. Until 2026-10 a retry sent
  the challenge's cookie back and got through one state's wall unnoticed.
- Scope is stated as content, not as a footnote: the API enumerates what a
  statewide page omits (`omits`) and the page renders it above the measures.
- **`omits` is a live description, not a fixed disclaimer.** Each entry is
  dropped once that gap genuinely closes for that state — the statewide
  executive offices line went this way in 2026-09, and a state with
  `statewideCoverage.status == "covered"` no longer sees it. A list that
  keeps disclaiming what the page now shows stops describing the page and
  becomes boilerplate a reader learns to skip past, including past the
  entries that are still true. When you close one of these gaps, removing
  its `omits` entry is part of closing it, not a follow-up.
- A gap that is closed *by checking and finding nothing* is also closed.
  `confirmed_none` drops the omission too: "this page omits Governor
  contests" implies one is being withheld, when the honest claim is that
  the state elects none this cycle — which the section says in its own
  words.

If a plain-language layer is ever revisited, the bar is a check that can
detect polarity inversion and dropped qualifiers — which is not an extension
of `grounding.py`, it is a different kind of check.

### 8. The visitor is never asked who or where they are

Civitas requires no account, stores no visitor data, and **does not ask a
visitor for their address**. Getting a reader to the races that affect them
is a navigation problem, solved with selection UI, not an input problem
solved by collecting an identifier.

This is stricter than "don't persist it". A resolve-only, never-logged
address box shipped on the state ballot page in 2026-08 and was removed in
2026-09: the storage was genuinely clean, but asking at all is the wrong
shape for a project whose entire premise is that a voter can research their
ballot without handing anything over. The box was also redundant — the
district rows already showed each district's counties and its sitting
representative, which is what a person can actually recall unprompted, so
the replacement is a client-side filter over those (`matchesDistrictQuery`
in `frontend/src/lib/elections.ts`).

What this rules in and out:

- **Out:** any field asking for a street address, ZIP, precinct, or
  geolocation permission to personalise what's shown (the one exception
  is the state below); any per-visitor identifier retained across requests
  to remember such a choice.
- **In, exactly this:** the reader's state, asked once in the header
  (`components/layout/YourStatePicker.tsx`, `hooks/useYourState.ts`) and
  used by every page that needs a state (the member directory, an issue's
  contact list, the calendar, compare, the homepage's links, `/elections`).
  It is held for the open tab only, in `sessionStorage`; never
  `localStorage`, a cookie or anything kept across visits; and it reaches
  the server only as the `?state=` a page already puts in its own URL. Its
  "Use my location" button never runs by itself: on a click it asks the
  browser for a position and places it in a state on the device, against
  the site's own state shapes (`lib/stateFromPoint.ts`, the election map's
  `/data/states-10m.json`). The coordinates are not sent, logged or
  stored; only the state is kept, as if picked from the list; a refusal
  falls back to the list. On-click, on-device, state only, per tab:
  nothing finer than a state (no county, district or town from a
  position), and nothing kept past the tab. `YourStatePicker.test.tsx`
  holds both (no request carries coordinates; session storage only).
- **In:** filtering, maps, and drill-down over data the page already has;
  explicit, unremembered navigation choices (the town selector, the state
  picker); linking out to the official lookup for a reader who wants a
  precinct-exact answer. Where a district needs to be findable, give it
  place names a person already knows and let them filter: counties, and
  the cities, towns and communities in it, for a U.S. House seat
  (`county_district_crosswalk.json` and the "120" map of
  `congressional_district_places.json`, the coming election's lines, for
  the ballot pages; `county_district_crosswalk_sitting.json` and the "119"
  map, the lines the sitting members were elected on, for "who represents
  me" on `/politicians` — in a redrawn state the same number is a
  different district, so each surface names its map and never borrows the
  other's; places by `scripts/build_district_place_crosswalk.py`), towns, communities
  (incorporated places and Census Designated Places) and counties for a
  state legislative one (`state_leg_district_crosswalk.json`, a block-level
  join documented in `scripts/fetch_state_leg_crosswalk.py`). Building
  those crosswalks is real work, and doing it is the price of not asking.
- **In:** following Civitas without an account. The Atom feeds (`/feeds`)
  are pulled, and a topic, state or one followed thing is chosen by which
  URL a reader subscribes to, so there is no subscriber list to keep. A
  push channel that would need one (email, web push, per-server webhooks)
  is out, for the same reason as an address box. A member, a bill, an
  agency and a national monitor each have a feed built from stored rows
  when asked for (`app/entity_feeds.py`: fixed templates, no model, ids on
  what the entry is, never on a slug or URL), nginx-cached like the rest.
  A page offers its most specific feed first — `pageMetadata({ feed })` in
  `src/lib/site.ts` (a state's ballot page its state's feed, `/congress`
  the Congress feed, a member's profile, a bill or an agency its own, an
  issue page its monitor's or else the Action Center feed) — and links it
  visibly (`FollowFeed`); a new page with a feed of its own should do the
  same.
- **In, server-side only:** the Census geocoder and Google Civic's
  `voterInfoQuery`, called from the pipeline (and, for the town selector,
  from the API, cached 12 hours) with **our own** fixed,
  publicly-known building addresses (`town_directory.json`,
  `state_candidate_sources.json`'s `house_addresses`) to resolve which
  district a *known* place sits in. Every visitor who picks the same town
  sends the identical request; nothing visitor-specific leaves the server.
  That distinction — our address, not theirs — is the whole line.

**What a reader searches for is never kept.** The site search
(`/api/search`, `/api/search/suggest`, `/search`) and Explore carry the query
in the URL, so: nginx's access log has one format, `civitas_private`, with no
query string and no referrer (`test_nginx_config.py`); uvicorn's access log
drops query strings (`main.DropQueryString`); search answers are
`Cache-Control: private` (the reader's browser may keep them, nginx's disk
cache never does); the `/search` page fetches with `no-store` (Next's data
cache is a file); and no log line names a query. Keep a new search-like
endpoint the same way.

The one thing counted about visitors — daily unique visits — uses an HMAC of
the IP under a random salt that exists only for the current UTC day and is
then deleted (`api/visits.py`, `VisitSalt`). A permanent key would not do:
the IPv4 space is small enough to enumerate, so anyone holding the key could
recover every stored IP. With the salt gone, nobody can.

A visit is a browser running a page, counted from requests the site needs
anyway. The frontend's proxy (`src/proxy.ts`, `lib/visitSignal.ts`) relays
two kinds to `track_visit`: a page request (`Sec-Fetch-Dest: document`),
which is only *held* in the throttle store under the client's daily key,
and a same-origin fetch from a running page (`Sec-Fetch-Dest: empty` — in
practice Next prefetching the links in view, which every page does within
seconds of loading), which counts what that client holds
(`_confirmed_paths`). Pages opened inside the app after the first are sent
by the page (`NavigationBeacon`): a click on a prefetched link reaches no
server, verified under `next build`. `track_visit` drops headless Chrome
(`HeadlessChrome`, the default for Playwright-driven agents) and crawlers
that render pages in a real browser engine and say so in their User-Agent
(`_SELF_DECLARED_CRAWLER`: Bingbot, Applebot, OAI-SearchBot, GoogleOther —
14 of the 17 addresses that sent the beacon in 16 hours on 2026-10-10).
When judging whether counting works from nginx logs, leave those out too:
GoogleOther's User-Agent is a Chrome-on-Android one ending in
"(compatible; GoogleOther)", and it makes `_rsc` requests by the hundred
(none of which reached the counter as a router request), which on 2026-10-11 read
as "real browsers ran the page and weren't counted" when no ordinary browser
had visited. Because a broken confirmation path reads as zero visitors
rather than as an error, `check_visits_confirmed` (every API worker, on the
liveness loop) raises an ops alert when a whole watched day holds pages and
confirms none; its window is a day because readers who run the page are few
enough that a quiet night is normal.

Two things this rules out. Never count from one request's headers: counting
requests without fetch metadata made crawlers two thirds of the unique
visitors on 2026-10-01, and the `Sec-Fetch-Dest: document` check that
replaced it was beaten within a week by a crawler that sent it from
thousands of rotating addresses and never ran a script (~7,000 of 7,357
visitors on 2026-10-08). And never count from a request of the page's own
that exists only to count: the beacon that counted every page from 2026-10-09
matched EasyPrivacy's `/track-visit?` rule, on by default in uBlock Origin,
Brave and AdGuard, and 0 of 4 search- and social-referred readers in six
hours sent it (19 visitors counted on 2026-10-10). A reader who blocks
tracking requests has chosen to; don't rename an endpoint to get past the
list. Their visit and first page still count, because the requests that
count them are the ones the site's links need; their later pages don't. Next
strips `RSC`, `Next-Router-Prefetch` and `_rsc` before the proxy runs, which
is why the router request is known by its fetch metadata, and why the proxy
can't tell a prefetch from a click.

Page-load timings (`POST /api/track-timing`, `PageLoadTiming`) are counted too,
and deliberately carry even less: the browser reports one cold load's Navigation
Timing, and the server keeps only a counter per (day, route template, metric,
bucket, device class) — no hash, no User-Agent, no exact duration. The endpoint
reads nothing about the caller at all. The device class (desktop, tablet,
mobile; 2026-10) is decided by the page from its own pointer and screen size
(`deviceClass` in `LoadTimingBeacon.tsx`) and sent as one of those three words,
which validation enforces — reading the User-Agent there would have been the
first header the endpoint ever read. A browser under automation
(`navigator.webdriver`) sends nothing. Keep it that way: a timing row that could
be joined to a `SiteVisit` would turn a performance histogram into a per-visitor
log.

Taps on an issue's action links (`POST /api/track-action`, `ActionTap`) are
counted the same way: one counter per (day, issue public id, kind), where the
kind is one of a fixed set validation enforces (`ACTION_TAP_KINDS`: picking a
state, calling, writing, copying the call script, a comment page, a committee,
a bill). Not which state, member or number, and nothing about the caller
reaches the row: the address only keys a per-client rule in the RAM throttle
store (`throttle.client_key`), so a tap counts once per client, issue and kind
a day and a client's taps are capped per hour; the issue must exist. An issue
view (`IssueView`, which decides the Trending flag) likewise counts once per
client per issue an hour (`ISSUE_VIEW_PERIOD_S`). The state picker on an issue (`ContactYourMembers.tsx`) is an
explicit, unremembered choice from a list: the state goes in the URL that loads
its members (`GET /api/action/issues/{id}/members?state=`) and nowhere else, and
the House town filter runs in the browser over district lists the page already
has. Keep both that way: a tap row that named a state would be a coarse
location log per issue.

The admin dashboard's headline traffic figures are the two that measure people:
**confirmed visitors** (`SiteVisit` from `CONFIRMED_VISITS_SINCE`, the first
whole day a visit counted only once the browser ran the page; null before it,
when the stored count was mostly automation) and **first-paint loads** (cold
loads whose browser reported a first contentful paint, from the timing
histogram). Raw `uniqueVisitors` stays in `GET /api/admin/visitor-stats` for
history, but is not shown as a visitor count.

Public API and MCP use (`ApiRequestCount`) is counted the same way: one
counter per (day, endpoint, channel, status), recorded by the public
router's route class (`api/public.py` `_CountedRoute`) and written by the
visit consumer, with nothing about the caller. A request refused as invalid
(422) also counts which parameter broke which rule (`ApiRejectionCount`:
"doc_type", "literal_error"), never the value sent, so a run of rejections
says what to fix. It is not a visit and never reaches the visitor figures;
it has its own admin tab. The channel header
that marks an MCP tool call is cleared by nginx on every outside request.

The site is served through Cloudflare, which by default rewrites pages on the
way out: it injected its Web Analytics beacon (loaded from
`static.cloudflareinsights.com`) and a JavaScript Detections script that sets
a one-year `cf_clearance` cookie, found 2026-09-30 in a real browser (curl
never runs either). The dashboard is not changed for this; instead nginx marks
every page `Cache-Control: ..., no-transform`
(`$cache_control_no_transform` in `nginx/civitas.conf`, enforced by
`test_nginx_config.py`), which Cloudflare honours. Don't drop it. What
Cloudflare still does (relays each request and sees the IP, caches static
files, Network Error Logging, a clearance cookie only if it challenges a
visitor) is disclosed on `/about/data#cloudflare`; check that section still
matches when anything about the proxy changes.

**A page requests nothing from another host** — and images are where that
slipped. Member portraits used to load straight from bioguide.congress.gov
(justices' from Oyez, an issue's news photo from the outlet), so every
visitor's IP went to each of them, and about one portrait in nine failed
outright under a full directory page. Every photo is now served from this
site at `/photo/<kind>/<id>` (2026-10): the API hands out those paths
(`app/photos.py`; JSON-LD makes them absolute), and the frontend route
(`app/photo/[kind]/[id]/route.ts`, `lib/photos.ts`) fetches the picture
server-side, from a source the backend names by kind and id through
`GET /api/photo-sources/…`: a justice's or issue's stored URL (Oyez, the
outlet), and for a member (`bioguide`) one of two official photos: the
headshot on the member's Congress.gov record (`depiction`, read from the
record the pipeline stored in `api_cache`) or bioguide.congress.gov's
portrait (`bioguide_photo_source`). Neither source is reliably the
head-and-shoulders shot (2026-10): bioguide's file is, for some members, a
full-length painted committee-chair portrait, mostly background as an
avatar, and for a few others it is the member's own depiction that is a
full-figure or wide shot. So the choice is measured, nightly in the
stock-trades run (`pipeline/fetch/member_photos.py`, stored in
`member_photos`): OpenCV's YuNet face detector measures each candidate's
face height over its shorter side, and bioguide wins only when the
depiction's face is below a head-and-shoulders share and bioguide's is
clearly larger. Both numbers are fitted, not typed:
`scripts/calibrate_member_photos.py` writes them to
`app/data/member_photo_calibration.json` from a hand-labelled comparison of
every current member's two photos (labels kept there with each image's
hash; rerun when labelled photos change). OpenCV runs only in a child
process started for the night's batch (`pipeline/fetch/face_detection.py`,
which nothing in `app/` imports; `tests/test_member_photos.py` checks no app
process loads `cv2`), so a native fault can't reach page serving or the
pipeline. The model is YuNet from the OpenCV Zoo (MIT), vendored in
`app/data/`; source and hash are in the calibration file. The endpoint reads
the stored choice, and a member without one (a departed
bill sponsor, or before the first run) gets the depiction-first rule. A
depiction is used only on Congress.gov's `/img/member/` path and only with
the attribution Congress.gov gives it; one without falls back to bioguide,
so the credit on `/about/data#privacy` stays true. Many
headshots are taller than 4:5 (up to 2:3), so member avatars crop at
`object-[center_25%]` (the OG card at `objectPosition: "center 25%"`),
which keeps the head in a square where a centred crop clips it. It
takes an id, never a URL, so it is not an open proxy; add a kind there for
a new picture, never a URL parameter. An issue's photo URL still comes from
an RSS item, so each kind is fetched only from its own hosts
(`PHOTO_SOURCE_HOSTS` in `app/photos.py`, mirrored by `PHOTO_HOSTS` in
`lib/photos.ts` and held equal by a test), https on the default port: the
pipeline drops a source elsewhere, the endpoint refuses one, and the route
follows redirects itself and refuses any hop off the list. Hosts are
checked by name, not resolved address (Node's fetch has no connect-time
hook without adding undici). nginx caches the route
(`photo_cache`, a week unread, stale on error) and rate-limits its cache
misses only, through the api-misses hop, sized so a cold directory page is
paced rather than refused. `tests/test_same_origin_photos.py` and
`frontend/src/lib/sameOriginImages.test.ts` fail on an absolute image URL
on another host; fonts, scripts, styles and map data are self-hosted, and
`/about/data#privacy` says so — check it when that changes.

The same line covers the visitor's own browser. The Action Center used to
remember a "your state" pick in `localStorage` (also read by the compare
page), a "log my action" diary with streaks, and which issues the browser had
voted on; all three were removed in 2026-09, and `ForgetLegacyStorage` clears
what earlier visits left behind. Browser storage is for a per-tab convenience
at most (`sessionStorage`, as the admin token and the header's state choice
use), never a record of the visitor.

A feature that can only work by asking where the visitor lives is a feature
this project doesn't ship. State the resulting limitation as content (see
§7's `omits`) rather than closing the gap by collecting an address.

## Data Pipeline

The pipeline runs nightly (configurable via `PIPELINE_CRON_SCHEDULE`) or can
be triggered manually via `POST /api/admin/pipeline/trigger`.

`scheduler.py`'s `_nightly_pipeline()` runs FIVE pipelines as a chain, in
order: **Senate → Supplementary → House → Stock trades → Election.** They
run one after another (the Pi's memory), but **none waits on the one before
it succeeding** (`app/pipeline_chain.py`, 2026-09): each reads whatever the
database holds, so a link that is skipped, fails or crashes is alerted
(`nightly-skipped-*`; `nightly-crashed-*` for a crash or a `failed` /
`no_data` run) and the next runs anyway. Until then any of those ended the
chain, and Stock trades and Election once went 19 nights without running
behind a Supplementary failure. One chain runs at a time
(`pipeline_chain.claim`): a trigger of any of its pipelines is refused (409) while one
is going, and a nightly run that finds a triggered chain going leaves the
night to it. A link skipped because another run holds the machine (another
run of that pipeline, or a member pipeline holding Stock trades off) waits
until no pipeline run is live (`scheduler.pipelines_running`, which stops
counting a run once it is stale: no progress for `QUIET_PIPELINE_TIMEOUT`,
6h, or older than `STALE_PIPELINE_TIMEOUT`, 24h — `run_tracker.row_is_stale`
for run rows and run locks, `PipelineRunTracker.hung` for the in-process
flags, both reading the same progress writes), then is tried once
more and that attempt is what gets reported: the other run may have been a
single senator, or have failed, so it never stands in for the link. The one
skip that ends a chain is a data reset holding the database, which every
later link would hit too. A killed process still ends its chain; the
staleness watch below is the backstop for that. Both member pipelines run
the analysis-code-hash invalidation (`invalidate_stale_analysis`), since
House may run after a failed Senate. `POST /api/admin/pipeline/trigger`
(and the token-authenticated `/api/pipeline/trigger`) runs the same
five-link chain, reported the same way, so a manual run can recover any of
them (a single senator or a fetch-only run is the Senate pipeline alone).
`pipelineChainIsRunning` in
the admin status keeps `check-and-deploy.sh` from restarting between two
links. `ops_alerts.check_pipeline_staleness` still watches for a pipeline
with no successful completion in `PIPELINE_STALE_ALERT_DAYS`;
`check_pipeline_overrun` watches the opposite case of a run that started
and is taking too long; its alert names the active step, that step's
detail and how long ago the run last wrote progress, so a slow step that is
working reads differently from a hung one. A step that loops over thousands
of upstream reads reports through `ProgressTracker.heartbeat` (written at
most every `HEARTBEAT_INTERVAL_S`, with the step's request count and
rate-limit wait appended, each write one short commit): the House
sponsorship step once spent six hours on a cache warm-up of about 19,000
bills with an empty detail and a frozen elapsed time (2026-10-16). The
admin status computes a running row's elapsed time from its start, never
from the stored value. Staleness is progress, not age (2026-10): a run that
has written nothing for six hours (the longest step measured to write
nothing until it ends took 102 minutes) is hung or dead, and its lock is
taken over; one still writing keeps it until the 24h ceiling. The jobs that
step aside for a pipeline (the bill refresh for House, the election syncs
for Election) and the chain's liveness (`pipeline_chain`, which counts any
run's progress write) use the same rule with their own windows. A new long
step that writes nothing until it ends must heartbeat, or past six hours it
reads as hung.

Outside the chain, the **agency sync** (`fetch/agencies.py`,
`scheduler._agency_sync`, `AGENCY_SYNC` lease) reads the Federal Register's
agency list and USAspending's toptier budgets once a week (tried hourly;
each source is read when a week has passed since it last stored). USAspending names no
Register agency, so the two are joined by name similarity at a threshold
`scripts/calibrate_agency_crosswalk.py` writes to
`app/data/agency_crosswalk.json` (§3a); an agency under it is kept and
listed unmatched, never guessed.

**What makes a night long is upstream fetching, not the analysis-code
fingerprint** (measured 2026-10-14: the fingerprint changed on 19 of the 21
nights before, and its slowest two nights were both unchanged-hash nights).
Most steps wait on rate limits (Congress.gov 1.2/s, the FEC 0.25/s), so
request count is run time. Two rules keep it from spiking (2026-10):

- **A bill's own records** (`bill-`, `-actions-`, `-cosponsors-`,
  `-summaries-`, `-titles-`) stay fresh until the bill changes. The hourly
  bill refresh reads Congress.gov's list of bills updated since its last
  pass, marks each listed bill's records stale
  (`bill_refresh.mark_bill_records_stale`), and records since when its
  passes have joined without a gap (`coveredSince` in its marker). A
  current-Congress record written since then is kept up to
  `BILL_RECORD_BACKSTOP_HOURS` (30 days) while the refresh has run within
  `PIPELINE_CACHE_TTL_HOURS`; otherwise the plain TTL applies
  (`congress._bill_record_get`). With one TTL for all of them, about 20,000
  of these were refetched every third or fourth night.
- **Per-entity fetches expire by key** (`api_cache_get(..., spread=True)`:
  member and bill records, FEC candidates, committees and receipts, GovInfo
  packages): each key expires at its own point in the last third of its
  TTL, never later, so entries written in one run fall out of step instead
  of all coming due on one night. A new high-volume per-entity fetch opts
  in the same way.

A bill's actions that can't be read fall back to the last history read
(any age) before raising `CongressUnavailable`, which discards the run's
whole stage and sponsorship step.

Each member pipeline executes in 4 phases per chamber, defined in
`senate_pipeline.py`/`house_pipeline.py`:

1. **FETCH** — Pull senators, House representatives, bills, roll-call votes,
   bill cosponsors, floor speeches, FEC financial data, Supreme Court cases,
   presidential records from Congress.gov, Senate.gov, GovInfo, FEC, Oyez,
   BLS, and Federal Register APIs
2. **TRANSFORM** — Normalize financial records, classify industries and donor
   types using FEC metadata + embedding similarity, batch-detect skip employers
   and transfer memos via embedding prototypes
3. **ANALYZE** — Classify bill policy areas, stance direction, and party
   alignment via embeddings; detect procedural bills via embedding similarity;
   compute legislative leadership (PageRank) and ideology (SVD) from
   cosponsorship networks; classify remaining donors via kNN; cross-reference
   donors with votes; compute representation sub-scores. Fully deterministic
   — no LLM call (campaign-promise analysis and per-senator narrative
   generation were both LLM-based here until removed in 2026-07 for
   unreliable output; see `cross_reference.py` and `policy_alignment.py`).
   The justice phase (separate) makes no LLM call either, and scores no one:
   it stores each justice's raw appointer estimate and its standard error
   (justice v3).
4. **ASSEMBLE + SAVE** — Build scorecards for senators and presidents, and
   records for justices; validate via `assemble/validator.py`; persist to SQLite

Between TRANSFORM and the rest, both chamber pipelines run
`member_lifecycle.py` against the roster they just fetched:

- Anyone in the database but absent from the roster is marked
  `is_current=False` with a `left_office_date`. Reversible — reappearing on
  the roster restores them and clears the clock, and a serving member on the
  roster who still carries a departure date has it cleared. Skipped (with an ops alert)
  when the roster comes back implausibly small, so a truncated Congress.gov
  response can't retire a chamber, and skipped for single-member
  `senator_filter` runs.
- Anyone whose `left_office_date` is more than `RETIREMENT_GRACE_DAYS` (180)
  old is deleted, along with their child rows and the four references no
  foreign key covers. The grace period outlasts a House special election, so
  a seat mid-refill still shows who held it.

**Presidents are never reconciled or removed, and neither is the Court** —
both functions take an explicit chamber and reject anything but
`senate`/`house`. A former president is permanent site content. The Court
has its own, narrower reconciliation: the justice step marks a justice no
longer on Oyez's sitting list inactive (`retire_departed_justices`, never a
delete, refused below the Court's quorum of six) and the Supplementary run
checks that list nightly so a confirmation or departure doesn't wait for the
weekly refresh (`court_roster_changed`). Relatedly,
the Senate/House leaderboards rank only currently-serving members, while the
presidential leaderboard ranks the *historical* field and excludes the
sitting president (see `president_service.get_president_leaderboard`) — for
that office, comparison against predecessors is the only meaningful ranking.

**Trend posts ("On the record", `analyze/trend_poster.py`)** run after each
hourly Action Center refresh (`scheduler._post_trends`, under the same lease).
They read Bluesky's public `getTrends` (`fetch/trending.fetch_bluesky_trends`:
`None` is an outage — nothing recorded, held or dropped — never "no trends"),
keep every listing as `TrendObservation` rows, and post at most one trend a run,
two an Eastern day, four hours apart, inside the posting window (held outside
it, dropped if the trend has left the list by the next run), the same record at
most once in 14 days and never a record an issue post already carried. A trend
posts only on a **pinned match** to an official record (`trend_evidence.py`):
an Action Center issue at cosine ≥ 0.5 that shares a distinctive entity with
the trend's own name (described trends only); an Act's full title (the whole
capitalised run must be the record's title, or the trend gives the bill
number — a shorter name inside a longer title, or a qualifier like "EU" in
front, is another act); a nominee's confirmation vote, only when the
trend began within a day of the vote (`trend_evidence.NOMINEE_WINDOW`); or a named
member's own recent bill on the topic — never a person alone. Records must be
dated on or before the trend's start plus two days, and a record more than 30
days older than the trend counts only when the trend's name names it; a vote
with no measure title and no bill is never a record. Only a trend Bluesky's
getTrends files under "politics" goes to matching (its own category, read as
published: `trend_relevance.POLITICS_CATEGORY`; the kNN relevance vote over
350 hand-labelled topics it replaced was removed 2026-10-16). The other
votes (`trend_relevance.py`, `political_violence_examples.json`/`loaded_language_examples.json`)
are calibrated by `scripts/calibrate_trend_gates.py` into
`trend_gates_calibration.json`. The trend's name must pass a neutrality gate
(`grounding`'s electioneering, editorializing and advocacy-as-fact checks plus a
loaded-language vote); a tragedy (the tragedy vote) is eligible with an
official record, or with a confirmed Action Center issue reported by two or
more outlets whose lede compose() verified (`trend_poster.tragedy_issue_refusal`;
the post quotes that lede, credited to its outlet, under the neutrality gate
and the loaded-language vote). The political-violence vote was removed
2026-10-16: agents' shootings, deaths in detention and attacks on the
Capitol are in scope, and the news answers them before any vote or document. Every word is template text, a
stored record field, or the trend's name quoted and attributed to Bluesky — no
model. Selection is party-blind (rank weight × match strength); each trend gets
one `TrendCandidate` audit row with the parties of the members it names and of
the record's sponsors, and `check_party_balance` alerts when the posted share
diverges from the eligible pool's (exact binomial). The post's picture is the
record's card (`services/record_cards.py`, `GET /api/cards/<key>`, rendered by
`/api/og?vote=|bill=|congressEvent=|explore=`), with alt text built from the
same object; for its first 28 days Bluesky gets the image on even Eastern days
and the link card on odd ones (`BroadcastPost.embed_kind`), and
`broadcast.refresh_engagement` reads each post's public counts for the admin
Trends tab's comparison.

In addition to the main pipeline, the **Action Center pipeline** runs hourly to
surface trending civic issues. It fetches RSS feeds from low-bias news sources,
filters articles for U.S. policy relevance by a kNN vote against the feed
articles it has already judged, each labelled by its publisher's own filing
(a politics feed or desk, a world feed or desk) and by whether it names a
sitting official or a federal agency, with k and the threshold refit once a
day and nothing labelled by hand (`app/policy_relevance.py`, stored in
`feed_articles`; `docs/research/action-center-relevance.md`),
clusters articles reporting the same event (complete linkage on headline plus
description start; two articles whose own text dates the event days apart
are never joined; the cut is refit once a day as the lowest no stored pair
of articles about one actor on stated days apart reaches, plus a stated
margin, `action_center.refit_cuts`, `CUT_MARGIN`; a missed merge costs a
story a source, a wrong one publishes a chimera, so the cut is
precision-first and is never refitted from a label of shared names alone), incorporates trending topics from Google Trends and Bluesky,
and uses the LLM only to *locate* attributable claims in the articles: the
summary and facts are the sources' own words, checked verbatim
(`post_composer.py`), and the recommended actions are built from real bill and
source URLs (`_build_actions_from_data`), never written by the model. Members
are linked by full name from the articles' own headlines and descriptions as
well as the issue's text (a bare surname only from the issue's text). When the
issue is served, a linked bill's stored stage names who acts next
(`bill_decider`) and an open comment period names its agency; the reader's own
members, their phones and their votes on the issue's roll calls come from the
state the reader picks on the page; the call script is a fixed template
around the record (`callScript`). Results
are stored in the `action_issues` table. It runs through the nightly chain
too: until 2026-10 it stood down for the Senate, Supplementary, House and
Stock trades links, and the Action Center went as stale as the chain was
long (13 hours on 2026-10-14). A refresh takes minutes, mostly waiting on
feeds and the LLM, and measured beside the chain it fits in memory (the
reasons are in `scheduler._hourly_action_refresh`); only an overlapping
refresh of its own holds it back. The hourly bill refresh still stands down
for the Senate and House runs, which rewrite the rows it updates.

A gate's labels come from the data wherever the data supplies one (the
relevance gate's publisher labels above). Hand labels are the exception, used
only where it was checked that no data-supplied label exists (committee
routing; the story-to-vote link, `app/roll_call_links.py`), and such a gate
records each data-supplied label that turns up later for its next refit.

Running beside the chain makes it a second writer on SQLite's one write
lock, so **no refresh stage holds a write transaction across slow work**
(a model call, an embedding pass, a network fetch): each stage commits
before the next one's fetch (`_commit_stage`), and a statement that opens a
write and may match nothing (the monitor stage's orphan sweep, the cleanup
deletes) is committed anyway, since its DELETE took the lock either way. On
2026-10-15, the first night it ran beside the chain, the orphan sweep's
open transaction lasted through the monitor stage's LLM call, about 40
seconds every hour. The Senate run's learning-store upsert (05:25:43, failed
at 05:26:13) and the House sponsorship step's cache write (failed at
07:23:39) each waited out the 30 s busy timeout inside that window and died
with "database is locked". `database.watch_write_holds` logs any write held
past `WRITE_HOLD_WARN_S` (10 s) and names the line that opened it. As a second
line of defence, the learning-store upserts and `api_cache_set` retry a
locked statement (`database.execute_retrying_lock`, three attempts with
backoff). This only works because a refused statement is always its
transaction's first write, so retrying it loses nothing.
`tests/test_write_lock_holds.py` makes a pipeline write at every slow call
of the refresh's stages on a file-backed database.

The pipeline holds itself to the same rule. **A learning-store write
commits at once** (`bill_learning.record_classification`,
`donor_classifier_ai._store_donor_learning`,
`industry_classifier._store_classification`): every caller goes on to
classify the next bill or donor (an encode, the kNN pass) before it would
commit, and on 2026-10-16 the hold watcher caught the Senate run holding
the lock 19 to 22 s that way, near the busy timeout everything else waits
on. A commit per upsert costs about 0.1 ms, against tens of ms of
classification per item, and keeps every later read in the run (the
exact-match lookups, the kNN references) seeing the rows already written,
which a buffer flushed at batch end would not. The same test file makes a
second-connection write at each classification step of the bill, donor and
industry classifiers.

Each issue's actions are fixed templates around stored data:

- **The committee with jurisdiction**, one per chamber or none
  (`app/committee_routing.py`, outside `app/pipeline/` so it is outside the
  fingerprint like `live_results/`). Tier 2 and tier 3 together: each
  committee's subjects as House Rule X / Senate Rule XXV list them
  (`committee_jurisdictions.json`, `scripts/fetch_committee_jurisdictions.py`;
  hubness-corrected, CSLS) blended with the votes of the nearest
  hand-labelled issues (`committee_routing_examples.json`, an empty label
  meaning no committee). It names a committee only when both signals pick
  the same one and the combined share clears the threshold
  (`committee_routing_calibration.json`, `scripts/calibrate_committee_routing.py`).
  The threshold is chosen on the training issues by the Action Center's
  precision rule (exact lower 95% bound ≥ 0.80, else the highest bound);
  the held-out curve is written for reading, never chosen from. The rule is
  not met yet (0.82 held out, bound 0.67), so the row is worded as a likely
  match — keep that wording until a recalibration reports
  `precision_rule_met_on_training: true` and the held-out bound agrees. The
  test fails until the script is rerun after either data file changes. The rules' subjects alone were
  measured at 0.38 held-out precision — do not drop the labelled votes to
  "simplify". The chair and ranking member are read from the stored rosters
  when the issue is served (`api/action._committee_leaders`).
- **"Comment by <date>"** for a proposed or final rule (the Register's own
  `type`) still open for comment whose title clears `EXPLORE_TITLE_MIN_SIM`
  (`_open_comment_docs`). The API drops it once the period closes (#903's
  open-only rule).

**Related votes in Congress** (`app/roll_call_links.py`, outside `app/pipeline/`
like `committee_routing`): each issue and monitor update is linked to the roll
calls taken from three days before its date to two after whose measure title
is close to its headline (mean of the two models' cosines at the calibrated
threshold; `app/data/roll_call_link_calibration.json`, from hand-labelled
pairs by `scripts/calibrate_roll_call_links.py`, under the precision rule; the
test fails until the script is rerun after the examples change). Computed in
the hourly refresh and stored as refs (`ActionIssue.related_roll_call_refs`,
`MonitorUpdate.roll_call_refs`); the API reads them into `issue_roll_calls`
(so the members panel and call script use them) and a monitor's
`onTheRecord`. Worded as related, never as a cause. An amendment vote is
compared by its stated purpose (`RollCall.amendment_purpose`) and is never
linked without one: its title names only the sponsor. Its labels are by hand
**only because no data-supplied label exists** (in the replay, 0 stories'
articles named a bill a candidate vote was on, and 2 issues had a vote
record as their primary source; the committee-routing precedent). Every
structural link found from now on is kept as a data-supplied label
(`RollCallLinkLabel`, `record_structural_labels`); recalibrate from those
once there are enough. Person names are `[name]` in the examples and in live
text (`NameMask`), and the mask is left out of what is compared. The gate is
registered with the calibration-drift check (`roll_call_links` in
`calibration_drift.GATES`).

The Today tab's open comment periods are ranked by salience, not deadline
(`comment_periods.salience`, shared with agency pages and Explore's
open-comments list): documents a recent issue links, then rules before
notices and Paperwork Reduction Act notices last, then by the Federal
Register's own reader count
(`ExploreDocument.fr_page_views`, read once a day by the Action Center —
`_refresh_open_comment_views` — not by the nightly Explore refresh, whose
module is fingerprinted). Citations were measured and rejected as the
signal: airworthiness directives carry hundreds of boilerplate ones. A
paperwork notice is the Register's own table-of-contents subject for PRA
collections (`fr_toc_subject`, read with the page views), never a title
word: 303 of 615 open on 2026-10-17, precision 303/303, recall 303/304.
Agency pages and Explore fold them under "Paperwork notices (N)".

Feed descriptions are **stripped of HTML** as they are parsed
(`news_feeds._strip_html`): the WordPress-backed feeds put real markup in
`<description>`, and three consumers read that field as prose — the
policy-relevance embedding (which sees only the first 200 characters, all of
it markup for an image-led item), the LLM prompt, and the digest detector
below. Block tags become `"; "` so item boundaries survive, and the
500-character cap measures prose rather than tags.

Each feed's publication times are kept for 60 days
(`ops_alerts.check_source_freshness`), and a feed silent for a day past its
longest gap in that window raises an ops alert. A day, not a multiple: on
Roll Call's own 130-day record (weekends of 64-72 hours, holiday weekends
82-88) twice the longest gap let a dead feed go a week unnoticed, the gap
plus a day under five days, and neither raised a false alert. A feed with
under a week of history has not shown a weekend yet, so the longest gap
among feeds of its kind that have a week stands in.

Then **multi-story digests are dropped at ingest** (`_digest_reason`) — an
outlet's recurring briefing ("Up First", "Morning news brief", "The week in
politics") is a single RSS item covering three to five unrelated stories, and
every stage downstream treats it as one story. Four mechanical signals:

- **The outlet's newsletter section.** An item whose URL sits under a
  `newsletter`/`newsletters` path segment is the outlet's own newsletter, a
  multi-section product whose feed description runs its section headings
  together (one became an issue "fact" of four headings in a row). Only the
  path segment counts, not a slug that mentions a newsletter.
- **A recurring-product title.** Matching is split by where the marker may
  appear, because most of these phrases are ordinary English somewhere else
  in a headline: product names count only title-initial ("Pentagon holds
  evening briefing on troop levels" is one story), "in brief" / "and more"
  only as a trailing tag, and "news brief" never matches "news briefing".
- **A body that lists unrelated stories** — its items name pairwise-disjoint
  entities *and* the headline fails to account for them. Both halves are
  required: disjointness alone flags any single story whose blurb hands off
  between actors, and a headline that names its own subject is what tells the
  two apart. A body cut at the description cap has its trailing fragment
  discarded first, since a fragment's entities are disjoint by construction.
  For the same reason an abbreviation's period ("U.S.", "Rep.") is not a
  sentence break, and photo credits and a broadcast sign-off ("… reports.")
  are not items: before 2026-10 those made 16 single stories in 627 feed
  articles read as lists.
- **A headline and body that tell the same unrelated stories**
  (`_parallel_stories`) — the two-story briefing the body check can't see
  ("<A>. And, <B>" over two teaser items): the headline's items name
  disjoint entities, so do the body's, and each body item names exactly one
  headline item's entities, every headline item named. The body half is
  what makes it precise: on 1,473 stored articles, disjoint headline items
  alone matched 12 single stories along with 16 briefings; with it, 5
  briefings and no single story (`docs/research/action-center-relevance.md`).

A **livestream or video page** ("WATCH LIVE: The president speaks at a midterm rally",
"WATCH: Senate rejects …") is for watching, not a written report, so it never
titles an issue or supplies its facts or sources, and a cluster of nothing
else publishes nothing until a report joins it (`announces_broadcast`, in the
issue loop). It is told by the headline's kicker, the label before its first
": ", classified by embedding margin, so it covers outlets that file their
livestreams as ordinary articles (PBS). The cut is fitted each day on labels
the publishers supply, never hand labels (`fit_broadcast_cut`): stored feed
articles filed under a video section of their site (The Hill's
`/video-clips/`, BBC's `/news/videos/`) against the same feeds' other
articles, F1-maximising, because the labels are noisy (The Hill files some
livestreams as news). A live blog ("Live updates:") is a report and is left
alone. `articles_dropped_broadcast` and `clusters_only_broadcast` are the
counters.

This has to happen here because once several stories share one article the
boundary between them is not recoverable later — cluster coherence filtering
sees one article, and a per-fact topic check does not separate the facts
(measured; see `ACTION_CENTER_PROMPT_VERSION`). Phrases that also appear on
single-topic explainers and live blogs ("what to know", "live updates") are
deliberately left out: the filter drops whole articles, so it is tuned for
precision. `articles_dropped_digest` is the counter to watch.

Each published issue records its **principal actor** (`ActionIssue.principal_actor`,
`pipeline/analyze/story_actor.py`). That is the official named earliest in the
headline, as `_find_related_officials` matched them. When the headline names
none, it is a class from a kNN vote over the relevance seed's examples
(`policy_relevance_examples.json`), whose categories say who acts. The admin's Action Center tab charts
the weekly Herfindahl index of those actors, the president's share and the
share with no government actor (`GET /api/admin/actor-concentration`).
This is measurement, not selection. On 2026-10-12, a replay of every run since
the claim layer, held out by date, tested two rules:

- a one-per-person daily cap
- demoting stories with no government actor

Neither found a single replacement story. Every story that passed the gates
that day had already published, so the cap could only withhold real civic
stories. Demotion either changed nothing or flagged civic stories. The
figures are in `story_actor`'s docstring. Don't add a selection rule for
balance without re-running that replay: it is the supply of stories that pass
the gates, not their order, that sets the mix.

After issues are committed, the Action Center pipeline also:
- **Saves a timeline entry** for each day's #1 issue (permanent record for
  year-in-review tracking, stored in `timeline_entries` table)
- **Updates national monitors** — recurring topics that appear across multiple
  days are auto-detected and tracked in `national_monitors` with sourced
  timeline updates in `monitor_updates`. A monitor opens when today's issue
  matches issues on four other days of the last 14 from three or more outlets
  (`_MONITOR_HISTORY_SIM`, measured by replaying every stored issue). The LLM
  only names it. A name that fails its checks (a title word its articles
  never use, an ungrounded description) falls back to the headline nearest
  the opening issues' centroid (`_representative_issue`), and a monitor
  still named after one of its own headlines is named again on later runs
  (`_rename_headline_monitors`; the slug stays). Pages show the stored
  title, never the slug: until 2026-10-14 the fallback was whichever issue
  crossed the day floor, and pages printed the slug back as the name. Until 2026-10-09 it copied the prompt's example title
  "U.S.-Iran Conflict" for most new topics, which then merged into the
  existing monitor of that name, so the site showed one monitor for months.
  A day's issue joins a monitor at title-to-monitor cosine ≥ 0.71 *and* only
  when it names an entity the monitor's text or updates name that is in few
  enough stored issues to tell stories apart (IDF > 2.0,
  `_MONITOR_ENTITY_MIN_IDF`; demonyms match by prefix); stored updates are
  re-checked the same way each refresh (`_prune_off_topic_updates`). Measured
  2026-10-14: the cosine floor alone let a Russian-diesel monitor take three
  stories sharing only "trump" (a third of all issues); the entity half drops
  all three and none of the Iran monitor's 291 on-topic updates.
  Existing monitors are deduplicated by title similarity. A monitor with no
  update for 7 days is marked "watching" (still listed; the next match makes
  it active again) and after 30 days "closed" (off the Ongoing tab, still in
  the Archive and at its link; deleted instead if it never gathered three
  updates). Long-running stories went up to 28 days
  between issues on that history. The lifecycle runs on every refresh
- **Lists one entry per monitor on the homepage record** — `GET
  /api/action/issues/recent` keeps a monitor's newest issue only
  (`_one_per_monitor`), beside the monitor's own entry. Two days of one story
  are separate developments, not near-identical duplicates, so the duplicate
  pass rightly keeps them; the record's six slots are where they crowded.

**What gets posted is a volume decision, measured like any other.** The
posting modules publish less than the site shows, on purpose: the day's top
two issues by rank and at most two issue posts a day
(`bluesky_poster.MAX_ISSUE_POSTS_PER_DAY`); a Congress day or week only when
something was on the record — a record vote, a measure passed, a nomination
confirmed (`congress_bluesky.has_business`) — and a weekend look-ahead only
when the Record's program shows business ahead. On 2026-10-10 the account
had about 40 followers and 88.5% of its last 1,021 posts drew no
interaction; posting everything was what taught readers to skip it. Change
one of these rules with a replay against the stored history, as their
docstrings do, not because one day's output looks thin. An issue that isn't
posted still keeps its page: the issue cleanup deletes only developing
issues nobody confirmed (`_cleanup_old_unposted_issues`), since the sitemap
lists every confirmed issue.

**A user-facing feature ships with its changelog entry.** A PR that adds a
page or a feature a reader would notice adds one entry to
`backend/app/data/whats_new.json` (its `_contract` names the fields: a
stable id, the UTC release date, a title, one plain sentence, the page's
path). That one entry is `/changelog`'s "New on the site" list, the What's
new feed and one Bluesky post (`app/whats_new.py`, kind `feature`): no post
is ever written by hand, and no code is written per feature. The posting
rule (at most one a day, in the posting window, never on a day busier than
the median of the fortnight before, never an entry over 28 days old, never
a page that doesn't load) is in the module's docstring with the replay
behind it. Write the sentence to stay true: no named politicians, no
pronouns, no em dashes, no emoji, no year or Congress number
(`tests/test_whats_new.py`). Changing an entry's id posts it again.

**Ahead of each election** a nightly pre-check (`ops_alerts.check_election_readiness`)
compares each cycle-specific data file with the election the calendar says
is next and alerts, once its lead time begins, for what is missing: the
cycle's entry in `redrawn_congressional_maps.json` (from January 1; an empty
entry when no state redrew), a computed district lean for every state on
the seated Congress's lines (`district_lines.table_for_lines`, shared by the
computed lean and the county and place crosswalks, carries the last table
forward only for a state whose map didn't change, and only once the cycle
is listed), the voter calendar (150 days out), the poll closing times
(45 days) and towns' sample ballots (60 days; a town ballot from an earlier
cycle is never served as this one's). Standing law is stored as law
(`election_rules.json`'s `standing`, a runoff counted from the general;
`office_terms.json`'s year rules; `state_ballot_scope.json`'s
`statewide_executive`), so the next cycle computes its own.

**Election night and after** (`election_phase.py`, `live_results/sync.py`).
The site's election is not simply the *next* one: from election day (Eastern
date) it stays on the election just held while any race's vote totals are
still moving and for `RESULTS_GRACE_DAYS` (14) after the last change, capped at
January 3 (20th Amendment), and only then rolls to the next cycle.
`active_election()` is the one answer for the pipeline's cycle, the API's
`electionDate`/`phase` and the season jobs — never `next_election_day()`
directly, which flips to the next cycle the night polls close.
`election_phase.season()` refines it into the six stretches the elections
pages lead by (campaign, election night, results, post-certification to
January 3, off-season to the cycle's first primary on the FEC's calendar,
and the primaries' ramp to the last congressional primary): read it for any
"what leads now" decision, never a second copy of these boundaries.

In that window a five-minute job (`scheduler._election_results_sync`) reads
each covered state's own election-night feed — Clarity, Tally ENR, TotalVote,
Enhanced Voting, one reader per vendor beside its primary adapter
(`fetch_general_results`), dispatched by `fetch/election_results.py`; a
state's `live_results` block in `state_candidate_sources.json` names a feed its
primary adapter doesn't read. The count is stored per race (`RaceResult`) with
structured events (`ElectionResultEvent`), and the rules are strict because a
wrong number on election night is worse than none:

- Nothing is read, stored or said for a state before its last polls close
  (`fetch/poll_close.py`, `app/data/poll_close_times.json` from
  `scripts/fetch_poll_close_times.py` — regenerate each cycle).
- Test, preview or mismatched data raises `UntrustedCount` and stores nothing
  (Enhanced Voting `isProduction`, Clarity `istestmode`/`showtestdatawatermark`
  — read as flags: the watermark is the string `"0"` on real elections —
  Tally `previewElections`, `_Preview` ids and `electionID`, TotalVote's
  "Testing in Progress" site message — New Mexico's real 2024 general page
  carried test votes under the general's own date four weeks out; a general's date
  listing no general — several unsingled entries, or only a runoff, recount
  or special — `pick_general`, unless the entries are copies of one election
  by the vendor's own id); a feed that goes backwards in time or
  version is refused; an impossible count is dropped; a poll whose total fell
  is stored but announces nothing.
- A covered state with no count stored an hour after its polls close sends
  an ops alert whatever its reads said (`check_missing_counts`): a walled or
  moved feed fails visibly, never as a quiet night and never worked around.
  A primary's contest labels are not proof of the general's: for the 14 days
  before election day a daily job reads every covered state's staged feed
  exactly as the sync will and alerts on no count, a refused one, or a race
  with no contest (`live_results/readiness.py`; it stores nothing and stops
  the day before). Washington's House labels changed between primary and
  general in 2026; and on 2026-10-12 New Mexico's and Montana's results hosts
  sent their current-election page to their Secretary of State's landing page
  (Montana's says results come "after 8:00 on November 3"), where in 2024 both
  served the staged general a month ahead. Those two will alert daily until
  their hosts serve the general — the honest state of the feed.
- A race the state's feed stops listing mid-count (a renamed contest, one set
  aside as impossible) keeps its last count, tagged NOT IN LATEST READ on the
  page (`inLatestRead`), and pages someone (`sync._dropped_races`): the
  state's other races keep its feed looking healthy, so nothing else would.
- The reporting figure is worded as the source counts it
  (`StateCount.units_complete`, `signals.reporting_line`, frontend
  `reportingText`): "N of M precincts fully reported" where it counts units
  FULLY in — Clarity's county roll-up ("Percent Completely Reported";
  Colorado's 8th read 0 of 3 counties all night in 2024 beside 230,000
  votes), TotalVote's "Precincts Fully", Tally's precinctsReporting — and
  "Results from N of M counties" where a unit counts on its first batch
  (Enhanced Voting: Utah read 29 of 29 counties two days in with a third of
  the vote still to count). A share short of every unit never rounds to 100%.
- A ranked-choice state (Maine, Alaska: `ranked_choice_general` in
  `state_candidate_sources.json`) is never read live: its election-night
  count is first choices, whose leader is not necessarily who the rounds
  elect, so "leads" and a change of party read from it could be the opposite
  of the result. Its page says so (`rankedChoiceStates`), and before
  election day the ballot page says the voter can rank (the state ballot's
  `rankedChoice`, read from the same citation — never a second copy of it).
- Civitas never calls a race. A count is "leading" — "not final" until the
  source itself says official, and still "leading", never "wins", after (an
  official count's leader can face a runoff: Georgia's general needs a
  majority) — and every sentence about it — the live-updates feed, the
  DEVELOPING Action Center issue a seat flip opens (`live_results/signals.py`),
  the election-night posts (`live_results/bluesky.py`, published through
  `broadcast.publish` to the Elections feed and Bluesky) — is
  a fixed template around the source's figures, never model text. A flip needs
  half the reporting units in. Where the units are places (counties, cities
  and towns) it needs every place in and `COUNTY_FLIP_SETTLE` since the first
  votes instead: an Enhanced Voting county or town "reports" on its first
  batch, and a Clarity county counts only once completely reported, so in
  Clarity's states (CO, IA, SC, WV) a change of party is raised only when every
  county says it is complete — in Colorado's 2024 count, weeks after
  election night (the map still shows who leads meanwhile). Where the units are
  places, or the source gives no reporting figure, the source's official flag
  also suffices; beside a precinct count that states one, it does not. That
  bar gates only *raising* a flip: once said, a flip is undone only by the
  lead itself going back to the seat's party or tying (`lead_is_back`), never
  by the count dipping below the bar with the same challenger ahead.
- "Civitas never calls a race" governs **election-night counts**: what a
  count says, and when. It is not a ban on describing an election before
  it. The pre-election map (below, `app/forecast/`) publishes a calibrated
  range for each Senate and House margin with its method and held-out checks shown,
  and stays inside the same spirit: ranges, never a winner, no chance of
  winning on any surface, no seat tally by party, and it is withdrawn the
  moment `active_election()` leaves the campaign phase, so no estimate ever
  sits beside a live count.
- The held election is archived before the site moves on
  (`app/election_archive.py`, `ElectionArchive`, kept by every reset through
  `RESET_KEEPS`): from election day each live-results pass copies the map's
  file once (the campaign's last build: the build stops on election day) and
  the ballot (federal, statewide, legislative and judicial contests), counts
  and statewide measures on every pass until the
  window closes; nothing rewrites it after, and a pass that finds no races
  never replaces a record. `/elections/{cycle}` serves it, and an old
  `/elections/{raceId}` link goes there once the site's election is a later
  one (`race_detail`'s `archiveCycle`). The same §7 rules hold there:
  quoted, and no race called.
- A special election is read the same way, from its own day until
  `RESULTS_GRACE_DAYS` after its count last moves (`live_results/specials.py`):
  the same readers find it by its date (`election_results.pick_election`),
  the same gates apply, and its count is stored on its FEC calendar row
  (`FederalElectionDate`), never on a `Race` (a special is on no cycle's
  ballot pages). Its swing against the seat's usual vote is a measurement
  in the lean's own units, given only once settled and where both major
  parties ran (`app/special_elections.py`).
- The archive page checks the frozen map against the count
  (`app/forecast/accountability.py`): a seat is graded only once its count
  is official by the source's own flag, only against the archive's frozen
  ranges (never the live file or a refit: no look-ahead), and in the map's
  units (two-party margin, top two D and R, as the fit's training results).
  Shares inside the 80% and 95% ranges carry exact binomial intervals,
  per chamber and range basis, beside the coverage the map promised, and
  the misses are listed. The swing map is the official margin minus twice
  the lean. Every figure is the backend's; nothing is graded early.
- A data reset inside the results window keeps the held election's ballot and
  count (`database.RESET_KEEPS_WHILE_RESULTS`) and the coverage markers its
  pages read beside them (statewide, judicial and ballot-basis `api_cache`
  tiers, `HELD_BALLOT_MARKER_TIERS`): after election day nothing rebuilds
  them — the roster and ballot syncs stand down (`election_is_held`).

**The Senate and House map** (`app/forecast/`, `GET /api/elections/map`,
`frontend/src/app/elections/map/`). Before election day each seat on the
active election's ballot shows its usual vote (a state's lean,
`state_pvi.json`; a district's computed lean on the lines of the Congress the
election seats, `congress_for_election(cycle)`, the race page's lines,
`district_lean.json`) and, where both
parties have a nominee, an 80% range for the margin: a structural line
(lean, incumbency, the generic-ballot average) combined by inverse variance
with the race's own poll average, one calibration scale on the spread. Rules
it keeps, each enforced by a test (`tests/test_forecast_model.py`,
`ElectionMap.test.tsx`):

- **Separate from scoring.** Nothing in `app/forecast/` imports scoring code
  or references (`app/pipeline/analyze`, `config_definitions`), it is not in
  `SCORE_WEIGHTS`, and no range is shown beside a Representation Score
  (`RaceContext` links to the map; it does not show the range). It lives
  outside `app/pipeline/`, so editing it never moves the analysis-code
  fingerprint.
- **Fitted, not typed (§3a), and refitted on its own.** `scripts/fit_forecast_model.py` builds
  `app/data/forecast_training.json` (Senate generals from 1998: MEDSL results
  and presidential returns, CC0, newest Dataverse version; FEC incumbency
  codes; Silver Bulletin's raw polls) and writes every parameter, the
  leave-one-cycle-out evaluation and the forward check (the newest cycle,
  fitted on earlier cycles only, with its binomial band) to
  `app/data/forecast_model.json`. `app/data_refresh.py` reruns it when the
  training polls change and monthly, into `/data/forecast_*.json` (the
  checked-in files are the fallback), and alerts, without holding the fit
  back, only when the forward check fails at familywise 5% (Holm over every
  horizon, chamber and direction, `data_refresh._forward_finding`): ranges
  too narrow against 80%, or far too wide against `model.WIDE_COVERAGE`
  (naming the scale factor). A width calibrated from leave-one-out coverage
  was measured and rejected (it moved every forward figure the wrong way). The poll window
  is the training polls' own reach (`model.poll_window`), never typed;
  beyond it every range is the structural line's, labelled fundamentals only.
- **Training polls: Silver Bulletin, gated, attributed.**
  `app/forecast/training_polls.py` (fetched by
  `scripts/fetch_silver_bulletin_polls.py` with `BOT_USER_AGENT` and no other
  identifying header) refuses a whole file on a failed gate (terms sentence,
  exact columns, append-only with bounded logged corrections, 0 to N days
  with N the accepted file's reach, valid shares, a new cycle's Senate and
  generic-ballot rows, stated results against MEDSL's certified margins) and
  names the gate in the alert; an unreadable page is an outage alert. Results
  always come from MEDSL and the FEC, never from the poll file; a race where
  the two disagree is excluded from the fit. Its terms ask for attribution to
  Silver Bulletin: keep it on the map, in `_source` and on `/about/data`.
  The test refits from the training file and must match it within tolerance at write precision (10 significant digits; a different BLAS changes a least-squares solve's last bits). A race's
  historical lean uses only the two presidential elections before it.
- **The House fit's lean is rebuilt, not borrowed.** `scripts/
  build_house_lean_history.py` makes each district's pre-election lean for
  2018, 2020 and 2022 on the lines each was held on, with
  `build_district_lean.py`'s own block method (VEST 2016/2020 on Census 2020
  blocks; 116th lines from TIGER polygons, 118th from the BEF), into
  `app/data/forecast_house_lean.json`; results and incumbency come from the
  FEC's Federal Elections workbooks. Three cycles cannot identify the
  generic-ballot coefficient, so the House takes the Senate fit's
  (`model.fit(national_coef=)`), refitted without each held-out cycle. Don't
  add a House cycle from a source behind a sign-in form (MEDSL's House file
  is one: its guestbook) or a lean made another way; the fit takes every
  FEC compilation the FEC's results index links.
- **Polls from Wikipedia, gated, attributed.** `forecast/polls.py` reads each
  race article's current revision (recorded per seat), parses the table whose
  columns are the confirmed nominees, and rejects the whole table on a failed
  gate (fieldwork after the revision or before January 1 of the year before
  the election, `earliest_fieldwork(cycle)`, shares over 102, a poll
  listed twice, nominee columns ambiguous); a rejected revision keeps the
  previous good one's polls. A House seat's table is in the "District N"
  section of its state's House article. A race whose ballot Civitas hasn't
  confirmed is "unconfirmed", never shown as uncontested. Everything poll-derived (API, CSV, share images)
  is CC BY-SA 4.0, "Wikipedia contributors", and says so.
- **Outage is not "no data".** Each seat's `pollSource` names what the
  poll source did (read / no table / no article / rejected / unreachable,
  with the kept revision's date); a seat with no lean is `no_lean`; a
  failed refresh alerts; a map past `STALE_AFTER` is marked stale. The page
  words each state; it never prints "no poll" for a failure.
- **One figure per seat across pages.** The race page's `usualVote` and the
  map's `leanLabel` come from `district_lean.lean_label`; labels, ranges and
  `close` are written by the backend on the rounded value, and the frontend
  never re-rounds them.
- **Wording.** Ranges and frequencies, never "wins", "favored" or
  "projected". The implied chances exist only for the method section's
  calibration table.
- **No year in the code.** Article titles, the fieldwork floor, the House
  lines and every label (CSV header and filename, page titles, the method's
  presidential window) come from `active_election().cycle` and the data
  files; a new cycle's first build never keeps the last cycle's file. No
  table for the cycle's lines alerts (`election-map-house-lines`), and fits
  that stop before the last certified general (sworn in each January 3)
  alert from the nightly pre-checks (`election-map-training-stale`), naming
  each missing input per chamber from the training files' `_coverage`.
- **Hidden from election day.** The refresh job (`scheduler`, every three
  hours) stops and the API answers 404 once `active_election()` leaves the
  campaign phase; the results map takes over.

After both member pipelines complete, `stock_pipeline.py` runs as a sibling
phase — fetches House (PDF) and Senate (HTML) STOCK Act periodic transaction
reports plus the sitting president's OGE Form 278-T filings (PDF, from OGE's
public presidential disclosure index), matches filer to a known member (the
president's filings are indexed under the office and need no matching),
labels each trade with the industry of the SIC code the SEC assigned its
issuer (`fetch/sec_tickers.py` — tier-1 structured metadata; the donor
embedding classifier measured 30% agreement with it on trade names and is
not used), and computes disclosure timeliness. Every stored trade's industry
is recomputed each night (`_reclassify_stored_trades`); the SEC being
unreachable raises rather than reading as "no industry". Best-effort per phase: one source being
down does not discard the others' rows.

No profit or gain figure is derived for any filer, and none can be: every one
of these forms reports an amount *bracket* per transaction with no cost basis
or share count. Disclosed ranges are stored and shown as filed.

The top bracket ("Over $50,000,000") states a floor and no ceiling, and is
stored as `amount_high == amount_low` — a sentinel, not a real upper bound.
`StockTradeSchema.amount_open_ended` derives from it and every surface renders
those as `$X+`. All three filer groups serialize through that one schema, so a
field added there reaches senators, representatives, and the president
together.

The same run then ingests each member's latest **annual financial disclosure**
(`holdings_pipeline.py`) — the asset list behind the scorecard's holdings pie:
House Schedule A parsed from word positions (`fetch/house_fd.py`; pdfplumber's
table extraction drops most rows on this form), Senate Part 3 from the eFD HTML
(`fetch/senate_fd.py`). One report per member, the newest, replacing the last
— never replaced by an *older* one, which a partial index or search can turn
up. Asset categories (`HOLDING_CATEGORIES` in `config_definitions.py`) come
only from the asset type the *filer* declared (the House's two-letter codes,
the Senate's type/subtype), mapped in `fd_common.py` — a form-vocabulary
translation, never a guess from the asset's name. Values stay brackets
(`low == high` is the same open-ended sentinel — except an exact value a House
filer states instead of a bracket, "$1,251.00", which is stored the same way
and told apart by its printed text: `schemas.EXACT_VALUE_RE`, passed to
`is_open_ended`); the pie is drawn by bracket
midpoints and says so, and no net-worth figure is produced. Reports that can't
be read are stored `parsed=False` with a reason (`scanned` paper filing,
`unrecognized` layout) and linked, not OCR'd. A Senate paper filing states
no year anywhere eFD shows it (its page is page images), so no year is
claimed or inferred for it: it ranks below every dated report, and an undated
filing (paper, or a title with no year) filed on or after the shown report's
date is named beside it ("also filed, on or after this report's filing date")
rather than guessed to be newer. A House member's new-filer report (index type
"H", read from this year's index too) is read for a member with nothing newer;
it states no date its values describe, so it is labelled by filing date and any
annual report outranks it. Filers are matched in `filer_matching.py`: the
last-name field up to its first comma ("Doe, Jr."), and a House filer listed
under a district no member of that surname holds — the Clerk keeps a member's
pre-redistricting district — is matched across the state only when the first
names agree (a shared token, or a measured similarity ratio); failing that, a
filer whose given names hold a member's whole name ("Roe, Jane Doe": a married
name) is matched to that member of the listed district. Each fetch module's
`PARSER_VERSION` keys its parse cache and is stored per report — bump it when a
parser's output changes, and already-ingested reports are re-read (a re-read
that can't read the report at all keeps the earlier holdings; one that reads
rows, or none, replaces them). Both phases are time-boxed
(`holdings_schedule.PHASE_CEILING`: index or search, report fetching, outage
probes, each with its own budget), so a first run or a version bump spreads
over a few nights, and the stock run's overrun alarm
(`ops_alerts.stock_trades_overrun_budget`) allows for that ceiling. The
holdings phases never decide the stock run's
status (that stays "every trade phase failed"); each phase that fails sends
its own ops alert instead. A phase fails on a parser regression — reads that
cannot be read at all (a crash, an unrecognized report, "scanned" where an
earlier parser read the text) outnumbering good ones, counted every night the
regression lasts; empty reads and changed row counts are the parser tests'
job, since a fix looks the same — or when members were tried (or the budget
went on requests that failed) and none loaded *and* none of the reports
already stored still loads either. That live probe, not a
memory of failing filings, is what tells an outage from a few dead links
(`_SourceHealth`). The sitting president's annual report (OGE 278e) is a
third phase (`ingest_president_holdings`): it has no asset-type column, so a
category comes only from what the form states, and every other security is
`UNSTATED` ("Type not stated"), never typed from its name.

**An outage in the sponsorship read is never a record of no bills** (2026-10-15).
A bill whose actions Congress.gov can't give the run (`CongressUnavailable`)
degrades only itself: no roll call is discovered from it, or it is staged from
its latest action (`_les_bill_stage`). One such request used to stop the House
sponsorship step for every member after it (296 of 433 on 2026-10-14). A member
whose sponsored legislation wasn't read is scored on the bills an earlier run
stored (`sponsorship_backfill.carry_forward_sponsored_bills`), and one with none
stored has Legislative Effectiveness's bill part unmeasured, its weight carried
by the other parts. Only members whose bills the run read are measured into the
LES reference (`live_les_reference`): counting the others as members with no
bills put that night's mean credit at 3.16 on a scale defined as 1.0 — and
only when enough of the chamber was read (§3a's coverage floor). How a
stored score treated its bills is kept in its confidence
(`legislativeEffectivenessBills`: `fetched:<date>`, `carried:<date>`,
`unavailable`), so the breakdown and the startup rescore score the stored bills
the same way. The run raises one ops alert naming what went unread
(`run_checks.alert_unread_sponsorship`), and a score-distribution shift on an
unchanged `ALGORITHM_VERSION` and Congress past what such nights have produced
raises another (`score_calibration.SAME_METHOD_*`). That one alerts rather than
holding the previous scores: it is measured on the scores just saved, and a held
score beside the run's stored inputs would disagree with its own breakdown.

The same rule holds for every fetcher the member pipelines read: a failed
request returns the last cached read, or says it failed (`None`, or raises
`CongressUnavailable` / `FecUnavailable` / `GovinfoUnavailable`) — never an
empty answer a caller would take for the source's. A bill whose cosponsor
list wasn't read is left out of the cosponsorship graphs (leadership,
coalition attraction) rather than read as one nobody cosponsored; a failed
roster page stops the run rather than shrink the roster (which
`member_lifecycle` reads as departures); an FEC committee registration that
couldn't be read keeps that member's stored funding. New fetchers follow it.

Each senator is processed independently. The pipeline uses `PipelineRun`
records to track progress and supports resumption. A member (either
chamber) whose prepare or score step raises keeps the scorecard of the
last run it passed, so `run_checks.alert_member_failures` sends one ops
alert per chamber per run (condition `member-failed-<chamber>`) naming
every failed member id with the exception, identical exceptions grouped
so an outage is one alert. It is deduped on the set of (member id,
exception type): the same set the next night says nothing new, a changed
set re-alerts and replaces it, and a run with no failures closes it
(2026-10-09: one senator failed every night from v6.31 on, with only the
run's failure count to show for it).

The page says so too. `run_checks.record_member_holds` marks each failed
member's row (`score_held`: the run that failed, the source it couldn't
read when the exception names one, and the run and method version of the
score kept, from the member's latest snapshot), and the profile, the
leaderboard and compare show it beside the score ("Score from the
2026-10-14 run (method v6.34): the 2026-10-16 run couldn't read FEC
filings"); the next run that scores the member clears it. A held member
keeps the *whole* scorecard of that run: the startup rescores
(`constituent_rescore`, `les_rescore`) skip them, and give every member they
do rescore a snapshot of the score it now shows, so every stored score can
be cited to a run (2026-10-16: a rescore had moved a held senator's score
to one no snapshot recorded).

Each stored score keeps the arithmetic that produced it: both chamber runs
store `explain_scores` over the same input as `calculate_scores` in
`score_breakdown`, and the rescores replace the part they rewrite. The
breakdown endpoints serve that (`basis: "scored"`), recompute on today's
records beside it, and report a dimension that would now come out
differently (`sinceScored`, with how many bills moved for Legislative
Effectiveness) instead of showing components for a number that isn't on the
page (2026-10-16: two senators' bills became law after the night's run, and
the recomputed components gave 86 and 68 beside scores of 75 and 57).
`app/score_keeping.py` holds both, outside `pipeline/`.

The ANALYZE phase runs members one at a time: `precompute_senator_analysis()`
(`cross_reference.py`) does the member's embedding work, then
`analyze_senator_batch()` consumes it and scoring follows. There is no
background thread. The old "Librarian" producer thread existed to overlap
embedding work with per-senator LLM calls; ANALYZE makes no LLM call now, and
the thread is gone.

## Development

### Prerequisites

- Docker and Docker Compose v2
- A free `api.data.gov` API key (sign up at https://api.data.gov/signup/)
- ~16 GB RAM, ~10 GB disk

### Running locally

```bash
cp .env.example .env    # then edit with your API key and admin token
docker compose up -d
# Frontend: http://localhost:3000
# API docs: http://localhost:8000/docs
```

For hot-reload development:
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

### Running backend tests

```bash
# All tests (from backend/ directory or via Docker)
cd backend && .venv/bin/python -m pytest tests/ -v

# Via Docker
docker compose run --rm --no-deps backend python -m pytest tests/ -v

# Fast tests only (no embedding model; runs offline). -n N runs N
# pytest-xdist workers; CI uses -n auto
docker compose run --rm --no-deps backend python -m pytest tests/ -v -m "not slow" -n 4
```

Test configuration is in `backend/pytest.ini`. Async tests use
`asyncio_mode = auto`. **Mark any test that loads a sentence-transformer
model `@pytest.mark.slow`** (~10s startup). The fast job runs `-m "not
slow"` on every PR and must pass with no network access and no model; the
Embedding Tests job runs `-m slow` on pushes to `main` and on any PR that
touches `backend/requirements.txt` or `backend/app/pipeline/`.

`conftest.py` holds the fast tests to that: in a test not marked slow,
constructing a sentence-transformer raises at once and resolving any host
but loopback fails at once, on CI as offline (`_fast_tests_offline`). Until
2026-10 a fast test that reached the model downloaded it on CI and waited
out the Hub client's retries offline (69 s a test), and one whose mocks
missed a fetch read the live source on CI. The fetch modules' rate limiters
and the retry functions' default backoff don't sleep in tests either
(`_fetches_do_not_wait`); a test about the waiting builds its own
`RateLimiter` or passes `backoff_s`. Every test must also pass under xdist,
where each worker is its own process with its own share of the files: no
test may rely on another having run first in the same process.

### Environment variables

See `.env.example` for all options. Key variables:

| Variable | Required | Description |
|---|---|---|
| `DATA_GOV_API_KEY` | Yes | API key from api.data.gov |
| `ADMIN_TOKEN` | Yes | Bearer token for admin panel |
| `LLM_BACKEND` | No | `llama-server` (default) or `ollama` |
| `LLAMA_SERVER_URL` | No | llama.cpp server URL |
| `DATABASE_URL` | No | SQLite path (`docker-compose.yml` sets `sqlite:////data/civitas.db`, the volume; the code default is the relative `sqlite:///data/civitas.db`) |
| `BLS_API_KEY` | No | Bureau of Labor Statistics v2 registration key: lifts the jobs API from 25 to 500 requests a day; works without it |
| `CURRENT_CONGRESS` | **Never in production** | Leave unset — computed from the clock. Setting it pins the scored windows *and* House members' district lines to that Congress past the next Jan 3; only for re-running an archived database |

**On the production Pi, `.env` is a hand-edited, Pi-local file** (see
"CI/CD" below for why — no GitHub Actions job ever touches the Pi
anymore, so there's no automated sync). To change a value: SSH in, edit
`.env` directly, then redeploy. `.env.example` stays the source of truth
for which variables exist and what they do; local development
(`docker compose up -d`) uses its own real `.env` file the same way it
always has. **The production `.env` must not set `CURRENT_CONGRESS`**:
an `.env` copied from a template that once set it (`CURRENT_CONGRESS=119`,
before 2026-09) pins the scored windows and the district lines on the
119th Congress for good — check for the line and delete it
(`check_current_congress_staleness` alerts once it has gone stale).

### Database

SQLite at `/data/civitas.db` inside the container (Docker volume
`civitas_app_data`). On the host:
```bash
sudo sqlite3 /var/lib/docker/volumes/civitas_app_data/_data/civitas.db
```

SQLAlchemy ORM models are in `backend/app/models.py`. Key tables: `senators`,
`representatives`, `key_votes`, `donors`, `industry_donations`,
`campaign_promises`, `lobbying_matches`, `learned_classifications`,
`explore_documents`, `action_issues`, `national_monitors`, `monitor_updates`,
`timeline_entries`, `pipeline_runs`.

**Schema changes are Alembic revisions** (`backend/migrations/`, applied by
`init_db` on start). Change the model, autogenerate a revision, drop removed
columns in it, and run `tests/test_alembic_migrations.py`, which fails when
the models and the revision history disagree — see
`backend/migrations/README.md`. Do not add to `_migrate_columns`: it is the
frozen bridge for databases that predate Alembic. **Expand, then contract:**
Swarm's start-first update and automatic rollback run the previous image
against the migrated schema, so a release may add columns and stop using
old ones, but only a *later* release drops or renames them (the README keeps
the pending list).

## Key Modules — Where to Find Things

| What | Where |
|------|-------|
| Pipeline orchestration | `backend/app/scheduler.py` (entrypoint), `backend/app/pipeline/senate_pipeline.py` / `house_pipeline.py` |
| Departed-member detection + removal | `backend/app/pipeline/member_lifecycle.py` |
| Member ids (slug, cross-chamber uniqueness, rename in place, old-id aliases) | `backend/app/member_ids.py` |
| Stock trade disclosures | `backend/app/pipeline/stock_pipeline.py` |
| Annual-report holdings (scorecard pie) | `backend/app/pipeline/holdings_pipeline.py`, `fetch/house_fd.py`, `fetch/senate_fd.py`, `fetch/fd_common.py`, `services/holdings_service.py`, `frontend/src/components/checker/Holdings.tsx` |
| Scoring formulas | `backend/app/pipeline/analyze/score_calculator.py` |
| Industry classification (embeddings + PAC decontextualization) | `backend/app/pipeline/transform/industry_classifier.py` |
| Donor type classification (tiered + batch skip detection) | `backend/app/pipeline/analyze/donor_classifier_ai.py` |
| Bill policy area + stance derivation (embedding-based) | `backend/app/pipeline/analyze/bill_analyzer.py` |
| Commemorative bill detection (LES 1x tier; calibrated threshold) | `backend/app/pipeline/analyze/commemorative.py` + `backend/scripts/calibrate_commemorative.py` |
| Party alignment (content-based) + partisan depth | `backend/app/pipeline/analyze/party_platform.py` |
| Caucus: the House Clerk's recorded caucus, else inference (votes + cosponsorship) | `backend/app/pipeline/fetch/house_clerk.py` (`parse_caucuses`), `backend/app/pipeline/transform/normalize_votes.py` |
| Committee seats and leadership titles (congress-legislators; the Clerk's list decides the House posts it names) | `backend/app/pipeline/fetch/committee_leadership.py`, `backend/app/pipeline/fetch/house_leadership.py` |
| Donor name casing (learned from Federal Register prose) | `backend/app/pipeline/transform/normalize_finance.py` (`_clean_donor_name`), `backend/scripts/build_name_casing.py`, `backend/app/data/name_casing.json` |
| kNN classifier + inverse-freq balancing | `backend/app/pipeline/analyze/nn_classifier.py` |
| Sponsorship analysis (PageRank leadership + SVD ideology) | `backend/app/pipeline/analyze/sponsorship_analysis.py` |
| Multi-word last name extraction + vote matching | `backend/app/pipeline/transform/normalize_members.py` |
| Lobbying match + key vote selection (deterministic, no LLM) | `backend/app/pipeline/analyze/cross_reference.py` |
| Action Center analysis (news → issues → monitors → timeline) | `backend/app/pipeline/analyze/action_center.py` |
| Claim composer (verbatim spans, actor check: capitalised rule + prototype margin, threshold refit daily) | `backend/app/pipeline/analyze/post_composer.py`, `backend/app/data/claim_actor_threshold.json`, `backend/scripts/calibrate_claim_actor.py` |
| Committee routing for issues (rules' jurisdictions + labelled issues) | `backend/app/committee_routing.py`, `backend/app/data/committee_*.json`, `backend/scripts/fetch_committee_jurisdictions.py`, `backend/scripts/calibrate_committee_routing.py` |
| Issue principal actor + weekly actor concentration (admin) | `backend/app/pipeline/analyze/story_actor.py`, `GET /api/admin/actor-concentration`, `frontend/src/components/admin/ActionCenterDashboard.tsx` |
| Justice record and appointer estimate (not scored, justice v3) | `backend/app/pipeline/justice_pipeline.py`, `backend/app/pipeline/analyze/justice_loyalty.py` |
| The Court page (`/court`: Martin-Quinn line, agreement grid, splits by term, decision lineups) and each justice's case list | `backend/app/services/court_service.py`, `backend/app/api/court.py`, `frontend/src/app/court/page.tsx`, `frontend/src/components/court/`, `frontend/src/components/scorecard/JusticeCases.tsx` |
| Election cycle pipeline (candidates, financials, ballot measures, coverage) | `backend/app/pipeline/election_pipeline.py` |
| What shapes a race (seat lean among all seats, FEC contribution categories, state CSV; descriptive, no forecast) | `backend/app/race_context.py`, `frontend/src/components/elections/ballot/RaceContext.tsx`, `RaceMoneyBars.tsx` |
| Election phase (campaign / election day / results) + results grace period | `backend/app/election_phase.py` |
| Senate and House map: usual vote and poll ranges (pre-election; separate from scoring) | `backend/app/forecast/` (`model.py`, `polls.py`, `training_polls.py`, `build.py`), `backend/scripts/fit_forecast_model.py`, `backend/scripts/fetch_silver_bulletin_polls.py`, `backend/scripts/build_house_lean_history.py`, `app/data/forecast_model.json`, `forecast_house_model.json`, `forecast_house_lean.json`, `backend/app/api/election_map.py`, `frontend/src/components/elections/map/`, `frontend/src/lib/electionMap.ts` |
| Live election-night results (vendor readers, sync, trust gates, events) | `backend/app/pipeline/fetch/election_results.py`, `backend/app/live_results/sync.py`, `fetch/poll_close.py`, each vendor's `fetch_general_results` |
| The map's ranges against the official count, and the swing map (archive page, the hub's Archive card) | `backend/app/forecast/accountability.py`, `backend/app/api/election_archive.py` (`/{cycle}/accountability`, `/{cycle}/swing.csv`, `latest_check` for `api/sections.py`), `frontend/src/components/elections/archive/MapAccountability.tsx`, `frontend/src/lib/sectionPreviews.ts` (`elections.archive`) |
| Archive of each held election (frozen pre-election map, ballot, counts, measures) | `backend/app/election_archive.py`, `backend/app/api/election_archive.py`, `frontend/src/app/elections/[raceId]/page.tsx` (a year is an archive, anything else an old race link), `frontend/src/app/elections/archive/page.tsx`, `frontend/src/components/elections/archive/CycleArchive.tsx` |
| Seat-flip DEVELOPING issues + election-night posts (feed, then Bluesky) | `backend/app/live_results/signals.py`, `live_results/bluesky.py` |
| Results UI (map colouring, live updates, state results) | `frontend/src/lib/results.ts`, `frontend/src/hooks/useLiveResults.ts`, `frontend/src/components/elections/results/` |
| Confirmed candidates — who is really on the November ballot, per state | `backend/app/pipeline/fetch/state_candidates.py` (`STRATEGIES` dispatch) + `backend/app/data/state_candidate_sources.json` (every URL/threshold; its `_contract` key documents the config shape). Adapters are per VENDOR, not per state — adding a state already on a supported vendor is a JSON entry, never new code, and no adapter branches on a state's name. Shared office/party/surname/winner parsing lives in `state_candidates_common.py`; that is what stops per-vendor decaying into per-state. |
| Next cycle: FEC registrations by seat, the FEC deadline event, how the new Congress compares (Congress.gov terms and birth years) | `backend/app/next_cycle.py`, `frontend/src/components/elections/season/` |
| Voter calendar (registration, early voting, mail-ballot deadlines; status on the state's own clock) | `backend/app/voter_calendar.py`, `backend/app/data/election_calendar.json` (built by `backend/scripts/build_election_calendar.py` from `scripts/election_calendar_sources.json`), `frontend/src/components/elections/VoterCalendar.tsx` |
| Statewide ballot-measure ingestion (verbatim, no LLM) | `backend/app/pipeline/fetch/ballot_measures_pdf.py` (pipeline stage + `STRATEGIES`), one reader per state in `fetch/ballot_measures_<st>.py`, registry `backend/app/data/ballot_measure_pdf_sources.json` |
| What changed on the new House maps (old/new district overlap, lean before/after, enacting act) | `backend/app/redistricting.py`, `backend/app/api/redistricting.py`, `backend/scripts/build_redistricting_overlap.py` → `app/data/redistricting_overlap.json`, `frontend/src/components/redistricting/`, `frontend/src/app/elections/redistricting/` |
| Special elections, vacancies and the election calendar (FEC election and reporting dates, House Clerk and Senate vacancies, a special's live count and swing, the season /elections leads with) | `backend/app/pipeline/fetch/special_elections.py`, `backend/app/live_results/specials.py`, `backend/app/special_elections.py`, `backend/app/api/special_elections.py` |
| Official-ballot link table + liveness gating (verified / blocked-unconfirmed / dead) | `backend/app/pipeline/fetch/ballot_lookup.py` |
| Public API v1 + its MCP server (spec generated from the routes, rendered at `/developers`) | `backend/app/api/public.py`, `backend/app/api/public_mcp.py`, `app/schemas.py` ("Public API v1"), `tests/test_public_api_contract.py`, `frontend/src/app/developers/page.tsx`, `frontend/src/lib/openapi.ts` |
| Votes with the president's stated positions (OMB SAPs, matching, per-member rate, expected rate by seat; informational, not scored) | `backend/app/pipeline/fetch/omb_statements.py`, `analyze/sap_position.py`, `analyze/presidential_support.py`, `services/presidential_support_service.py`, `frontend/src/components/presidentialSupport/`, `frontend/src/app/congress/president/` |
| Federal agencies: weekly Register list + USAspending budgets, name crosswalk, agency pages | `backend/app/pipeline/fetch/agencies.py`, `backend/app/api/agencies.py`, `backend/scripts/calibrate_agency_crosswalk.py` (`app/data/agency_crosswalk.json`), `frontend/src/app/agencies/` |
| Presidential nominations (daily Congress.gov sync, FJC join, list/stats/detail, roll-call links) | `backend/app/pipeline/fetch/nominations.py`, `backend/app/services/nomination_service.py`, `services/nomination_links.py`, `backend/app/api/nominations.py`, `frontend/src/app/congress/nominations/` |
| Site search: entity index (write time), identifier parser, ranking, direct navigation, section anchors | `backend/app/site_search_index.py`, `backend/app/site_search.py`, `backend/app/api/search.py`, `backend/app/data/site_pages.json`, `backend/app/data/site_search_sections.json`, `backend/scripts/calibrate_site_search.py`, `frontend/src/components/search/`, `frontend/src/app/search/page.tsx` |
| Explore hybrid search ranking (RRF fusion, priors, dedup, diversity) | `backend/app/services/explore_search.py` |
| Explore permanent addresses (`/explore/{key}` from the official identifier; old row ids 308 to it), coverage windows and "not held here" notes | `backend/app/services/explore_keys.py`, `backend/app/services/explore_coverage.py`, `frontend/src/lib/exploreHref.ts` |
| Explore keyword index (FTS5 + BM25F) | `backend/app/pipeline/lexical_index.py` |
| Document citation graph + PageRank authority | `backend/app/pipeline/analyze/document_authority.py` |
| Search-quality evaluation harness | `backend/scripts/evaluate_explore_search.py` |
| News feed fetching (RSS) | `backend/app/pipeline/fetch/news_feeds.py` |
| Trending topic fetching | `backend/app/pipeline/fetch/trending.py` |
| Donor-vote cross-referencing | `backend/app/pipeline/analyze/policy_alignment.py` |
| Representative service + paginated votes | `backend/app/services/representative_service.py` |
| Finance normalization (embedding-based skip detection) | `backend/app/pipeline/transform/normalize_finance.py` |
| Data validation | `backend/app/pipeline/assemble/validator.py` |
| Civitas-computed district lean (display only, beside Cook) | `backend/scripts/build_district_lean.py`, `backend/app/data/district_lean.json`, `backend/app/data/district_lean_corrections.json`, `backend/app/district_lean.py` |
| Ground-truth regression gate | `backend/app/pipeline/analyze/ground_truth.py` |
| Generated data files' automatic refresh (cadence, gates, provenance, staleness alerts) | `backend/app/data_refresh.py` (`DATA_FILES`), `scheduler._data_refresh` |
| Score/data-quality diagnostic playbook | `SCORE_AUDIT.md` |
| Enums, weights, industry codes | `backend/app/config_definitions.py` |
| Senator service + paginated votes | `backend/app/services/senator_service.py` |
| Action Center API | `backend/app/api/action.py` |
| Related votes in Congress (story to roll call, at write time) + monitor "On the record" | `backend/app/roll_call_links.py`, `backend/scripts/calibrate_roll_call_links.py`, `backend/app/data/roll_call_link_*.json`, `frontend/src/components/action/RelatedVotes.tsx`, `OnTheRecord.tsx` |
| Trend posts ("On the record"): getTrends reader, gates, pinned record matching, audit, record cards | `backend/app/pipeline/fetch/trending.py` (`fetch_bluesky_trends`), `analyze/trend_relevance.py`, `analyze/trend_evidence.py`, `analyze/trend_poster.py`, `backend/scripts/calibrate_trend_gates.py`, `backend/app/services/record_cards.py`, `backend/app/api/cards.py`, `frontend/src/app/api/og/route.tsx`, `frontend/src/components/admin/TrendPostsDashboard.tsx`, `docs/research/trend-posts.md` |
| Publishing: every post stored, then sent to Bluesky; the Atom feeds | `backend/app/broadcast.py` (`publish`, `FEEDS`), `backend/app/api/feed.py`, `frontend/src/app/feeds/page.tsx`; the posting modules (`bluesky_poster.py`, `bluesky_spotlight.py`, `congress_bluesky.py`, `election_bluesky.py`, `trend_poster.py`) decide what and when; a post may carry its own image (`image_url`/`alt_text`, sent as an image embed with the link card as fallback); the hours a non-news post reaches Bluesky are `app/data/bluesky_post_windows.json` (`scripts/fetch_bluesky_activity.py`, `broadcast.SCHEDULED_KINDS`) |
| Representative API routes | `backend/app/api/representatives.py` |
| API routes | `backend/app/api/` (senators, representatives, presidents, justices, admin, explore, action, health) |
| Frontend pages | `frontend/src/app/` (action [issues/monitors/timeline tabs], elections [state index, states/[ST] ballot, [raceId] detail], scorecard, leaderboard, explore, about, admin) |
| Frontend API client (incl. paginated vote fetching) | `frontend/src/lib/api.ts` |
| Per-client rate limits + once-per-period rules shared by every API worker | `backend/app/api/throttle.py` |
| Process roles (read-only API vs pipeline) | `backend/app/config.py` (`PROCESS_ROLE`), `backend/app/main.py` (lifespan), `backend/app/background.py`, `docker-compose.swarm.yml`, `nginx/civitas.conf` |
| Admin dashboard (tabbed sub-dashboards, SVG line charts, chart palette) | `frontend/src/app/admin/page.tsx` (shell + tabs), `frontend/src/components/admin/` |
| Share a section as an image (capture, framing, share dialog) | `frontend/src/lib/shareImage.ts`, `frontend/src/components/share/` |
| Same-origin photos (portraits, justices, issue photos) | `backend/app/photos.py`, `backend/app/api/photos.py`, `backend/app/pipeline/fetch/member_photos.py` (which member photo, by face size; detector process `face_detection.py`; `scripts/calibrate_member_photos.py`), `frontend/src/lib/photos.ts`, `frontend/src/app/photo/[kind]/[id]/route.ts` (and `[width]/route.ts`, the sized copies `Photo`'s srcset asks for), `frontend/src/components/Photo.tsx`, nginx `location /photo/` |
| Issue contact surface (state picker, phones, roll-call record, call script) + action-tap counter | `frontend/src/components/action/ContactYourMembers.tsx`, `backend/app/api/action.py` (`get_issue_members`, `issue_roll_calls`, `bill_decider`), `backend/app/api/visits.py` (`track_action`), `GET /api/admin/action-taps` |
| Page-load timing beacon + histogram | `frontend/src/components/LoadTimingBeacon.tsx`, `backend/app/api/visits.py` (`track_timing`), `GET /api/admin/load-times` |
| SEO: per-route metadata, canonicals, JSON-LD, sitemap | `frontend/src/lib/site.ts`, `frontend/src/lib/seo.ts`, `frontend/src/app/sitemap.ts`, `backend/app/api/sitemap.py` |
| Frontend types | `frontend/src/types/` |
| Metric explanations (tooltips on all scorecard metrics) | `frontend/src/components/checker/MetricTooltip.tsx` |
| Detail pages' two layers: chamber strip, scorecard in short, methods ledger, bill stage ladder and sources, "cite this", held and thin-data marks | `frontend/src/components/scorecard/ChamberStrip.tsx`, `AtAGlance.tsx`, `MethodsLedger.tsx`, `HeldScore.tsx`, `frontend/src/components/congress/StageLadder.tsx`, `BillSources.tsx`, `frontend/src/components/shared/CiteThis.tsx` (a government document or record cites its issuer: `frontend/src/lib/citedSources.ts`); data from `api/politicians.py` (`_chamber_field`, `_scored_on`) and `bill_service.stage_milestones` |
| Action Center issue parts (shared with `/issue/[id]`: meta, tags, "What you can do", coverage, sources) | `frontend/src/components/action/IssueEnrichment.tsx` |
| Leaderboard spread strip, ties and thin-data marks | `backend/app/api/leaderboard.py`, `backend/app/score_display.py` (`score_spread`, `thin_dimensions`), `frontend/src/components/leaderboard/ScoreSpreadStrip.tsx` |
| Site navigation: sections, main menu, footer, section tabs, hub cards and the homepage index | `frontend/src/lib/routes.ts` (`SECTIONS`), `frontend/src/components/layout/Navbar.tsx`, `Footer.tsx`, `SectionTabs.tsx`, `frontend/src/components/sections/SectionCards.tsx`, `frontend/src/components/home/SectionsBand.tsx`, `frontend/src/lib/sectionPreviews.ts`, `backend/app/api/sections.py` |

| Homepage (masthead, on-the-record entries, record index, sources panel) | `frontend/src/components/home/Masthead.tsx`, `OnTheRecord.tsx` (from `backend/app/api/notable.py`), `RecordIndex.tsx`, `Holdings.tsx` |

## Conventions

### Backend (Python)

- Python 3.13+, type hints throughout
- FastAPI for HTTP, SQLAlchemy 2.0 ORM (mapped_column style), Pydantic v2 for schemas
- `async def` for API routes and fetch functions; the nightly pipeline itself runs synchronously in a background thread of the pipeline process
- Logging via `logging.getLogger(__name__)` — structured, no print statements
- **TLS verification is never loosened.** A site that sends an incomplete
  certificate chain (Alaska's, Alabama's and Virginia's election sites,
  2026-10) is repaired the way browsers do it: `make_async_client`'s client
  fetches the missing intermediate from the certificate's own AIA address,
  checks its signature, and adds it to `TLS_CONTEXT`, whose partial chains
  are off so the chain must still end at a certifi root. Never
  `verify=False`, and never a root taken from the network.
- All pipeline modules use dependency injection for DB sessions
- Never store secrets in source code — all credentials come from `.env` via `pydantic-settings`
- Use parameterized queries via SQLAlchemy ORM; never concatenate user input into SQL
- **Dependencies** (`backend/requirements.txt`) pin exactly the dependency tree
  of the direct dependencies — every package, `==` or a sha256-hashed wheel
  URL, nothing extra. `tests/test_requirements_pins.py` enforces both
  directions: add a new direct dependency to its `DIRECT` set, and when you
  drop one's last use, remove it there and the test names every pin that went
  unused with it (42 ChromaDB-era pins sat in the image for months before
  anyone noticed). Everything installs from wheels (`--only-binary=:all:` in
  the Dockerfile and CI): a package with no aarch64 wheel fails the build
  rather than compiling for the build host's CPU (the 2026-07-12 SIGILL).
  torch is the **CPU-only** build, pinned by wheel URL per architecture —
  PyPI's Linux torch drags in ~4 GB of NVIDIA CUDA libraries the Pi can't
  use. Dependabot can't bump it; `scripts/check_torch_cpu_pin.py` (weekly in
  `torch-cpu-watch.yml`) reports a newer build and prints the replacement
  lines. Research/calibration scripts' extra packages (pandas, statsmodels,
  …) live in `scripts/requirements-research.txt`, never in the image.
- **The public API's documentation is its contract.** A route under
  `/api/public/v1` declares a `response_model` from the "Public API v1"
  schemas (which forbid extra fields), a `summary`, a tag and parameter
  descriptions; `/developers` and the MCP tools are generated from exactly
  that, so nothing else needs editing. `tests/test_public_api_contract.py`
  must cover the new endpoint, since those routes return `JSONResponse` and
  FastAPI would otherwise never check the body against the documented shape.
- **Read path must stay lightweight**: never load the embedding model or LLM on
  API read requests (GET endpoints). All ML inference happens at pipeline write
  time. Anything a response needs that takes a model — the Action Center's
  "new since last update" facts (`ActionIssue.new_facts`, `app/fact_diff.py`),
  the homepage feed's duplicates (`duplicate_of_id`) — is worked out when the
  pipeline writes the row and stored; `test_action_api.py` fails if serving
  issues loads the model. The `senator_service.py` and `representative_service.py` read paths use
  only string operations and ORM queries with `selectinload` for eager loading.
  Foreign key columns on child tables (`donors.senator_id`,
  `key_votes.senator_id`, etc.) must have `index=True` for acceptable query
  performance.
- **Performance conventions for concurrent users**:
  - Use `selectinload()` for relationship eager loading to avoid N+1 queries
  - Batch related-entity lookups (collect IDs, query with `.in_()`, map back)
  - Wrap blocking I/O (`fetch_news_articles`, embedding model calls) with
    `await asyncio.to_thread()` to keep the event loop non-blocking
  - Set `Cache-Control` headers on relatively static endpoints (config,
    leaderboards, action issues) to enable browser and nginx proxy caching
  - **Two backend services in production, one image** (`settings.PROCESS_ROLE`,
    2026-09). `backend` is the read-only API (`PROCESS_ROLE=api`, two uvicorn
    workers via `WEB_CONCURRENCY`); `pipeline` runs the scheduler, the startup
    jobs and every triggered run (`PROCESS_ROLE=worker`, always one process —
    its admin status reads run flags from its own memory, and it refuses to
    start as several). Plain `docker compose up` runs one `PROCESS_ROLE=all`
    backend doing both. More containers add no hardware: the point is that a
    pipeline can't hold the interpreter lock page requests wait on, or take
    the site down when it runs out of memory.
  - **Anything that starts background work belongs to the pipeline process.**
    `app.background.start_writer`/`writing` refuse in the API role (a 503),
    and nginx sends `/api/admin/` and every trigger endpoint to `pipeline`
    ("Background work" in `nginx/civitas.conf`). A new POST, PUT, PATCH or DELETE route must be
    either routed there or listed in `tests/test_nginx_routing.py`'s
    `SERVED_BY_API` — that test fails otherwise. Explore summaries are such
    work: an LLM generation that finishes after its reader leaves, so nginx
    streams `POST /api/explore/{id}/summary` from the pipeline process,
    where one generation per text is shared by every reader of it and the
    cap and hold-offs are plain in-process state
    (`services/explore_summary.py`) — nothing to split across API workers
    or a rolling update.
  - **No per-client state in module globals.** With several API workers each
    has its own copy, so a limit stretches to its value times the worker
    count and a once-per-period rule lets a second request through on the
    other worker. Rate limits and once-per-period rules go through
    `app/api/throttle.py` (`hit`, `claim`): a SQLite file in RAM
    (`/dev/shm`), shared by the container's workers and never on disk —
    the same lifetime and exposure the per-process dicts had. Keys come
    from `throttle.client_key`: an HMAC of the IP under the store's own
    daily salt — never an IP, and never the visitor hash `SiteVisit`
    stores. A salt is deleted once the day after its own ends, and rules
    count a client's previous-day key too, so nothing resets at midnight.
    Every row expires. The hourly upstream-lookup
    budget (`rate_limit.spend_upstream`) is one shared count the same way.
    Caches of data every client sees alike (`bill_service`'s collection
    cache) are fine per process. So is state in the pipeline process, which
    is always exactly one process (it refuses to start as several):
    `services/explore_summary.py` keeps its one-generation-per-client rule
    there, whole by construction, keyed by an HMAC of the address under a
    salt only that process holds (never the address).
  - A module cache of a file the pipeline rewrites must notice the rewrite
    from the API process: keep a `file_cache.files_stamp` of it and reload
    when it moves. Clearing the cache from the writer only clears the
    writer's own process.
  - Two backend *processes* also meet during a Swarm start-first rollout,
    when the old and new tasks overlap on the same database, which is what
    the `init_db` lock and the `IF NOT EXISTS` DDL guard against
  - Nginx caches every response the backend marks cacheable, for as long
    as its `Cache-Control` says (`api/cache_headers.py`) — no per-route
    cache block needed — and rate-limits the API's cache *misses* only:
    every cached `/api` location hands misses to an internal loopback
    server ("api-misses") that applies `limit_req` before the backend (or,
    for `/api/og`, the frontend's image renderer), because a limit on the
    public location runs before the cache and would refuse cache hits.
    Explore search and OG images have limits of their own there
    (`tests/test_nginx_routing.py` checks every cached location goes
    through it). Only `/api/config` and `/api/og` set their own lifetime, and
    `/api/public/` is deliberately uncached (its responses carry the
    caller's own rate-limit counts)

### Frontend (TypeScript)

- Next.js 16 App Router with server components where possible
- TypeScript strict mode, types in `src/types/`
- Tailwind CSS for styling
- API calls go through `src/lib/api.ts`
- Dynamic configuration fetched from `GET /api/config` — never hardcode industry codes, score weights, or category labels
- **Site prose uses no em dashes** (2026-10). Use a comma, colon, parentheses or a
  full stop; for a separator inside a label, the middle dot " · ". A string that is
  only "—" is the table placeholder for no data and stays.
  `src/lib/noEmDash.test.ts` fails on any other em dash in a user-visible string.
  The rule covers backend text the site shows too: the scorecard's "show the
  math" details and notes come from `score_calculator.py` and
  `president_scorer.py`, and `backend/tests/test_shown_text_style.py` fails on
  an em dash in any string there that isn't a docstring or a log message.
- Every metric shown on scorecards has a `MetricTooltip` component providing
  plain-English explanation (hover on desktop, tap on mobile). When adding new
  metrics, always add a corresponding tooltip so users can understand what they
  are seeing. The component is at `src/components/checker/MetricTooltip.tsx`.
- An About chapter's top-level parts are `Section`s, direct children of
  `AboutPage`: that is how its "On this page" list finds them (four or
  more), so a part wrapped in a fragment or another component drops out of
  it. A new score gets a plain line in `/about/scores`' short version,
  held to a Flesch-Kincaid grade of 8 or below.
- A scored dimension leads with its `finding` (one template sentence from the
  breakdown API, `services/score_findings.py`, outside `pipeline/` so its
  wording never moves the analysis-code fingerprint); components, weights and
  statistics go under the column's "How this is computed" disclosure. Keep a
  finding subjectless and at about a Flesch-Kincaid grade 8 or lower.
- The "this week" strip (`components/shared/ThisWeekStrip.tsx`) is an async
  server component; a client page gets it as a `strip` prop from its server
  `page.tsx`, never by fetching it on the client.
- Every page's `<main>` is `id="main-content"`: the skip link targets it, and
  `globals.css` gives it `min-height: 100vh` so the footer never paints in the
  first screen while a client-fetched page loads and then jumps away (CLS
  0.58-0.80 on /action, /elections and Explore documents before 2026-10-14).
  `layoutStability.test.ts` fails on a `<main>` without the id.
- **Reflow at 200% text (WCAG 1.4.10).** On a phone at 200% text the header's
  wordmark and its SEARCH and MENU buttons can't share one row, so the bar
  wraps, and `HeaderClearance` (root layout) moves `--header-clearance` down
  to the measured header; at ordinary sizes it leaves the stylesheet's 6rem
  alone. Width-dependent choices inside a component are container queries in
  rem (`@min-[20rem]:` on the bar), which follow a larger text size however
  the reader set it. A grid that can be narrower than its content needs
  `grid-cols-[minmax(0,1fr)]` (an implicit column is as wide as its widest
  child), and a label in a narrow box needs `min-w-0` with
  `[overflow-wrap:anywhere]`. Until 2026-10 every page's header, and the
  scorecard header, ran past a 320-375 px screen at 200% text.
- Large tab components are code-split with `next/dynamic` to reduce initial
  bundle size (e.g., Action Center tabs load on demand). Use in-memory
  `cachedFetch` from `src/lib/api.ts` for API calls that benefit from
  client-side TTL caching.
- **Sharing a section as an image** is opt-in per section: mark the section
  element `data-share-section="<anchor-id>"`, put a `ShareSectionButton`
  inside it, and wrap the page in a `ShareSubjectProvider` naming what it is
  about (the image's title strip and link come from there). Mark controls
  that mean nothing in a picture (toggles, "open" buttons, votes) with
  `data-share-exclude`. The capture is client-side, from the live DOM, and
  never requests a third-party host from the visitor's browser (§8): images
  that aren't same-origin are left blank unfetched, so mark them excluded.
  The guard covers images only (`<img>`, SVG `<image>`, CSS image urls) —
  fonts, stylesheets and an external `<use href>` inside a captured section
  are fetched as-is, which is fine only while they stay self-hosted.
  Every photo the site shows is already same-origin (§8's `/photo/<kind>/<id>`),
  so a capture draws them as-is.
  Every share point must supply `source` and `asOf` (required by the
  `ShareSubject` type; a section whose figures come from elsewhere sets its
  own with `shareMetaAttrs`), and the captured image carries the source
  line, the as-of date and the score scale under the picture, since a
  picture travels without its page.
- **A new page goes in `SECTIONS`** (`src/lib/routes.ts`): that one table is
  the main navigation (a disclosure button per section on desktop, two
  levels on a phone), the footer, each section's tab bar, its hub's cards and
  the homepage's index (which is why the footer on `/` drops its section
  columns, `Footer omit`), so a page that isn't in it is reachable
  only by an inline link. `routes.test.ts` fails on a page route in no
  section (records with a `[segment]`, the footer's own pages and the admin
  aside). A page with a live figure gets it from `GET /api/sections`
  (`app/api/sections.py`), worded in `lib/sectionPreviews.ts`; a figure not
  read yet says so, never a bare zero.
- Tabbed UIs follow the WAI-ARIA tabs pattern with a roving `tabindex`.
  Activating a tab must focus **the incoming tab**, not its panel — the
  Arrow/Home/End handler lives on the `role="tablist"` container, so moving
  focus into the panel strands the keyboard user and kills every arrow press
  after the first. The panel keeps `tabIndex=0` so Tab still reaches content.
  Focus through `focusTabWhenSelected` (`src/lib/tabFocus.ts`): it waits for
  the incoming tab to render as selected (a URL-derived selection can land a
  frame late) and drops the focus if another tab was selected first.
- **A drawer opened at one of its items moves focus in the drawer's own
  effect** (the ballot page's `ContestDrawer` `focusId`, from a measure row
  or a `#measure-{id}` link). React runs a child's effects before its
  parent's, so an item that focused itself would have its focus and scroll
  undone by the drawer's "start at the top" effect a moment later.

#### Search metadata (2026-09)

- **Every route sets its metadata through `pageMetadata()`** (`src/lib/site.ts`)
  — title, description, canonical, Open Graph, Twitter, in one call. Next
  merges metadata *shallowly*: a route that sets `openGraph` at all drops its
  parent's og:site_name, og:url and — verified under `next build` — the root
  `opengraph-image` too, which is why the helper always supplies an image.
- **Canonicals are per route, never on the root layout** (it would be
  inherited by every page and declare the whole site a duplicate of the
  homepage). A section layout's canonical is inherited the same way, so a
  dynamic child (`/politicians/[id]`, `/explore/[id]`…) must set its own,
  taken from the record, not the request's spelling. An Explore document's
  is its permanent key (`services/explore_keys.py`: the FR document
  number, the Record granule, the Court's docket), never its row id, which
  the pipeline can replace; every other address for it answers a 308 to
  the key, and links, the sitemap, the public API's `siteUrl` and "Cite
  this" use the key (`exploreHref`). Its 404 can't (a
  route's `notFound()` discards its page's metadata; the head comes from
  the layouts above), so a section whose index page is a client component
  keeps that page's metadata in a layout inside an `(index)` route group
  (`politicians/(index)`, `congress/bills/(index)`, `elections/(index)`,
  `explore/(index)`), and each record route has a layout setting only its
  404's title; a not-found head has no canonical (`notFoundMetadata`).
  `app/sectionMetadata.test.ts` fails on a layout with a canonical above a
  record route.
- Titles lead with what people type into a search box (member name +
  party-state, bill number, full state name); the root template appends
  " | Civitas Research" (no em dashes in site prose, 2026-10). Search-facing wording lives in `src/lib/seo.ts`, not in
  `page.tsx` (Next rejects extra exports there).
- **The site's name is "Civitas Research"** (`SITE_NAME`) wherever a
  search engine reads a name: the title template, og:site_name,
  application-name, the manifest, the homepage's `WebSite` and
  `Organization` JSON-LD (`siteJsonLd` in `src/lib/seo.ts`, with "Civitas"
  as an `alternateName`), and citations. "Civitas" alone is shared by a UK
  think tank, a U.S. university institute, an oil company and more, and
  Google's site-name system wants the WebSite data, the title and
  og:site_name to agree. The on-page mark stays "CIVITAS".
  `www.civitas-research.org` 301s to the apex in `nginx/civitas.conf`.
- A missing record is a real 404 via `notFound()` plus `noindex`, never a
  200 page that says "not found". The converse holds too: an unreachable
  backend or a 5xx is an error page (`app/error.tsx`), never a 404 — fetch
  detail records through `fetchRecord` (`src/lib/ssrPayload.ts`), which
  returns null only for a 404. A record route's 404 offers the next step
  for that kind of record from its segment's `not-found.tsx`
  (`components/notFound/RecordNotFound.tsx`: a member's closest names, the
  bills list, the Action Center, Explore search, a state's ballot), never only "back home".
- `sitemap.xml` is rendered per request from `GET /api/sitemap` — never
  prerendered, since `next build` can't reach the backend. Add a new
  detail-page type there, or search engines have no way to find it.
  Explore documents have no upper bound, so they are listed a page at a
  time (`/sitemaps/explore/{n}`, from `GET /api/sitemap/explore`), and
  `robots.txt` names `/sitemap-index.xml`, which lists both.
- `robots.txt` must not disallow `/api/`: Googlebot's renderer honours it for
  the XHRs the client-rendered pages make, and would index them empty.

#### Client-side URL state on statically prerendered routes (2026-07)

Three traps, all found in the Action Center's tab bar, **none of which
reproduce under `next dev` — only `next build`**. Any page that keeps view
state in the query string is exposed to them.

- **`router.replace()` silently does nothing** on a statically prerendered
  route once the page was loaded *with* a query string: Next treats the
  same-route navigation as already-satisfied and the address bar never
  updates. `/action?tab=timeline` froze there for the whole session — every
  tab click swapped the panel but left the URL reading `?tab=timeline`, and a
  refresh re-froze it. Use `window.history.replaceState` for search-param-only
  updates instead (every page that keeps view state in the query string does
  now, `/compare`'s pair included — it was the last `router.replace`, found
  2026-10); Next patches the History API, copies its internal state
  onto the entry (so passing `null` is safe and popstate still works), and
  dispatches `ACTION_RESTORE` to keep `usePathname`/`useSearchParams` in sync
  without performing a navigation.
- **That sync cuts both ways.** Because the replaced URL comes straight back
  through `useSearchParams`, any prop derived from it becomes live. A
  `?issue=<id>` the page wrote itself flipped a card's `deepLinked` prop and
  fired its arrival effect, scroll-jumping the page on an ordinary expand
  click. Params meant to describe *how the page was opened* must be latched at
  mount (`useState` initializer), not re-read on every render.
- **A soft navigation with an *empty* search reuses the cached entry's search
  string.** So after a session has seen `/action?tab=timeline`, every
  `<Link href="/action">` in the app lands back on the timeline tab. A href
  that names its tab is never reused this way — hence `ACTION_CENTER_HREF`
  (`/action?tab=issues`) for all in-app links. Bare `/action` stays fine as a
  public entry point: a cold load has no client cache to restore from. Do not
  "tidy" that query string away.

#### Empty defaults are content (2026-10)

A default view that renders zeros or "nothing found" on an ordinary day is a
defect, not an empty state. Say what the site does know, from stored data,
decided by the backend: `/congress` on a day without business leads with the
recess and the last week of business (`recess` on `/api/congress/latest`),
the bills page's "Active now" falls back to bills that moved this week and
says so (`shown`/`movedSince`), a bill page states its stage's base rate
(`stageBaseRate` — worded as what happens to bills at the stage, never a
forecast for the bill), the Ongoing tab lists recently closed monitors, and
`/compare` offers pairs that need no picking. Never fill the gap with model
text or a visitor's input (§7, §8).

#### Accessibility: WCAG 2.2 AA is a merge requirement (2026-10)

Accessibility outranks styling. A change that fails WCAG 2.2 Level AA is not
merged, however it looks; when a design and a criterion disagree, the design
moves. `/accessibility` states this target and the known gaps; a change that
closes or opens one updates that page in the same PR.

How to check, before asking for review:

- **`npm test`**: the jsdom axe tests (scorecards, Congress reports, the
  state ballot, the voter calendar, the maps) and
  `src/lib/wcagContrast.test.ts`, which computes the WCAG ratio of every text
  token on every ground and of every `text-*`/`bg-*` pair written together in
  one class string (each variant: `hover:`, `aria-selected:`...) from
  `tailwind.config.ts`, and fails below 4.5:1 (3:1 for large text). A colour
  that fails is fixed in the token or the pair, never with an opacity tweak at
  one call site.
- **`npm run build && npm run test:a11y -- .lighthouserc.populated.json`**
  (`scripts/axe-routes.mjs`, also run by the Lighthouse workflow on both of
  its route lists): every axe rule tagged WCAG 2.0, 2.1 or 2.2 A/AA, in
  Chrome, at 320px and 1280px, plus no horizontal scroll at 320px. A new
  route or page type goes into the Lighthouse configs so both gates see it.
  The workflow is not a required GitHub check, so read its result on the PR.
- **By hand, on a production build**, for what axe cannot see: keyboard
  paths through anything interactive you touched (menus, tabs, disclosures,
  drawers, dialogs, maps), Shift+Tab as well as Tab; focus visible and never
  under the fixed header; 200% text at 1280px; text drawn inside SVG charts
  and maps (axe does not measure it); hover and focus pop-ups; reduced
  motion. Say in the PR what you checked and the result.

Patterns that exist because they failed once:

- **Focus Not Obscured (2.4.11)** is one rule: `html { scroll-padding-top }`
  in `globals.css`, sized to the fixed header. Don't add `scroll-mt-*` to
  anchor targets (it adds to the padding). A page with a sticky bar under the
  header marks it `data-sticky-subnav`.
- **Dialogs** trap Tab with `trapTab` (`src/lib/focusedDialog.ts`), which
  also wraps when focus sits on the dialog or its heading. A check for "on
  the first control" let Shift+Tab out of every dialog here.
- **Links in running text** use `INLINE_LINK` (`src/lib/controlStyles.ts`):
  underlined, since cyan is 1.1-1.3:1 against the ink around it.
- **Labels on map tiles** carry `MAP_LABEL_HALO` (`src/lib/results.ts`); no
  ink colour reads on every party fill.
- **Hover content** stays open while the pointer moves onto it and closes on
  Escape from anywhere (`MetricTooltip`); no `title` attribute as a tooltip.
- **Scripted scrolling** takes `scrollBehavior()` (`src/lib/motion.ts`), so
  reduced motion jumps instead of gliding.
- **Targets** are 24x24 (`min-h-6`, or `h-6 w-6` with a negative margin, as
  `MetricTooltip` does) unless they are links inside a sentence.
- **A filter that changes a list** says so in a `role="status"` line
  (`CalendarList`).
- **A display or pixel face** is for the wordmark only, never body or data
  text.

### Testing

- pytest with `asyncio_mode = auto`
- Tests live in `backend/tests/test_*.py`
- Use `SimpleNamespace` or dicts for mock data in unit tests
- Test scoring, classification, and validation logic — not LLM output
- When changing scoring logic or classification, update corresponding tests to reflect the new expected behavior
- Tests never read or write the data volume (`/data`): `conftest.py` points
  the runtime paths into each test's `tmp_path` and refuses (and fails the
  test on) any read or write that still reaches `/data` — a read would make
  a result depend on the host's live data. A new runtime file under `/data`
  needs its path redirected there. An app module a test imports lazily is
  redirected as it loads (`_RedirectOnImport`, an import hook in
  `conftest.py`), with the redirect undone with the test.
- Tests never depend on the real clock. Pin the instant a test reads with
  `freeze_utcnow` (it also pins `election_phase.election_today`) or by
  patching `app.time_utils.utcnow`, and use fixed dates in fixtures. The
  Congress is pinned for you: the autouse `_sitting_congress_pinned`
  (`conftest.py`) starts every test with `settings.CURRENT_CONGRESS` at
  `TEST_CONGRESS` (119) and makes `congress_in_session()` answer it while
  the test leaves the clock alone — so a job a test starts
  (`start_writer`, `writing()`, `scoring_congress`) or a `Settings()` it
  builds doesn't move to whatever Congress the real clock has in office.
  Once a test patches the clock (`app.time_utils.utcnow`, or a name it
  reads such as `app.time_utils.datetime`), the Congress follows that
  clock, as in production. A test about another Congress sets it
  explicitly (`monkeypatch.setattr(settings, "CURRENT_CONGRESS", N)`) or
  freezes the clock inside it. To check a change, run the fast suite with
  the whole process on another date — `scripts/frozen_clock_plugin.py`
  (time-machine, from `scripts/requirements-research.txt`; its docstring
  has the details) — on both sides of noon ET on an odd year's Jan 3, on
  election night, and *before* the fixtures' era (a spring before the
  cycle's primaries, a year before the Congress): a test that assumes
  "now" is after its fixture's dates (a primary settled, a rule
  published, a vote held) passes on today's clock and breaks on an
  earlier one, so pin its clock to the fixture's era. The clock runs on
  from `FREEZE_AT` while the suite runs (about ten minutes), so an instant
  meant to be *before* a hand-over needs more margin than that: 15:00Z is
  10:00 ET, after the old midnight trap and two hours short of noon:

  ```bash
  cd backend
  for at in 2025-07-01T15:00Z 2026-04-08T02:00Z \
            2027-01-03T15:00Z 2027-01-03T17:01Z 2026-11-04T02:00Z; do
    FREEZE_AT=$at PYTHONPATH="$PWD" PYTEST_PLUGINS=scripts.frozen_clock_plugin \
      .venv/bin/python -m pytest tests/ -m "not slow" -q -p no:cacheprovider
  done
  ```
- Code that opens its own sessions from several threads (`asyncio.to_thread`,
  a `threading.Timer`) gets `file_sessionmaker` (a file-backed database, a
  connection per session) as its `SessionLocal`, never the test's one
  `db_session` handed to every thread — two threads in one Session race.

### Deployment

- **Production runs in Docker Swarm mode** (single-node — `docker swarm init`
  is a one-time host setup step, not part of any deploy script). Deploys are
  `docker stack deploy -c docker-compose.yml -c docker-compose.swarm.yml
  civitas` — nothing else. There is no hand-rolled blue/green script anymore
  (`deploy.sh`, removed 2026-07): Swarm's own `update_config.order:
  start-first` (start the new task, health-check it, *then* stop the old one)
  and `failure_action: rollback` (auto-revert if the new task never becomes
  healthy) are the zero-downtime + rollback mechanism natively — `deploy.sh`
  was reimplementing exactly this in ~600 lines of bash.
- `docker-compose.swarm.yml` is a Swarm-only overlay on top of the base
  `docker-compose.yml` (which is also the plain-dev `docker compose up -d`
  file). Read the comment block at the top of `docker-compose.swarm.yml` for
  what it changes and why — the two things worth knowing without opening it:
  backend/frontend publish **no** host port under Swarm (nginx is the only
  published service, on 8081, same as before — Swarm's host-mode port
  publishing can't bind to `127.0.0.1` only like plain `docker run -p` can,
  confirmed live, so the fix was to stop publishing those ports to the host
  at all rather than accept LAN-wide exposure); and `app_data` is pinned to
  the pre-existing `civitas_app_data` named volume via `external: true`, not
  whatever a fresh stack deploy would otherwise auto-name it.
- nginx is **in** the stack now (`nginx/Dockerfile` + `nginx/civitas.conf`),
  attached to the same Swarm overlay network as backend/frontend. Its config
  is static — no more per-deploy template rewrite — because Swarm's overlay
  DNS (`backend`, `frontend`) already resolves to whichever task is healthy,
  blue/green-flip semantics included.
- Usage: `./check-and-deploy.sh` (the cron-invoked poller, safe to run
  manually — see CI/CD below) builds fresh images tagged `sha-<short-sha>`
  and runs the stack-deploy command above. There's no separate
  frontend-only/backend-only deploy anymore; Swarm only rolls the services
  whose image tag actually changed.
- nginx's security headers (nosniff, X-Frame-Options, Referrer-Policy,
  Permissions-Policy) live in `nginx/security-headers.conf`. nginx drops the
  server-level `add_header`s from any location that sets one of its own, so
  **every location that adds a header must also include that file**;
  `backend/tests/test_nginx_config.py` fails when one doesn't.
- Docker images built from `backend/Dockerfile`, `frontend/Dockerfile`,
  `nginx/Dockerfile`
- Data persists in the `civitas_app_data` Docker named volume, which survives
  rebuilds and stack redeploys (it's external to the stack, never recreated
  by `docker stack deploy`)
- Frontend Dockerfile uses multi-stage build (deps → build → runner) with non-root user

### CI/CD

Pushes to `main` run `.github/workflows/ci.yml` (lint/build/tests) on
GitHub-hosted runners only. Deployment is **pull-based**, not triggered by
GitHub Actions: a cron job on the production Pi (`*/5 * * * *
check-and-deploy.sh`) polls `origin/main` and, when it finds a new commit,
checks `gh run list --workflow CI` for that commit and refuses to ship a red
build (override with `FORCE_DEPLOY=1`), then builds images and runs
`docker stack deploy` itself — no separate `deploy.sh` to call anymore.
GitHub Actions never executes anything on the Pi.

**Why not a self-hosted GitHub Actions runner (removed 2026-07):** the
deploy job used to run on a self-hosted runner registered directly on the
Pi. Once this repo went public, that became a real risk regardless of how
carefully the old `cd.yml`'s own trigger was gated — a PR doesn't need to
modify `cd.yml` at all to reach a self-hosted runner; it can add an
entirely new workflow file with its own `pull_request` trigger targeting
the same runner label. GitHub's own guidance is that self-hosted runners
"should almost never be used for public repositories." Pull-based deploy
removes this attack surface entirely: nothing GitHub Actions runs has any
path to executing code on the Pi.

- Check deploy status: `tail -f deploy-poll.log` on the Pi, `git log -1` in
  the deploy checkout to see what's currently live, or `docker stack
  services civitas` / `docker service ps civitas_backend` for Swarm's own
  rollout state.
- Trigger a deploy without waiting for the next cron tick: SSH in and run
  `./check-and-deploy.sh` directly — it's the same command cron runs, safe
  to invoke manually.
- Cron entry: `crontab -l` on the Pi (runs as user `ryan`).

**Secrets are Pi-local now, not synced from GitHub.** The old system wrote
`.env` fresh from GitHub Secrets on every deploy (only possible because
GitHub Actions can inject secrets into a running job — external scripts
can never fetch them). Rotating a secret now means: SSH in, edit the
relevant line in `.env` directly, then deploy (`./check-and-deploy.sh` or
wait for the next cron tick). `gh secret set NAME` still works and is worth
keeping as a record of the current intended value, but it no longer does
anything functional on its own — it's bookkeeping, not a sync mechanism.

**Setting up a new deploy target** (replacing the Pi, or adding a second
one): clone the repo to the target device, create `.env` there by hand
(copy every key from `.env.example`, filled in with real values — no
secrets are auto-provisioned anymore), then add the same crontab entry
pointing at `check-and-deploy.sh` in that checkout. No GitHub Actions
runner registration needed.

**Images build locally on the Pi, not via GHCR pull — this is unrelated to
the runner change above and still applies.** The original design —
`build-and-push` in `ci.yml` cross-builds images on GitHub-hosted runners
and pushes to GHCR, `check-and-deploy.sh` pulls those tags instead of
building — hit a cross-microarchitecture SIMD trap worth remembering if
anyone reintroduces GHCR publishing: `ubuntu-24.04-arm` runners are
server-grade Ampere/Cobalt CPUs, a different ARM64 microarchitecture than
the Pi 5's Cortex-A76. `hnswlib` (ChromaDB's HNSW vector-index C++
library) had no prebuilt aarch64 wheel anywhere, so `pip install` compiled
it from source on whichever machine ran the build, auto-detecting and
baking in *that* machine's SIMD instruction support — the resulting
backend image SIGILL'd (exit 132) on the Pi at first real HNSW-index use,
crash-looping in production until caught and rolled back same-day
(2026-07-12). "Native ARM64" on GitHub's runners is not the same ISA as
the Pi. Building on the Pi itself is reliably safe (compiling for itself
can't produce an incompatible binary) — hence `check-and-deploy.sh`
building locally on every deploy, slower but guaranteed
instruction-set-compatible.

**Not the same situation as `llama-server`'s image (2026-08-29):** the
`ghcr.io/ggml-org/llama.cpp:server` image `docker-compose.yml` pulls is a
different case, not a reintroduction of the pattern above — it's a
third-party image ggml-org itself builds and publishes for arbitrary
arm64 hardware (so it can't bake in one build machine's specific SIMD
extensions the way our own from-source `pip install` compiles did), and
it was live-tested doing real inference — actual prompt processing and
token generation via `/v1/chat/completions`, not just a health check — on
the production Pi 5 before adopting it. The SIMD trap below is still a
real risk for reintroducing GHCR publishing of *our own* backend/frontend
images.

`ci.yml`'s `build-and-push` job was removed entirely (2026-07-23), not
just left disabled — its actual consumer, the old `cd.yml`, had already
been removed by the self-hosted-runner change above, so even a fixed
build would never have been deployed from anywhere. If GHCR image
publishing is wanted again for some other reason (backups, a future
multi-host deploy), it needs designing fresh against the current
architecture — chromadb/hnswlib are gone now too (see the sqlite-vec
migration), so the specific SIMD trap above may no longer apply, but
don't assume that without testing a GHCR-built image past first real
vector-store use on the Pi, not just an HTTP health check (the crash
never showed up there).
