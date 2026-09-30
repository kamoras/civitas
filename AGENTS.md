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
House representatives, presidents, and Supreme Court justices on how well they
represent constituents. It aggregates voting records, campaign finance, floor
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
- **Deployment**: Docker Swarm (single-node), `docker stack deploy` for zero-downtime rolling updates, nginx (in-stack) reverse proxy with caching
- **Branches covered**: Senate (100 senators), House (435 representatives), Presidents (historical + modern), Supreme Court (9 justices)
- **News Feeds**: RSS parsing (AP, NPR, PBS, BBC, The Hill, Politico, Roll Call) + Google Trends + Bluesky trending for Action Center; 41 per-state newsrooms for election races
- **Action Center**: Three tabs — Today (the day's issues, what a reader can do about each, open comment periods), Ongoing (national monitors: auto-detected ongoing concerns), Archive (year-in-review timeline). Elections live on `/elections`, find-your-members on `/politicians`
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
│   │   │   ├── analyze/         # Bill analysis, scoring, cross-referencing, action center LLM synthesis, justice scoring
│   │   │   ├── assemble/        # Scorecard builder + validator
│   │   │   ├── senate_pipeline.py, house_pipeline.py  # FETCH→TRANSFORM→ANALYZE→ASSEMBLE+SAVE per chamber
│   │   │   ├── member_lifecycle.py  # Roster reconciliation + removal of departed members (never presidents)
│   │   │   ├── stock_pipeline.py  # STOCK Act trade-disclosure ingestion (sibling phase)
│   │   │   ├── vector_store.py  # sqlite-vec + sentence-transformer model management
│   │   │   └── lexical_index.py # SQLite FTS5 keyword index (BM25F) over explore docs
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
  monitor significance and merge decisions, timeline period summaries,
  Bluesky spotlight and weekly-summary text (issue posts are the verified
  lede, verbatim), early-signal vote drafts, on-request Explore document
  summaries (`POST /api/explore/{id}/summary`, a write), and justice profile
  summaries. Never a score, and never ballot content (§7). (Per-senator/rep
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

Three narrow, disclosed exceptions exist today (bill stance direction's
tier-0 verb check, industry classification's hotel-brand tier, and donor
classification's PAC-suffix/payment-processor tier — see README
"Classification Strategy" for the full list and each one's empirical
justification). Each earns its place by the same bar: a *specific,
measured* embedding-model failure mode, documented in code at the point of
definition, running only as a precision pre-filter ahead of a genuine
embedding classifier that still handles everything the pre-filter doesn't
catch — never a replacement for one. That bar is deliberately high. "I
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
| 1 | FEC structured metadata / learning store | Unambiguous entity types, previously classified entities |
| 2 | Sentence-transformer cosine similarity | Industry, donor type, bill policy, party alignment, stance direction, procedural detection, commemorative detection, skip entity detection, employer filtering, memo transfer detection, category normalization |
| 2b | SVD / PageRank on cosponsorship matrix | Ideology scoring (Tauberer 2012), legislative leadership (Brin & Page 1998) |
| 3 | k-Nearest Neighbor in embedding space | Remaining unclassified donors and bills |
| 4 | LLM (LFM2.5-1.2B-Instruct) | Natural-language text only — Action Center claims and summaries, Bluesky posts, Explore document summaries, justice profiles (full list in the bullet above) |

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
`_NOT_ANALYSIS_PATHS` (the holdings ingest, filer matching, the run-coordination
modules, the election run's orchestration, the LDA bill-name matcher
`analyze/lobbying_records.py`, the Explore summary prompt `analyze/prompts.py`,
the modules that word and publish posts) and
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
informational Promise Persistence and Funding Diversity — use
transparent statistical formulas with no LLM input. All formulas include
inline academic citations.

Key mathematical properties:
- **Linear shrinkage**: Scores regress toward 50 when data is sparse (e.g.,
  a senator with 1 campaign promise gets a score near 50, not 0 or 100).
  Two exceptions: Constituent Alignment's vote part shrinks toward the
  party's measured typical score (its scale tops out at the seat's norm, so
  50 is below average), and Legislative Effectiveness's bill component
  doesn't shrink by bill count (a member's bills are the whole record, not
  a sample) — its leadership component is still pulled toward 50 for short
  tenure.
  The rate is the count confidence below — fixed, not estimated from the
  population's variance, so do not call it Bayesian or empirical Bayes
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
4. Rerun the generating script and commit the refreshed JSON when the
   underlying data goes stale (a new census, a population audit finding
   drift) — this is normal, expected maintenance, not a one-time setup
   step to forget about.

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
   from the day after an election — the newest pinned lines before it.
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
   skipping — and past that, the nightly chain still goes on to Stock
   trades and Election; main's startup Constituent Alignment rescore takes
   the same lease for its House part and waits for a refresh the same
   way, between passes that hold nothing — no lease, no writer — so an
   admin data reset is never refused for the wait). The pipeline process
   releases a lease a killed holder left (`district_pvi.
   release_orphaned_holds`, beside the startup run-row sweep) once it has
   gone a beat interval and a half without a beat, so a deploy mid-run
   doesn't block House runs for the lease's hour (a House run, trigger or
   the startup rescore refused by a lease still being checked waits for
   the check, `district_pvi.waits_for`); that a leftover holder is
   dead rests on the pipeline service's stop-first update order
   (`docker-compose.swarm.yml`), and the missed-beat check keeps a live
   one's lease anyway. A job still holding the outgoing Congress after a
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

   Better still, when the population a value describes is the one the
   pipeline is scoring, measure it in the run itself. Legislative
   Effectiveness's reference (per-stage bill totals, chamber median credit,
   average baseline, spread, current majority party) is computed from the members each run
   is about to score (`compute_les_reference`), persisted to
   `/data/les_reference.json` for the API's breakdowns, with
   `app/data/les_reference.json` (`scripts/calibrate_les_credit_scale.py`)
   as the pre-first-run fallback. Funding Independence's references
   (the chamber's PAC-share size fit, v6.22, and the Senate's small-donor
   baseline by state population, v6.24) work the same way (`compute_funding_reference`,
   `funding_reference.json`, `scripts/audit_pac_ratio.py`), and so does
   Constituent Alignment's per-party expected break rate by seat lean
   (`compute_constituent_reference`, `constituent_reference.json`,
   `scripts/calibrate_constituent_reference.py`); all go through
   `pipeline/analyze/population_reference.py`. A value frozen on one date
   can't track a quantity that accumulates over a congress.

