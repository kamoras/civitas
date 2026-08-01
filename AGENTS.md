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
speeches, judicial opinions, and stated platforms from official government
sources, then analyzes them using embedding-based classification, content-based
party alignment, and deterministic scoring. It also features an Action Center
that surfaces trending civic issues from news feeds, auto-detects ongoing
national concerns as trackable monitors, builds a year-in-review timeline, and
provides non-partisan summaries with recommended actions. Everything runs
locally on a single self-hosted device with zero cloud AI calls.

## Architecture

- **Frontend**: Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS — port 3000 (not published to the host under Swarm — see Deployment)
- **Backend**: FastAPI (Python 3.13), SQLAlchemy ORM, SQLite — port 8000 (same)
- **LLM**: LFM2.5-1.2B-Instruct via llama.cpp (native ARM, port 8070) or Ollama (Docker, port 11434)
- **Embeddings**: sentence-transformers, two models in-process — Snowflake Arctic-XS
  (classification) and all-MiniLM-L6-v2 (search index + similarity gates)
- **Vector Store**: sqlite-vec (`vec0` virtual tables in `/data/vectors.db`) — replaced
  ChromaDB in the 2026-07 migration; see `pipeline/vector_store.py`
- **Keyword Index**: SQLite FTS5 (`explore_fts`, external-content over
  `explore_documents`) with BM25F ranking — the second retrieval channel behind
  Explore search; see `pipeline/lexical_index.py`
- **Deployment**: Docker Swarm (single-node), `docker stack deploy` for zero-downtime rolling updates, nginx (in-stack) reverse proxy with caching
- **Branches covered**: Senate (100 senators), House (435 representatives), Presidents (historical + modern), Supreme Court (9 justices)
- **News Feeds**: RSS parsing (AP, NPR, Reuters, PBS) + Google Trends + Reddit trending for Action Center
- **Action Center**: National monitors (auto-detected ongoing concerns), year-in-review timeline, elections tab

All services, models, and data run on-device. No data leaves the server.

## Repository Layout

