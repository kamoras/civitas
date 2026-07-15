# AGENTS.md — Civitas Project Guide

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

- **Frontend**: Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS — port 3000/3001
- **Backend**: FastAPI (Python 3.13), SQLAlchemy ORM, SQLite — port 8000/8001
- **LLM**: Qwen2.5 1.5B via llama.cpp (native ARM, port 8070) or Ollama (Docker, port 11434)
- **Embeddings**: sentence-transformers (Snowflake Arctic-XS), runs in-process
- **Vector Store**: ChromaDB for semantic search document store
- **Deployment**: Docker Compose, blue/green zero-downtime via `deploy.sh`, nginx reverse proxy with caching
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
│   │   │   ├── stock_pipeline.py  # STOCK Act trade-disclosure ingestion (sibling phase)
│   │   │   └── vector_store.py  # ChromaDB + sentence-transformer model management
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
├── docker-compose.dev.yml
├── deploy.sh                    # Blue/green zero-downtime deployment
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
| 4 | LLM (Qwen2.5 1.5B) | Narrative synthesis, promise analysis, summaries, action center issue summarization |

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
  cache, ChromaDB reference corpus). The self-training system accumulates
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
population-stdev floor is the backstop that would catch a real collapse.
House has no named ground-truth reference cases (`GROUND_TRUTH` is
Senate-only) — `check_score_distribution` is House's only automated
regression gate, wired into `house_pipeline.py` alongside Senate's.

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

In addition to the main pipeline, the **Action Center pipeline** runs hourly to
surface trending civic issues. It fetches RSS feeds from low-bias news sources,
filters articles for U.S. policy relevance using embedding similarity, clusters
related articles, incorporates trending topics from Google Trends and Reddit,
and uses the LLM to generate non-partisan summaries with recommended citizen
actions. Results are stored in the `action_issues` table.

After issues are committed, the Action Center pipeline also:
- **Saves a timeline entry** for each day's #1 issue (permanent record for
  year-in-review tracking, stored in `timeline_entries` table)
- **Updates national monitors** — recurring topics that appear across multiple
  days are auto-detected and tracked in `national_monitors` with sourced
  timeline updates in `monitor_updates`. Existing monitors are deduplicated
  by embedding similarity; dormant monitors are marked "watching"

After both member pipelines complete, `stock_pipeline.py` runs as a sibling
phase — fetches House (PDF) and Senate (HTML) STOCK Act periodic transaction
reports, matches filer to a known member, classifies trade industry (reusing
the donor-industry embedding classifier), and computes disclosure timeliness.

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

**On the production Pi, `.env` is generated by `cd.yml` on every deploy from
GitHub Actions Secrets — it is not a file to hand-edit there anymore.** A
local edit on the Pi survives only until the next deploy, then gets silently
overwritten. To change a value: `gh secret set NAME` (prompts for the value),
then push to `main` or `gh workflow run cd.yml` to apply it. `.env.example`
stays the source of truth for which variables exist and what they do; local
development (`docker compose up -d`) still uses a real `.env` file as before,
since that path never goes through `cd.yml`.

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

### Testing

- pytest with `asyncio_mode = auto`
- Tests live in `backend/tests/test_*.py`
- Use `SimpleNamespace` or dicts for mock data in unit tests
- Test scoring, classification, and validation logic — not LLM output
- When changing scoring logic or classification, update corresponding tests to reflect the new expected behavior

### Deployment

- **Always use `deploy.sh`** for production deploys — never `docker compose up -d`.
  `deploy.sh` implements blue/green zero-downtime deployment: it builds the new
  image, starts it on the standby port, health-checks it, then switches nginx
  over. Running `docker compose up -d` bypasses blue/green and creates containers
  that conflict with the deploy script's port slots.
- Usage: `./deploy.sh` (all), `./deploy.sh frontend`, `./deploy.sh backend`
- Blue slot: frontend=3000, backend=8000. Green slot: frontend=3001, backend=8001.
  Active slot is tracked in `.deploy-frontend-slot` / `.deploy-backend-slot`.
- `docker compose up -d` is for **local development only** — it binds directly
  to ports 3000/8000 without blue/green rotation.
- Docker images built from `backend/Dockerfile` and `frontend/Dockerfile`
- Data persists in Docker named volumes (`app_data`) that survive rebuilds
- Frontend Dockerfile uses multi-stage build (deps → build → runner) with non-root user

### CI/CD & self-hosted runner

Pushes to `main` auto-deploy: `.github/workflows/ci.yml` runs lint/build/tests
on GitHub-hosted runners, then `.github/workflows/cd.yml` triggers on that
workflow's completion, pulls `origin/main`, and runs `deploy.sh` — no manual
SSH step needed. The deploy job runs on a self-hosted GitHub Actions runner
registered directly on the production Pi (`~/actions-runner`, systemd service
`actions.runner.kamoras-civitas.pi5-civitas`, running as user `ryan`), because
the deploy needs the machine's own Docker daemon, `.env`, and blue/green
slot-state files (`.deploy-frontend-slot` / `.deploy-backend-slot`) — it does
not check out a fresh copy, it operates on the existing checkout in place.