This also applies to constants that are themselves the *output* of a
fitting script (regression coefficients, saturation points derived from
a residual stdev, min/max clamp ranges) — if a script prints "paste this
value into score_calculator.py," that script should instead write the
value into a JSON file the code reads, exactly like population/PVI data.

A plain hardcoded constant is still fine for a genuine, non-calculated
fact with a clear citable source that doesn't drift from run to run
(e.g. `STATE_PVI`'s Cook Political Report values, updated by hand "per
election cycle" per its own comment, or a physical/legal constant like
an FEC contribution limit). The bar is specifically about **calculated**
values — anything a regression, an average, or a percentile produced —
which must trace back to a generating script and a data file, not a
comment asserting "trust me, I ran a script once."

### 3b. Search ranking is measured, not asserted

Explore search combines four rankers — semantic kNN, BM25F keyword, recency,
and citation-graph PageRank — with weights in `config_definitions.py` under
"Explore search ranking". Those weights are the tuning surface for search
quality, and the same discipline that applies to scoring constants applies
here: **do not change a ranking weight because a result set looks better.**

`backend/scripts/evaluate_explore_search.py` is the instrument. It reports
MRR and Recall@k for each channel and for the fusion, broken out by query
style (title / paraphrase / identifier / rare-term), against the live index.
Run it before and after, and say what moved.

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

### 4. Content-based party alignment