```
civitas/
├── backend/
│   ├── app/
│   │   ├── api/                 # FastAPI route handlers (senators, representatives, presidents, justices, explore, action, admin, health)
│   │   ├── services/            # Business logic (senator_service, representative_service with paginated vote APIs)
│   │   ├── pipeline/
│   │   │   ├── fetch/           # API clients (Congress.gov, FEC, GovInfo, Senate.gov, Oyez, BLS, Federal Register)
│   │   │   ├── transform/       # Data normalization, embedding-based industry classification
│   │   │   ├── analyze/         # Bill analysis, scoring, cross-referencing, LLM narratives, justice scoring
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
│   ├── requirements.txt
│   ├── pytest.ini
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── app/                 # Next.js App Router pages (action [issues/monitors/timeline/elections/branches/globe],
│   │   │                        #   politicians [directory + per-member profile], bills, compare, explore, leaderboard,
│   │   │                        #   about, changelog, accessibility, environmental, feedback, admin)
│   │   ├── components/          # React components (action, checker, president, justice, explore, home, effects)
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
- **Statistical formulas** with Bayesian shrinkage for scoring metrics
- **LLM inference** for narrative generation, summarization, and promise
  evaluation — tasks that require natural language understanding

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
| 2 | Sentence-transformer cosine similarity | Industry, donor type, bill policy, party alignment, stance direction, procedural detection, skip entity detection, employer filtering, memo transfer detection, category normalization |
| 2b | SVD / PageRank on cosponsorship matrix | Ideology scoring (Tauberer 2012), legislative leadership (Brin & Page 1998) |
| 3 | k-Nearest Neighbor in embedding space | Remaining unclassified donors and bills |
| 4 | LLM (LFM2.5-1.2B-Instruct) | Narrative synthesis, promise analysis, summaries, action center issue summarization |

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
This fingerprint is compared to the stored hash from the last pipeline run:

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

The `normalize_learning_store()` function runs at the start of the kNN phase
to fix case inconsistencies. Stale or hallucinated category labels (e.g.,
"LEGAL", "SPORTS") are mapped to valid industries via embedding cosine
similarity against the industry description prototypes — there is no hardcoded
alias table. This prevents label fragmentation from diluting kNN vote weights.

### 3. Deterministic, auditable scoring

The five representation sub-scores (Funding Independence, Promise Persistence,
Constituent Alignment, Funding Diversity, Legislative Effectiveness) use
transparent statistical formulas with no LLM input. All formulas include
inline academic citations.

Key mathematical properties:
- **Bayesian shrinkage**: Scores regress toward 50 when data is sparse (e.g.,
  a senator with 1 campaign promise gets a score near 50, not 0 or 100)
- **Count confidence**: `min(n / threshold, 1.0)` ensures minimum sample
  sizes before trusting extreme scores
- **State-adjusted baselines**: Independent voting scores account for Cook
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

   Exception (2026-07): `_district_pvi()` / `district_pvi.json` no longer
   needs this step — `app/pipeline/fetch/district_pvi.py` refreshes it
   automatically inside the Supplementary pipeline, since it just scrapes
   whatever Cook PVI value Wikipedia's infoboxes currently show (no
   election-year window is hardcoded in the fetch itself, unlike
   state_pvi.json — see `ops_alerts.check_state_pvi_staleness` for why
   that one's sources are deliberately pinned and can't self-advance the
   same way). `scripts/fetch_district_pvi.py` still exists only to
   regenerate the bundled pre-first-ingest fallback.

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

Party alignment for bills is determined by what the bill does (embedding
similarity to party platform positions), not how senators voted on it. Vote
tallies refine but do not override the content-based signal, because senators
trade votes, face whip pressure, and make tactical compromises that don't
reflect the bill's actual ideological alignment.

Partisan depth (how strongly a senator leans D or R) is computed primarily
from the senator's actual voting record: for each policy area, the ratio of
Yea/Nay votes on D-leaning vs R-leaning bills determines the area's alignment.
Campaign promise text analysis is a secondary enrichment signal.  This follows
Poole & Rosenthal (1985) in using roll-call data as the primary indicator of
ideological position.

When available, the SVD-derived ideology score (from tier 2b sponsorship
analysis) serves as a Bayesian prior for the partisan depth calculation.
The prior weight decreases as the senator accumulates more vote data:
`data_confidence = min(partisan_vote_count / 15, 1.0)`. With 15+ votes,
the ideology prior has zero weight; with fewer votes, it regularizes the
estimate toward the senator's revealed cosponsorship ideology (Efron &
Morris 1975).

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
window to the member's **most recent election only**
(`select_recent_elections` in `fetch/fec.py`, `n=1`), not the current
congress. Senators legitimately raise little money in the 4 non-election
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

Narrower windows mean less data backs each dimension by design, not because
coverage got worse — `calculate_confidence`'s vote/bill thresholds are
recalibrated accordingly (see `score_calculator.py`), and `ground_truth.py`'s
population-distribution checks are the backstop that would catch a real
collapse. The gate derives every expectation from the current population's
own raw data (rank consistency against FEC/roll-call metrics, point-mass and
snapshot-history distribution checks — no named reference members, no
hand-typed score ranges, per principle 1/3a), so the identical checks run
for both chambers in `senate_pipeline.py` and `house_pipeline.py`.

## Data Pipeline

The pipeline runs nightly (configurable via `PIPELINE_CRON_SCHEDULE`) or can
be triggered manually via `POST /api/admin/pipeline/trigger`. It executes in
4 phases per chamber, defined in `senate_pipeline.py`/`house_pipeline.py` and
invoked by `scheduler.py`'s `_nightly_pipeline()`:

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
   donors with votes; analyze campaign promises (LLM); generate per-senator
   narratives (LLM); compute representation sub-scores; score Supreme Court
   justice impartiality
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
related articles, incorporates trending topics from Google Trends and Reddit,
and uses the LLM to generate non-partisan summaries with recommended citizen
actions. Results are stored in the `action_issues` table.

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

Each senator is processed independently. The pipeline uses `PipelineRun`
records to track progress and supports resumption.

The ANALYZE phase uses a **producer-consumer pattern** to overlap embedding
work with LLM inference. A background "Librarian" thread
(`_embedding_producer` in `senate_pipeline.py`) pre-computes all embedding-based
analyses for the next senator via `precompute_senator_analysis()` in
`cross_reference.py`, while the main "Analyst" thread waits for the LLM HTTP
response. Results flow through a bounded `queue.Queue(maxsize=3)`. On a Pi 5,
this overlaps ~2-4s of embedding work with ~15-30s LLM calls. LLM prompts use
**context compression**: platform text is distilled into concise policy topic
bullets via `_extract_platform_topics()` rather than feeding raw scraped text.

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

# Fast tests only (skip embedding model loading)
docker compose run --rm --no-deps backend python -m pytest tests/ -v \
  -k "not Embedding and not PolicyArea"
```

Test configuration is in `backend/pytest.ini`. Async tests use
`asyncio_mode = auto`. Tests marked `@pytest.mark.slow` load the
sentence-transformer model (~10s startup).