**Nothing in `cd.yml` is hardcoded to this specific Pi.** The checkout path
comes from `DEPLOY_PATH`, a runner-level environment variable set in
`~/actions-runner/.env` (a file the GitHub Actions runner service loads into
every job automatically) — not from the workflow file. Registering a runner
on a new/replacement device needs only the standard runner setup plus one
line, nothing repo-specific to edit:

```bash
mkdir -p ~/actions-runner && cd ~/actions-runner
# download+extract the linux-arm64 runner tarball from the latest
# github.com/actions/runner release, then:
TOKEN=$(gh api -X POST repos/kamoras/civitas/actions/runners/registration-token --jq .token)
./config.sh --url https://github.com/kamoras/civitas --token "$TOKEN" \
  --name <device-name> --labels civitas-pi --work _work --unattended
echo "DEPLOY_PATH=/path/to/your/checkout" >> .env
sudo ./svc.sh install $(whoami) && sudo ./svc.sh start
```

All 13 deploy-time secrets already live in GitHub (see below) and get written
to `.env` fresh on every deploy, so nothing credential-bearing needs to be
copied to the new device by hand either — just clone the repo to whatever
path you put in `DEPLOY_PATH` and register the runner.

**Security invariant — do not violate this:** a self-hosted runner means any
workflow job that uses it executes arbitrary shell commands on the physical
Pi. `cd.yml` is safe because it can only fire via `workflow_run` (gated to
`head_branch == 'main'` and `event == 'push'`) or manual `workflow_dispatch` —
neither of which an external contributor can trigger by opening a pull
request. **Never add `runs-on: [self-hosted, civitas-pi]` (or any
`self-hosted` label) to a job that runs on `pull_request` or
`pull_request_target` events.** Once this repo is public, a malicious PR is
the standard way self-hosted runners get compromised — GitHub's own docs warn
against this exact pattern. `ci.yml` must keep running untrusted PR code on
`ubuntu-latest` only.

- Check the runner: `sudo systemctl status actions.runner.kamoras-civitas.pi5-civitas`
- Trigger a deploy without a new commit: `gh workflow run cd.yml`
- `deploy.sh` also independently checks `gh run list --workflow CI` for HEAD
  before deploying (override with `FORCE_DEPLOY=1`) — redundant with the
  workflow gating above, kept as defense-in-depth and for manual local runs.

**`cd.yml` currently builds locally on the Pi again (temporary).** The
original design — `build-and-push` in `ci.yml` cross-builds images on
GitHub-hosted runners and pushes to GHCR, `cd.yml` pulls and runs
`SKIP_BUILD=1 ./deploy.sh` — is still there and still runs on every push to
`main`, but **`cd.yml` no longer pulls from it**. Reason: `ci.yml` first used
`ubuntu-latest` + QEMU emulation, which crashed mid-build on Node's JIT
(2026-07-12, "Illegal instruction"). The fix was switching to
`ubuntu-24.04-arm` — a *native* ARM64 hosted runner, no emulation. That
introduced a worse problem: those runners are server-grade Ampere/Cobalt
CPUs, which support instruction-set extensions the Pi 5's Cortex-A76 cores
don't. The resulting backend image SIGILL'd (exit 132) on the Pi, consistently
at ChromaDB/onnxruntime init, crash-looping in production until caught and
rolled back to a locally-built image the same day. "Native ARM64" on GitHub's
runners is not the same ISA as the Pi — cross-microarchitecture, not just
cross-emulation.

**Root cause, narrowed (2026-07-12 follow-up investigation):** `onnxruntime`
was the wrong suspect — it ships a genuine prebuilt universal aarch64 wheel
(`manylinux_2_27/2_28_aarch64`) from PyPI, so the same binary installs
regardless of build machine. `hnswlib` (ChromaDB's HNSW vector-index C++
library) has **no prebuilt aarch64 wheel anywhere** — `pip install` compiles
it from source on whichever machine runs the build, and its build system
likely auto-detects and bakes in build-host SIMD instruction support. That
lines up exactly with the crash point (first real HNSW-index use, at
ChromaDB init) and explains why building on the Pi itself is reliably safe:
the Pi compiling for itself can't produce an incompatible binary. A real fix
would pin a conservative `CXXFLAGS`/`CFLAGS` architecture target (e.g.
`-march=armv8-a`) for the build stage so hnswlib compiles to a portable
baseline regardless of build host — **untested**, don't ship this without
verifying a GHCR-built image survives past ChromaDB init on the Pi first
(the health check alone won't catch it — see the note below).

Until the build is fixed and re-verified, `cd.yml`'s `Deploy` step
runs plain `./deploy.sh` (no `SKIP_BUILD`), building on the Pi itself —
slower, but guaranteed instruction-set-compatible since it's the target
hardware. `ci.yml`'s `build-and-push` job still runs and still pushes to
GHCR; nothing currently deploys from those tags. Do not re-enable the GHCR
pull path in `cd.yml` without first confirming a GHCR-built image actually
runs on the Pi past ChromaDB init, not just that it deploys and passes the
HTTP health check (the crash happens on first real vector-store use, which
the health check doesn't touch).