A bill's party alignment comes from **how the parties actually voted on it**
whenever a roll call exists, and from its content (embedding similarity to
party platform positions) only when none does (`refine_with_vote_data`). The
consumer is the voted-with-party computation, and "did this member break with
their party" is defined by the parties' real split: a bill whose content
reads partisan but passed with both party majorities must not count as a
party-line vote. (Content used to win over a bipartisan split; a 2026-06
audit found that pinned every House member's score near 87–89.) Content
alignment still drives bills with no roll call and the per-area partisan
depth breakdown.

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

Senate.gov roll call XML uses multi-word last names (e.g. "Cortez Masto",
"Van Hollen", "Blunt Rochester").  The pipeline extracts the original last
name from the Congress.gov "LastName, FirstName" format during member
normalization and stores it as `lastNameForVoteMatch`.  Unicode accents are
stripped (NFD decomposition) so "Luján" matches "Lujan" in the XML.

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

**Funding is the one exception**: Funding Independence and Funding Diversity
window to the member's **most recent completed election only**
(`select_recent_elections` in `fetch/fec.py`, `n=1`: general election day
has passed — a re-election campaign still in progress is the *next*
mandate's, not the current one), not the current congress. Itemized donor
detail covers that election's full period (six years Senate, two House —
`election_period_cycles`), and every funding share is taken over
contributions, not receipts (`normalize_finance.summarize_election_totals`). Senators legitimately raise little money in the 4 non-election
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
- Absence must be able to say *which* absence it is. `MeasureCoverage` carries
  `confirmed_none` vs `not_yet_covered` / `ingest_failed`, because an empty
  measures section on a page about a state's ballot reads as "nothing to
  research" — the same null-is-not-zero discipline `Candidate.last_financials_sync`
  applies per field, applied at the collection level.
- Removed measures are rendered as removed for a grace window, not deleted.
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
  geolocation permission to personalise what's shown; any per-visitor
  identifier retained across requests to remember such a choice.
- **In:** filtering, maps, and drill-down over data the page already has;
  explicit, unremembered navigation choices (the town selector, the state
  picker); linking out to the official lookup for a reader who wants a
  precinct-exact answer. Where a district needs to be findable, give it
  place names a person already knows and let them filter: counties for a
  U.S. House seat (`county_district_crosswalk.json`), towns for a state
  legislative one (`state_leg_district_crosswalk.json`). Building that
  crosswalk is real work — `scripts/fetch_state_leg_crosswalk.py`
  documents why three obvious sources give wrong answers — and doing it
  is the price of not asking.
- **In:** following Civitas without an account. The Atom feeds (`/feeds`)
  are pulled, and a topic or state is chosen by which URL a reader
  subscribes to, so there is no subscriber list to keep. A push channel
  that would need one (email, web push, per-server webhooks) is out, for
  the same reason as an address box.
- **In, server-side only:** the Census geocoder and Google Civic's
  `voterInfoQuery`, called from the pipeline (and, for the town selector,
  from the API, cached 12 hours) with **our own** fixed,
  publicly-known building addresses (`town_directory.json`,
  `state_candidate_sources.json`'s `house_addresses`) to resolve which
  district a *known* place sits in. Every visitor who picks the same town
  sends the identical request; nothing visitor-specific leaves the server.
  That distinction — our address, not theirs — is the whole line.

The one thing counted about visitors — daily unique visits — uses an HMAC of
the IP under a random salt that exists only for the current UTC day and is
then deleted (`api/visits.py`, `VisitSalt`). A permanent key would not do:
the IPv4 space is small enough to enumerate, so anyone holding the key could
recover every stored IP. With the salt gone, nobody can.

Page-load timings (`POST /api/track-timing`, `PageLoadTiming`) are counted too,
and deliberately carry even less: the browser reports one cold load's Navigation
Timing, and the server keeps only a counter per (day, route template, metric,
bucket) — no hash, no User-Agent, no exact duration. The endpoint reads nothing
about the caller at all. Keep it that way: a timing row that could be joined to
a `SiteVisit` would turn a performance histogram into a per-visitor log.

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

The same line covers the visitor's own browser. The Action Center used to
remember a "your state" pick in `localStorage` (also read by the compare
page), a "log my action" diary with streaks, and which issues the browser had
voted on; all three were removed in 2026-09, and `ForgetLegacyStorage` clears
what earlier visits left behind. Browser storage is for a per-tab convenience
at most (`sessionStorage`, as the admin token uses), never a record of the
visitor.

A feature that can only work by asking where the visitor lives is a feature
this project doesn't ship. State the resulting limitation as content (see
§7's `omits`) rather than closing the gap by collecting an address.

## Data Pipeline

The pipeline runs nightly (configurable via `PIPELINE_CRON_SCHEDULE`) or can
be triggered manually via `POST /api/admin/pipeline/trigger`.

`scheduler.py`'s `_nightly_pipeline()` runs FIVE pipelines as a chain, in
order: **Senate → Supplementary → House → Stock trades → Election.** Each
link starts only if the previous one finished, so a failure partway down
means every pipeline after it does not run at all — and leaves no run row
behind to look wrong. `ops_alerts.check_pipeline_staleness` watches for that
(no successful completion in `PIPELINE_STALE_ALERT_DAYS`); `check_pipeline_
overrun` watches the opposite case of a run that started and is taking too
long. Note `POST /api/admin/pipeline/trigger` runs only the first three —
it cannot recover Stock trades or Election, which reach completion solely
via the nightly chain.

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
   Justice impartiality scoring (separate phase) still uses the LLM for a
   9-justice profile summary.
4. **ASSEMBLE + SAVE** — Build scorecards for senators, presidents, and
   justices; validate via `assemble/validator.py`; persist to SQLite

Between TRANSFORM and the rest, both chamber pipelines run
`member_lifecycle.py` against the roster they just fetched:

- Anyone in the database but absent from the roster is marked
  `is_current=False` with a `left_office_date`. Reversible — reappearing on
  the roster restores them and clears the clock. Skipped (with an ops alert)
  when the roster comes back implausibly small, so a truncated Congress.gov
  response can't retire a chamber, and skipped for single-member
  `senator_filter` runs.
- Anyone whose `left_office_date` is more than `RETIREMENT_GRACE_DAYS` (180)
  old is deleted, along with their child rows and the four references no
  foreign key covers. The grace period outlasts a House special election, so
  a seat mid-refill still shows who held it.

**Presidents are never reconciled or removed, and neither is the Court** —
both functions take an explicit chamber and reject anything but
`senate`/`house`. A former president is permanent site content. Relatedly,
the Senate/House leaderboards rank only currently-serving members, while the
presidential leaderboard ranks the *historical* field and excludes the
sitting president (see `president_service.get_president_leaderboard`) — for
that office, comparison against predecessors is the only meaningful ranking.

In addition to the main pipeline, the **Action Center pipeline** runs hourly to
surface trending civic issues. It fetches RSS feeds from low-bias news sources,
filters articles for U.S. policy relevance using embedding similarity, clusters
related articles, incorporates trending topics from Google Trends and Bluesky,
and uses the LLM only to *locate* attributable claims in the articles: the
summary and facts are the sources' own words, checked verbatim
(`post_composer.py`), and the recommended actions are built from real bill and
source URLs (`_build_actions_from_data`), never written by the model. Results
are stored in the `action_issues` table.

Feed descriptions are **stripped of HTML** as they are parsed
(`news_feeds._strip_html`): the WordPress-backed feeds put real markup in
`<description>`, and three consumers read that field as prose — the
policy-relevance embedding (which sees only the first 200 characters, all of
it markup for an image-led item), the LLM prompt, and the digest detector
below. Block tags become `"; "` so item boundaries survive, and the
500-character cap measures prose rather than tags.

Then **multi-story digests are dropped at ingest** (`_digest_reason`) — an
outlet's recurring briefing ("Up First", "Morning news brief", "The week in
politics") is a single RSS item covering three to five unrelated stories, and
every stage downstream treats it as one story. Two mechanical signals:

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

This has to happen here because once several stories share one article the
boundary between them is not recoverable later — cluster coherence filtering
sees one article, and a per-fact topic check does not separate the facts
(measured; see `ACTION_CENTER_PROMPT_VERSION`). Phrases that also appear on
single-topic explainers and live blogs ("what to know", "live updates") are
deliberately left out: the filter drops whole articles, so it is tuned for
precision. `articles_dropped_digest` is the counter to watch.

After issues are committed, the Action Center pipeline also:
- **Saves a timeline entry** for each day's #1 issue (permanent record for
  year-in-review tracking, stored in `timeline_entries` table)
- **Updates national monitors** — recurring topics that appear across multiple
  days are auto-detected and tracked in `national_monitors` with sourced
  timeline updates in `monitor_updates`. Existing monitors are deduplicated
  by embedding similarity; dormant monitors are marked "watching"

After both member pipelines complete, `stock_pipeline.py` runs as a sibling
phase — fetches House (PDF) and Senate (HTML) STOCK Act periodic transaction
reports plus the sitting president's OGE Form 278-T filings (PDF, from OGE's
public presidential disclosure index), matches filer to a known member (the
president's filings are indexed under the office and need no matching),
classifies trade industry (reusing the donor-industry embedding classifier),
and computes disclosure timeliness. Best-effort per phase: one source being
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
(`low == high` is the same open-ended sentinel); the pie is drawn by bracket
midpoints and says so, and no net-worth figure is produced. Reports that can't
be read are stored `parsed=False` with a reason (`scanned` paper filing,
`unrecognized` layout) and linked, not OCR'd. A Senate paper filing states
no year anywhere eFD shows it (its page is page images), so no year is
claimed or inferred for it: it ranks below every dated report, and an undated
filing (paper, or a title with no year) filed on or after the shown report's
date is named beside it ("also filed, on or after this report's filing date")
rather than guessed to be newer. Each fetch module's
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

Each senator is processed independently. The pipeline uses `PipelineRun`
records to track progress and supports resumption.

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

# Fast tests only (no embedding model; runs offline)
docker compose run --rm --no-deps backend python -m pytest tests/ -v -m "not slow"
```

Test configuration is in `backend/pytest.ini`. Async tests use
`asyncio_mode = auto`. **Mark any test that loads a sentence-transformer
model `@pytest.mark.slow`** (~10s startup). The fast job runs `-m "not
slow"` on every PR and must pass with no network access and no model; the
Embedding Tests job runs `-m slow` on pushes to `main` and on any PR that
touches `backend/requirements.txt` or `backend/app/pipeline/`.

### Environment variables

See `.env.example` for all options. Key variables:

| Variable | Required | Description |
|---|---|---|
| `DATA_GOV_API_KEY` | Yes | API key from api.data.gov |
| `ADMIN_TOKEN` | Yes | Bearer token for admin panel |
| `LLM_BACKEND` | No | `llama-server` (default) or `ollama` |
| `LLAMA_SERVER_URL` | No | llama.cpp server URL |
| `DATABASE_URL` | No | SQLite path (`docker-compose.yml` sets `sqlite:////data/civitas.db`, the volume; the code default is the relative `sqlite:///data/civitas.db`) |
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
| Stock trade disclosures | `backend/app/pipeline/stock_pipeline.py` |
| Annual-report holdings (scorecard pie) | `backend/app/pipeline/holdings_pipeline.py`, `fetch/house_fd.py`, `fetch/senate_fd.py`, `fetch/fd_common.py`, `services/holdings_service.py`, `frontend/src/components/checker/Holdings.tsx` |
| Scoring formulas | `backend/app/pipeline/analyze/score_calculator.py` |
| Industry classification (embeddings + PAC decontextualization) | `backend/app/pipeline/transform/industry_classifier.py` |
| Donor type classification (tiered + batch skip detection) | `backend/app/pipeline/analyze/donor_classifier_ai.py` |
| Bill policy area + stance derivation (embedding-based) | `backend/app/pipeline/analyze/bill_analyzer.py` |
| Commemorative bill detection (LES 1x tier; calibrated threshold) | `backend/app/pipeline/analyze/commemorative.py` + `backend/scripts/calibrate_commemorative.py` |
| Party alignment (content-based) + partisan depth | `backend/app/pipeline/analyze/party_platform.py` |
| Caucus inference (votes + cosponsorship) | `backend/app/pipeline/transform/normalize_votes.py` |
| kNN classifier + inverse-freq balancing | `backend/app/pipeline/analyze/nn_classifier.py` |
| Sponsorship analysis (PageRank leadership + SVD ideology) | `backend/app/pipeline/analyze/sponsorship_analysis.py` |
| Multi-word last name extraction + vote matching | `backend/app/pipeline/transform/normalize_members.py` |
| Lobbying match + key vote selection (deterministic, no LLM) | `backend/app/pipeline/analyze/cross_reference.py` |
| Action Center analysis (news → issues → monitors → timeline) | `backend/app/pipeline/analyze/action_center.py` |
| Justice profile summary (LLM, from pre-computed statistics) | `backend/app/pipeline/justice_pipeline.py` |
| Election cycle pipeline (candidates, financials, ballot measures, coverage) | `backend/app/pipeline/election_pipeline.py` |
| Confirmed candidates — who is really on the November ballot, per state | `backend/app/pipeline/fetch/state_candidates.py` (`STRATEGIES` dispatch) + `backend/app/data/state_candidate_sources.json` (every URL/threshold; its `_contract` key documents the config shape). Adapters are per VENDOR, not per state — adding a state already on a supported vendor is a JSON entry, never new code, and no adapter branches on a state's name. Shared office/party/surname/winner parsing lives in `state_candidates_common.py`; that is what stops per-vendor decaying into per-state. |
| Statewide ballot-measure ingestion (verbatim, no LLM) | `backend/app/pipeline/fetch/ballot_measures_pdf.py` (pipeline stage + `STRATEGIES`), one reader per state in `fetch/ballot_measures_<st>.py`, registry `backend/app/data/ballot_measure_pdf_sources.json` |
| Official-ballot link table + liveness gating | `backend/app/pipeline/fetch/ballot_lookup.py` |
| Explore hybrid search ranking (RRF fusion, priors, dedup, diversity) | `backend/app/services/explore_search.py` |
| Explore keyword index (FTS5 + BM25F) | `backend/app/pipeline/lexical_index.py` |
| Document citation graph + PageRank authority | `backend/app/pipeline/analyze/document_authority.py` |
| Search-quality evaluation harness | `backend/scripts/evaluate_explore_search.py` |
| News feed fetching (RSS) | `backend/app/pipeline/fetch/news_feeds.py` |
| Trending topic fetching | `backend/app/pipeline/fetch/trending.py` |
| Donor-vote cross-referencing | `backend/app/pipeline/analyze/policy_alignment.py` |
| Representative service + paginated votes | `backend/app/services/representative_service.py` |
| Finance normalization (embedding-based skip detection) | `backend/app/pipeline/transform/normalize_finance.py` |
| Data validation | `backend/app/pipeline/assemble/validator.py` |
| Ground-truth regression gate | `backend/app/pipeline/analyze/ground_truth.py` |
| Score/data-quality diagnostic playbook | `SCORE_AUDIT.md` |
| Enums, weights, industry codes | `backend/app/config_definitions.py` |
| Senator service + paginated votes | `backend/app/services/senator_service.py` |
| Action Center API | `backend/app/api/action.py` |
| Publishing: every post stored, then sent to Bluesky; the Atom feeds | `backend/app/broadcast.py` (`publish`, `FEEDS`), `backend/app/api/feed.py`, `frontend/src/app/feeds/page.tsx`; the posting modules (`bluesky_poster.py`, `bluesky_spotlight.py`, `congress_bluesky.py`, `election_bluesky.py`) decide what and when |
| Representative API routes | `backend/app/api/representatives.py` |
| API routes | `backend/app/api/` (senators, representatives, presidents, justices, admin, explore, action, health) |
| Frontend pages | `frontend/src/app/` (action [issues/monitors/timeline tabs], elections [state index, states/[ST] ballot, [raceId] detail], scorecard, leaderboard, explore, about, admin) |
| Frontend API client (incl. paginated vote fetching) | `frontend/src/lib/api.ts` |
| Per-client rate limits + once-per-period rules shared by every API worker | `backend/app/api/throttle.py` |
| Process roles (read-only API vs pipeline) | `backend/app/config.py` (`PROCESS_ROLE`), `backend/app/main.py` (lifespan), `backend/app/background.py`, `docker-compose.swarm.yml`, `nginx/civitas.conf` |
| Admin dashboard (tabbed sub-dashboards, SVG line charts, chart palette) | `frontend/src/app/admin/page.tsx` (shell + tabs), `frontend/src/components/admin/` |
| Share a section as an image (capture, framing, share dialog) | `frontend/src/lib/shareImage.ts`, `frontend/src/components/share/`, `frontend/src/app/photo/bioguide/[id]/route.ts` |
| Page-load timing beacon + histogram | `frontend/src/components/LoadTimingBeacon.tsx`, `backend/app/api/visits.py` (`track_timing`), `GET /api/admin/load-times` |
| SEO: per-route metadata, canonicals, JSON-LD, sitemap | `frontend/src/lib/site.ts`, `frontend/src/lib/seo.ts`, `frontend/src/app/sitemap.ts`, `backend/app/api/sitemap.py` |
| Frontend types | `frontend/src/types/` |
| Metric explanations (tooltips on all scorecard metrics) | `frontend/src/components/checker/MetricTooltip.tsx` |
| Action Center issue parts (shared with `/issue/[id]`: meta, tags, "What you can do", coverage, sources) | `frontend/src/components/action/IssueEnrichment.tsx` |
| Homepage (masthead, record index, sources panel) | `frontend/src/components/home/Masthead.tsx`, `RecordIndex.tsx`, `Holdings.tsx` |

## Conventions

### Backend (Python)

- Python 3.13+, type hints throughout
- FastAPI for HTTP, SQLAlchemy 2.0 ORM (mapped_column style), Pydantic v2 for schemas
- `async def` for API routes and fetch functions; the nightly pipeline itself runs synchronously in a background thread of the pipeline process
- Logging via `logging.getLogger(__name__)` — structured, no print statements
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
- **Read path must stay lightweight**: never load the embedding model or LLM on
  API read requests (GET endpoints). All ML inference happens at pipeline write
  time. The `senator_service.py` and `representative_service.py` read paths use
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
- Every metric shown on scorecards has a `MetricTooltip` component providing
  plain-English explanation (hover on desktop, tap on mobile). When adding new
  metrics, always add a corresponding tooltip so users can understand what they
  are seeing. The component is at `src/components/checker/MetricTooltip.tsx`.
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
  Member photos are the exception, read through the same-origin
  `/photo/bioguide/[id]` route (cached and rate-limited in nginx) — add a
  route like it (taking an id, never a URL) rather than proxying arbitrary
  URLs.
- Tabbed UIs follow the WAI-ARIA tabs pattern with a roving `tabindex`.
  Activating a tab must focus **the incoming tab**, not its panel — the
  Arrow/Home/End handler lives on the `role="tablist"` container, so moving
  focus into the panel strands the keyboard user and kills every arrow press
  after the first. The panel keeps `tabIndex=0` so Tab still reaches content.
  Focus through `focusTabWhenSelected` (`src/lib/tabFocus.ts`): it waits for
  the incoming tab to render as selected (a URL-derived selection can land a
  frame late) and drops the focus if another tab was selected first.

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
  taken from the record, not the request's spelling.
- Titles lead with what people type into a search box (member name +
  party-state, bill number, full state name); the root template appends
  " — Civitas". Search-facing wording lives in `src/lib/seo.ts`, not in
  `page.tsx` (Next rejects extra exports there).
- A missing record is a real 404 via `notFound()` plus `noindex`, never a
  200 page that says "not found". The converse holds too: an unreachable
  backend or a 5xx is an error page (`app/error.tsx`), never a 404 — fetch
  detail records through `fetchRecord` (`src/lib/ssrPayload.ts`), which
  returns null only for a 404.
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
  updates instead; Next patches the History API, copies its internal state
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

### Testing

- pytest with `asyncio_mode = auto`
- Tests live in `backend/tests/test_*.py`
- Use `SimpleNamespace` or dicts for mock data in unit tests
- Test scoring, classification, and validation logic — not LLM output
- When changing scoring logic or classification, update corresponding tests to reflect the new expected behavior

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