### Environment variables

See `.env.example` for all options. Key variables:

| Variable | Required | Description |
|---|---|---|
| `DATA_GOV_API_KEY` | Yes | API key from api.data.gov |
| `ADMIN_TOKEN` | Yes | Bearer token for admin panel |
| `LLM_BACKEND` | No | `llama-server` (default) or `ollama` |
| `LLAMA_SERVER_URL` | No | llama.cpp server URL |
| `DATABASE_URL` | No | SQLite path (default: `sqlite:////data/civitas.db`) |

**On the production Pi, `.env` is a hand-edited, Pi-local file** (see
"CI/CD" below for why — no GitHub Actions job ever touches the Pi
anymore, so there's no automated sync). To change a value: SSH in, edit
`.env` directly, then redeploy. `.env.example` stays the source of truth
for which variables exist and what they do; local development
(`docker compose up -d`) uses its own real `.env` file the same way it
always has.

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

## Key Modules — Where to Find Things

| What | Where |
|------|-------|
| Pipeline orchestration | `backend/app/scheduler.py` (entrypoint), `backend/app/pipeline/senate_pipeline.py` / `house_pipeline.py` |
| Departed-member detection + removal | `backend/app/pipeline/member_lifecycle.py` |
| Stock trade disclosures | `backend/app/pipeline/stock_pipeline.py` |
| Scoring formulas | `backend/app/pipeline/analyze/score_calculator.py` |
| Industry classification (embeddings + PAC decontextualization) | `backend/app/pipeline/transform/industry_classifier.py` |
| Donor type classification (tiered + batch skip detection) | `backend/app/pipeline/analyze/donor_classifier_ai.py` |
| Bill policy area + stance derivation (embedding-based) | `backend/app/pipeline/analyze/bill_analyzer.py` |
| Party alignment (content-based) + partisan depth | `backend/app/pipeline/analyze/party_platform.py` |
| Caucus inference (votes + cosponsorship) | `backend/app/pipeline/transform/normalize_votes.py` |
| kNN classifier + inverse-freq balancing | `backend/app/pipeline/analyze/nn_classifier.py` |
| Sponsorship analysis (PageRank leadership + SVD ideology) | `backend/app/pipeline/analyze/sponsorship_analysis.py` |
| Multi-word last name extraction + vote matching | `backend/app/pipeline/transform/normalize_members.py` |
| LLM narrative generation | `backend/app/pipeline/analyze/cross_reference.py` |
| Action Center analysis (news → issues → monitors → timeline) | `backend/app/pipeline/analyze/action_center.py` |
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
| Representative API routes | `backend/app/api/representatives.py` |
| API routes | `backend/app/api/` (senators, representatives, presidents, justices, admin, explore, action, health) |
| Frontend pages | `frontend/src/app/` (action [issues/monitors/timeline/elections/branches/globe], scorecard, leaderboard, explore, about, admin) |
| Frontend API client (incl. paginated vote fetching) | `frontend/src/lib/api.ts` |
| Frontend types | `frontend/src/types/` |
| Metric explanations (tooltips on all scorecard metrics) | `frontend/src/components/checker/MetricTooltip.tsx` |
| Interactive globe component | `frontend/src/components/action/GlobeTab.tsx` |
| Homepage action preview | `frontend/src/components/home/ActionPreview.tsx` |

## Conventions

### Backend (Python)

- Python 3.13+, type hints throughout
- FastAPI for HTTP, SQLAlchemy 2.0 ORM (mapped_column style), Pydantic v2 for schemas
- `async def` for API routes and fetch functions; the nightly pipeline itself runs synchronously in a background thread
- Logging via `logging.getLogger(__name__)` — structured, no print statements
- All pipeline modules use dependency injection for DB sessions
- Never store secrets in source code — all credentials come from `.env` via `pydantic-settings`
- Use parameterized queries via SQLAlchemy ORM; never concatenate user input into SQL
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
  - Backend runs with `--workers 2` in production to use multiple CPU cores
  - Nginx applies rate limiting (`limit_req_zone`) and proxy caching for
    Action Center endpoints

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
- Tabbed UIs follow the WAI-ARIA tabs pattern with a roving `tabindex`.
  Activating a tab must focus **the incoming tab**, not its panel — the
  Arrow/Home/End handler lives on the `role="tablist"` container, so moving
  focus into the panel strands the keyboard user and kills every arrow press
  after the first. The panel keeps `tabIndex=0` so Tab still reaches content.

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
