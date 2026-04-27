# Civitas — Political Representation Tracker

Civitas is an open-data AI/ML platform that aggregates data from official
U.S. government sources into unified transparency scorecards for senators,
House representatives, presidents, and Supreme Court justices. It also
features an Action Center that surfaces trending civic issues from news
analysis, auto-detects ongoing national concerns as trackable monitors,
and builds a year-in-review timeline. Voting records, campaign finance,
floor speeches, judicial opinions, and stated platforms are analyzed using
embedding-based classification, content-based party alignment, and
deterministic scoring — all running locally on a Raspberry Pi 5 with zero
external API calls to cloud AI services.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          DATA SOURCES (read-only)                            │
│                                                                              │
│  Congress.gov   FEC API    GovInfo API  Senate.gov  Oyez    BLS / BEA        │
│  (bills, votes, (campaign  (bill text,  (speeches,  (SCOTUS (jobs,           │
│   members)       finance)   histories)   remarks)    cases)  GDP)            │
│                                                                              │
│  AP / NPR / Reuters / PBS (RSS)     Google Trends     Reddit r/politics      │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │  rate-limited HTTP
                           │  (Congress 1.2 RPS, FEC 0.25 RPS, GovInfo 1.0 RPS)
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                   NIGHTLY PIPELINE  (APScheduler, default 3 AM UTC)          │
│                                                                              │
│  ┌─────────┐   ┌───────────┐   ┌───────────────────────────────────────┐    │
│  │ 1.FETCH │──▶│2.TRANSFORM│──▶│            3. ANALYZE                 │    │
│  └─────────┘   └───────────┘   │                                       │    │
│                                │  ┌─────────────────┬────────────────┐ │    │
│                                │  │ Librarian thread│ Analyst thread │ │    │
│                                │  │ (batch embed)   │ (LLM + score)  │ │    │
│                                │  │                 │                │ │    │
│                                │  │ bill embeddings │ ◄── blocks on  │ │    │
│                                │  │ donor kNN       │   LLM response │ │    │
│                                │  │ lobbying match  │                │ │    │
│                                │  │ promise align   │ narrative synth│ │    │
│                                │  └─────────────────┴────────────────┘ │    │
│                                └───────────────────────────────────────┘    │
│                                                │                             │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   │   ┌──────────────────────┐ │
│  │4. EXPLORE│   │5.JUSTICES│   │6.PRESIDENTS   │   │    7. FINALIZE       │ │
│  │ (ChromaDB│   │ (Oyez)   │   │(BLS/BEA/ ◄────┘   │ (persist scores,    │ │
│  │  index)  │   │          │   │ Gallup)  │         │  PipelineRun record)│ │
│  └──────────┘   └──────────┘   └──────────┘         └──────────────────────┘ │
│                                                                              │
│  ──────────────────── HOUSE PIPELINE (runs after Senate) ──────────────────  │
│  Same 6-phase structure for all 435 representatives                          │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │ writes
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                          PERSISTENCE LAYER                                   │
│                                                                              │
│  SQLite  (modern-punk.db)                ChromaDB  (HNSW vector index)       │
│  ├── senators / representatives          └── ExploreDocument embeddings      │
│  ├── KeyVote / SponsoredBill                 (~384-dim, Snowflake Arctic-XS) │
│  ├── Donor / IndustryDonation / LobbyingMatch                                │
│  ├── CampaignPromise                                                         │
│  ├── ActionIssue / NationalMonitor / MonitorUpdate                           │
│  ├── TimelineEntry / WeekSummary / MonthSummary / YearSummary                │
│  ├── ApiCache  (raw API responses, TTL=72h, never cleared)                   │
│  ├── AnalysisCache  (LLM outputs, hash-keyed, cleared on code changes)       │
│  └── LearnedClassification  (cross-run entity learning store)                │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │ reads
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  FastAPI backend  (port 8000)                                                │
│  /api/senators      /api/representatives     /api/presidents                 │
│  /api/justices      /api/action              /api/explore                    │
│  /api/admin         /health                                                  │
│                                              ┌────────────────────────┐      │
│  APScheduler ──▶ nightly pipeline            │  llama.cpp server      │      │
│  APScheduler ──▶ hourly action refresh  ────▶│  DeepSeek-R1 1.5B      │      │
│                                              │  port 8070 (ARM-native)│      │
└──────────────────────────┬───────────────────└────────────────────────┘──────┘
                           │ JSON
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  Next.js 14 frontend  (port 3000)                                            │
│  /scorecard  /leaderboard  /action  /explore  /about  /admin                 │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Hardware:** Raspberry Pi 5 (16 GB RAM), NVMe SSD. All models, databases, and services run on-device. No cloud GPU, no third-party AI APIs, no data leaves the device.

---

## Data Pipeline: Design Rationale

The pipeline is structured around a specific set of constraints that shape every decision.

### Why a Nightly Batch Pipeline?

A 100-senator + 435-representative full refresh requires 4–6 hours cold (warm: 45–90 minutes). Online/streaming processing is not viable at these volumes on the target hardware: the sentence-transformer model occupies ~90 MB, the LLM occupies ~900 MB, and peak memory during the analyze phase (overlapped embedding + LLM) reaches ~3 GB. Batching allows us to control memory precisely, while a separate hourly pipeline handles the Action Center's lower-latency requirements.

The pipeline uses a SQLite-level mutex (`PipelineRun.status == "running"`) rather than a process-level lock, making it safe to deploy in multi-container blue/green environments: any new container discovering an in-progress run on startup marks it `stale` rather than blocking.

### Why an Adversarial Data Architecture?

Government data sources are not designed for machine consumption. Congress.gov returns inconsistent bill title formats; FEC records contain duplicate committee entries; senate.gov lacks a public API. The **FETCH → TRANSFORM** separation isolates source-specific parsing from domain logic. Every raw API response is stored verbatim in `ApiCache` (never cleared, TTL=72h), so the pipeline can be replayed deterministically against historical snapshots — essential for debugging and auditing.

Rate limits are enforced at the source level (Congress.gov: 1.2 RPS, FEC: 0.25 RPS, GovInfo: 1.0 RPS) with per-API backoff, not global throttling. This prevents a slow FEC response from stalling the Congress.gov queue.

### Why a Producer-Consumer Analyze Phase?

The analyze phase uses a **producer-consumer pattern** to overlap embedding work with LLM inference:

- **Librarian thread** (producer): Runs ahead of the current senator, pre-computing all embedding-based analyses (lobbying matches, key vote selection, promise alignment, platform topic extraction) for the *next* senator in batches of 64.
- **Analyst thread** (consumer): Blocks on LLM HTTP responses (~15–30s each), then immediately consumes the pre-computed results from the Librarian.

On a Pi 5, this overlaps 2–4s of embedding work per senator with the LLM wait, saving 200–400s across 100 senators — a ~10–15% wall-clock reduction at zero additional peak memory cost, since the Librarian only runs one senator ahead.

LLM prompts use **context compression**: platform text is distilled into concise policy-topic bullets rather than raw scraped HTML, keeping prompts within the 2,048-token context budget of the 1.5B model.

### Why Embedding-First, LLM-Sparingly?

The most important architectural decision is what *not* to use the LLM for.

The LLM is called only when the output requires:
- Natural language synthesis (per-senator narrative summaries, promise analysis text)
- Irreducibly context-dependent reasoning (reading the senator's full platform against their votes)

Everything else uses geometric methods in sentence-embedding space:

| Task | Method | Rationale |
|------|---------|-----------|
| Bill policy area | Nearest centroid (14 prototypes) | Deterministic, explainable, ~100ms vs. ~30s |
| Donor industry | k-NN (300+ labeled entities) | Generalizes from precedent; full audit trail |
| Party alignment | Nearest centroid (party platform corpora) | Content-based, not vote-based — avoids circular reasoning |
| Lobbying conflicts | Cosine similarity (donor industry ↔ bill policy) | Transparent, reproducible threshold |
| Key vote selection | Composite score: party deviation + donor overlap | Fully deterministic |
| Monitor deduplication | Pairwise cosine (full text + title-only) | Robust to surface paraphrase |
| Issue deduplication | Post-LLM title embedding similarity | Catches LLM-generated near-duplicates missed by pre-filtering |

LLM calls per full run: ~100–400 (one narrative per senator/rep, plus daily action issues). Embedding operations per run: ~50,000. The pipeline is a **semantic classification and retrieval system** that uses a language model only at the final synthesis step.

### Why Local Inference?

1. **No data exfiltration.** Senator donor records, promise evaluations, and issue analyses never leave the local network.
2. **Cost at scale.** ~500 LLM calls/day × 365 = ~180,000 calls/year. At cloud API pricing, hundreds of dollars annually. At 0 marginal cost on the Pi.
3. **Reproducibility.** Model weights are pinned. An analysis run today produces identical output to one run six months ago on the same input. Cloud-hosted models update without notice.
4. **Latency independence.** No rate limits, no network jitter, no API quota.

The choice of DeepSeek-R1 1.5B over larger alternatives (7B+) is deliberate. The inference tasks here are structured extraction — completing a constrained template (list key facts, classify stance, extract promise text) — not open-ended generation. Empirically, DeepSeek-R1 1.5B produces acceptable quality on these tasks at ~3s/call on ARM, vs. 25–45s for a 7B model. The quality ceiling is determined by the structure of the prompt, not model size.

---

## Classification Strategy — Zero Hardcoded Rules

Every classification decision in the pipeline is made mathematically. There are no hardcoded keyword lists, regex patterns, or if/else string-matching heuristics. The pipeline uses a tiered strategy following computational parsimony (Jurafsky & Martin 2023):

| Tier | Technique | Speed | Used For |
|------|-----------|-------|----------|
| 1 | FEC metadata / learning store exact match | Instant | Donor types, previously classified bills and donors |
| 2 | Sentence-transformer embeddings (cosine similarity) | Fast | Bill policy areas, industry, party alignment, donor types, stance direction, procedural detection, skip entity detection, employer filtering, memo transfer detection |
| 2b | SVD / PageRank on cosponsorship matrix | Fast | Ideology scoring (Tauberer 2012), legislative leadership (Brin & Page 1998) |
| 3 | k-Nearest Neighbor in embedding space | Fast | Remaining unclassified donors (~5%), bill classification from reference corpus |
| 4 | LLM (DeepSeek-R1 1.5B via llama.cpp) | Slow | Narrative synthesis, promise analysis, PAC identification |

Key embedding-based classification features:
- **Semantic prototypes** define each category via natural-language descriptions, not keyword lists. The embedding model matches entities to the nearest prototype by cosine similarity.
- **PAC decontextualization** detects "[Industry] PAC" naming patterns via a PAC-context prototype and margin-based runner-up selection, replacing regex suffix stripping.
- **Self-funded detection** uses SequenceMatcher ratio (Ratcliff & Obershelp 1988) for fuzzy name similarity instead of exact string matching.
- **Batch skip detection** classifies employer names and memo texts against skip prototypes in vectorized batches for performance.
- **Semantic category normalization** maps stale/unknown category labels to valid industries via embedding similarity, replacing a hardcoded alias table.
- **Stance direction** is derived from embedding similarity against pro/anti/neutral action prototypes instead of keyword patterns.
- **Procedural bill detection** uses embedding similarity against a procedural prototype instead of substring matching.

### Retrieval-Augmented Classification (RAC)

A persistent learning store (SQLite `LearnedClassification`) accumulates labeled classifications across pipeline runs. A vector reference corpus (ChromaDB) grows with each run. Together they implement a retrieval-augmented classification pattern: past decisions inform future ones, reducing both latency and error rate over time (Lewis et al. 2020).

Confidence levels distinguish source quality:
- `1.0` — rule-based (FEC metadata, exact match)
- `0.9` — embedding-based (cosine similarity classification)
- `0.7` — LLM-based (structured extraction)

This enables selective re-verification: low-confidence classifications from previous runs can be re-evaluated when related code changes.

**Version-aware artifact management** ensures updated analysis algorithms always produce fresh results. At pipeline start, a SHA-256 fingerprint of all analysis source files is compared to the stored hash from the last run. If the code has changed, stale artifacts (LLM cache, learned classifications, kNN reference corpus) are cleared so updated algorithms start clean. The API cache (raw Congress.gov / FEC / GovInfo responses) is never cleared — it reflects source data, not processing logic.

### Party Alignment (Content-Based)

Party alignment for bills is determined by **what the bill does**, not how senators voted on it. This addresses a fundamental limitation of roll-call-based ideology measures (Poole & Rosenthal 1985; Clinton, Jackman & Rivers 2004): vote outcomes reflect party discipline, logrolling, and strategic calculation as much as the bill's ideological content.

The system implements a nearest-centroid classifier (Rocchio 1971) in sentence-embedding space:
1. Each party's platform positions per policy area are embedded as centroids
2. Bill text is embedded and compared to both party centroids
3. Stance direction (pro/anti) disambiguates policy-area overlap
4. Vote tallies refine (not override) the content-based classification
5. Sponsor party data serves as supervised ground truth for adaptive learning

Independent senators have their caucus party inferred mathematically from voting patterns (proportion of votes aligning with each party), ensuring they are scored fairly against the party they actually caucus with.

---

## Action Center Pipeline (Hourly)

The Action Center is intentionally separate from the nightly pipeline because it operates on different timescales and data characteristics:

```
Every hour at :15
       │
       ▼
  1. FETCH ─── RSS (AP/NPR/Reuters/PBS) + Google Trends + Reddit
       │
       ▼
  2. FILTER ── Embed each article against 18 US policy prototypes
       │         Discard cosine_sim < 0.15 (off-topic articles)
       ▼
  3. CLUSTER ─ Pairwise cosine similarity on title embeddings
       │         Merge clusters with centroid similarity > 0.40
       ▼
  4. RANK ──── score = 0.4 × (source breadth) + 0.6 × (trending score)
       │         Select top 4 clusters (MAX_ISSUES)
       ▼
  5. LLM ───── Per cluster: neutral summary + key facts + citizen actions
       │         Post-generation title deduplication (cosine_sim > 0.82)
       ▼
  6. ENRICH ── ChromaDB semantic search → link related bills/senators
       │         Resolve bill IDs mentioned in article text
       ▼
  7. MONITORS ─ Detect cross-day recurring topics (similarity > 0.50)
       │          Create/update NationalMonitor records (min 3 days)
       │          Re-merge duplicate monitors (title OR full sim > 0.50)
       ▼
  8. TIMELINE ─ Record daily TimelineEntry
                 At week/month/year boundaries: LLM generates period summary
```

**Why cluster before ranking?** Articles about the same event arrive from multiple outlets within minutes. Without clustering, all 4 "top issues" would be the same story from AP, NPR, Reuters, and PBS. Clustering first, then ranking by source breadth, surfaces the 4 most distinct newsworthy topics.

**Why filter at 0.15 cosine similarity?** The policy prototype filter is deliberately permissive. False negatives (dropping a real policy story) are worse than false positives. The LLM step handles borderline cases through its non-partisan framing constraint.

**Why a 3-day monitor threshold?** A topic appearing on 3+ distinct days is structurally different from a one-day news spike — it indicates a developing situation citizens may need to track. A 2-day threshold creates too many ephemeral monitors.

**Why post-LLM title deduplication?** Article clusters with overlapping coverage (different outlets, slightly different angles) can have pre-LLM embedding similarity below the merge threshold (< 0.40), yet the LLM generates near-identical titles for both. A post-generation cosine similarity check at 0.82 on title embeddings catches these cases and drops the duplicate before it reaches the database.

---

## Scoring

### Non-Partisan by Construction

The scoring formulas deliberately avoid any ground truth that encodes partisan assumptions. Every dimension measures *structural behavior* against a neutral baseline, not agreement or disagreement with any policy position.

```
Overall = 0.25 × FundingIndependence
        + 0.25 × PromisePersistence
        + 0.25 × IndependentVoting
        + 0.25 × FundingDiversity

FundingIndependence = (1 − PAC_fraction) × 100
  where PAC_fraction = total_PAC_receipts / total_raised

PromisePersistence = (kept / (kept + broken + partial)) × 100
  with Bayesian shrinkage:
    α = 1 / (1 + n_promises/5)     [stronger shrinkage for fewer promises]
    score_shrunk = α × prior_mean + (1−α) × raw_score
  Prevents small-sample score inflation (Efron & Morris 1975)

IndependentVoting = (against_party_count / total_votes) × PVI_weight × 100
  where PVI_weight = 1 + (district_partisan_lean × 0.1)
  A Democrat in D+30 district needs higher deviation to reach same score
  as one in R+10 district (Carson et al. 2010)

FundingDiversity = (1 − HHI(industry_donations)) × 100
  where HHI = Σ(industry_share_i²)   [Herfindahl-Hirschman Index]
  High score = broadly-funded; low score = dominated by one sector
```

### Senate & House Scores

Each senator and House representative receives four sub-scores (0-100, higher = better):

| Metric | Weight | What It Measures | Key Reference |
|--------|--------|------------------|---------------|
| **Funding Independence** | 25% | PAC dependency + top-donor concentration | Bonica 2014; Stratmann 2005 |
| **Promise Persistence** | 25% | Campaign commitments kept + floor advocacy + participation | Naurin 2011; Martin 2011 |
| **Independent Voting** | 25% | Party-line breaks (state-adjusted) + donor independence | Carson et al. 2010 |
| **Funding Diversity** | 25% | Donor traceability + industry diversity (inverse HHI) | Rhoades 1993 |

House representatives use the same scoring framework, data sources, and classification pipeline as senators, ensuring comparable scores across chambers.

Additional senator metrics (informational, not scored):

| Metric | What It Measures | Technique |
|--------|------------------|-----------|
| **Leadership Score** | Legislative influence — how many peers cosponsor this senator's bills | PageRank on cosponsorship graph (Brin & Page 1998) |
| **Ideology Score** | Behavioral ideological position derived from cosponsorship patterns | SVD on cosponsorship matrix (Tauberer 2012) |
| **Partisan Depth** | How deeply aligned with their party across policy areas | Content-based voting analysis with SVD ideology as Bayesian prior |

### Supreme Court Justice Scores

Each justice is scored on impartiality and ideological consistency based on case-level voting data from the Oyez Project and Supreme Court APIs.

### Presidential Scores

Presidents are scored on five dimensions (Independence, Follow-Through, Public Mandate, Effectiveness, Competence) using a mix of live API data (BLS employment, Federal Register executive orders) and historical records (C-SPAN Historians Survey, Gallup approval data, BEA GDP).

All scores default to 50 when data is insufficient. No LLM input is used in score calculation — formulas are deterministic and auditable.

---

## Academic Grounding

Key algorithmic decisions and their academic backing:

| Decision | Rationale | Reference |
|----------|-----------|-----------|
| Embeddings over keywords for classification | Semantic similarity generalizes to unseen text; keywords are brittle | Reimers & Gurevych 2019 (Sentence-BERT) |
| kNN over LLM for donor classification | 5s vs 40min, no hallucinated categories, deterministic | Cover & Hart 1967; Snell et al. 2017 |
| Content-based party alignment | Vote tallies conflate strategy with ideology | Clinton, Jackman & Rivers 2004; Laver et al. 2003 |
| Learning store as experience replay | Past classifications bootstrap future accuracy | Lin 1992; Yarowsky 1995 |
| Inverse HHI for funding diversity | Standard concentration metric from IO economics | Rhoades 1993 |
| Bayesian shrinkage for promise scores | Prevents inflation when few promises are evaluable | Efron & Morris 1975 |
| Cook PVI adjustment for independence | Raw party-break rates mislead without constituency context | Carson et al. 2010 |
| Donation-vote correlation ≠ causation | Methodological caution in interpreting funding influence | Ansolabehere et al. 2003 |
| Fuzzy name matching for self-funded detection | SequenceMatcher handles spelling variations, middle names | Ratcliff & Obershelp 1988 |
| PageRank for legislative leadership | Cosponsorship network centrality measures peer influence | Brin & Page 1998; Tauberer 2012 |
| SVD ideology as Bayesian prior for partisan depth | Behavioral ideological signal regularizes sparse vote data | Poole & Rosenthal 1985; Efron & Morris 1975 |

See the [Methodology page](/about) for full details and inline citations.

---

## Prerequisites

- **Docker** and **Docker Compose** (v2)
- A free **api.data.gov** API key — sign up at https://api.data.gov/signup/
- ~16 GB RAM recommended
- ~10 GB disk for Docker images and model weights
- For native LLM inference: llama.cpp compiled for your architecture

## Quick Start

```bash
# 1. Clone the repo
git clone git@github.com:kamoras/modern-punk.git
cd modern-punk

# 2. Create your env file from the template
cp .env.example .env

# 3. Edit .env — at minimum set your API key and admin token
#    DATA_GOV_API_KEY=your-key-from-api-data-gov
#    ADMIN_TOKEN=your-secret-admin-token
nano .env

# 4. Start all services
docker compose up -d

# 5. Verify everything is running
docker compose ps

# 6. Open the app
#    Frontend: http://localhost:3000
#    API docs: http://localhost:8000/docs
```

### LLM Backend Options

The pipeline supports two LLM backends, configured via `LLM_BACKEND` in `.env`:

**Option A: llama.cpp (recommended for ARM/RPi)**
```bash
# Compile llama.cpp with ARM optimizations
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp && mkdir build && cd build
cmake .. -DCMAKE_C_FLAGS="-mcpu=cortex-a76+dotprod+fp16" \
         -DCMAKE_CXX_FLAGS="-mcpu=cortex-a76+dotprod+fp16"
cmake --build . --config Release -j4

# Install as systemd service (see deploy docs)
# Set LLM_BACKEND=llama-server in .env
```

**Option B: Ollama (simpler setup)**
```bash
# Ollama runs as a Docker container alongside the app
# Set LLM_BACKEND=ollama in .env
docker exec mp-ollama ollama pull deepseek-r1:1.5b
```

The data pipeline runs automatically on the cron schedule in `.env`
(default: 3 AM daily). To trigger it manually:

```bash
curl -X POST http://localhost:8000/api/admin/pipeline/trigger \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

## Deployment

The project uses blue/green zero-downtime deployment via `deploy.sh`:

```bash
./deploy.sh backend    # build, start, health check, swap nginx, stop old
./deploy.sh frontend
./deploy.sh            # both
```

Data is persisted in Docker named volumes (`app_data`) that survive container rebuilds and redeployments.

## Development Setup

```bash
# Start deps, then run backend and frontend with live reloading
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

### Running Backend Tests

```bash
docker compose run --rm --no-deps backend python -m pytest tests/ -v

# Fast tests only (no embedding model)
docker compose run --rm --no-deps backend python -m pytest tests/ -v \
  -k "not Embedding and not PolicyArea"
```

### Project Structure

```
modern-punk/
├── backend/
│   ├── app/
│   │   ├── api/              # FastAPI route handlers (senators, representatives, presidents, justices, explore, action, admin)
│   │   ├── services/         # Business logic (senator_service, representative_service with paginated votes)
│   │   ├── pipeline/
│   │   │   ├── fetch/        # API clients (Congress.gov, FEC, GovInfo, Senate.gov, Oyez, BLS, Federal Register, news RSS)
│   │   │   ├── transform/    # Data normalization + embedding-based industry classifier
│   │   │   ├── analyze/      # Bill, donor, party alignment, scoring, justice scorecards
│   │   │   │   ├── bill_analyzer.py          # Embedding-based bill classification + stance
│   │   │   │   ├── bill_learning.py          # Adaptive kNN reference corpus
│   │   │   │   ├── party_platform.py         # Content-based party alignment + partisan depth
│   │   │   │   ├── nn_classifier.py          # kNN donor classifier + category normalization
│   │   │   │   ├── donor_classifier_ai.py    # Tiered donor classification + batch skip
│   │   │   │   ├── sponsorship_analysis.py   # PageRank leadership + SVD ideology
│   │   │   │   ├── policy_alignment.py       # Industry↔policy area mapping
│   │   │   │   ├── cross_reference.py        # Per-senator LLM narrative
│   │   │   │   ├── action_center.py          # News analysis, LLM summarization, national monitors, timeline
│   │   │   │   ├── score_calculator.py       # Deterministic scoring formulas
│   │   │   │   └── ollama_client.py          # LLM backend abstraction
│   │   │   ├── assemble/     # Senator scorecard builder + validator
│   │   │   ├── vector_store.py  # ChromaDB + sentence-transformer
│   │   │   └── orchestrator.py  # Pipeline control flow
│   │   ├── models.py         # SQLAlchemy ORM (Senator, Representative, KeyVote, Justice, NationalMonitor, TimelineEntry, etc.)
│   │   ├── schemas.py        # Pydantic response schemas
│   │   ├── database.py       # DB engine + session management
│   │   └── config.py         # Pydantic settings from .env
│   ├── tests/                # pytest test suite
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/app/              # Next.js app router pages
│   │   ├── about/            # Methodology page with full citations
│   │   ├── action/           # Action Center (issues, monitors, timeline, elections, branches, globe)
│   │   ├── explore/          # Semantic search over government documents
│   │   ├── scorecard/        # Senator, representative, president, and justice scorecards
│   │   ├── leaderboard/      # Rankings across all branches (House paginated)
│   │   └── admin/            # Pipeline control panel with granular progress
│   ├── src/components/       # React components
│   └── Dockerfile
├── deploy.sh                 # Blue/green zero-downtime deploy
├── docker-compose.yml
├── .env.example
├── AGENTS.md                 # Project design principles and developer guide
└── README.md
```

## Environment Variables

See `.env.example` for all options. Key variables:

| Variable | Required | Description |
|---|---|---|
| `DATA_GOV_API_KEY` | Yes | API key from api.data.gov (covers Congress.gov, FEC, GovInfo) |
| `ADMIN_TOKEN` | Yes | Bearer token for admin panel and pipeline triggers |
| `LLM_BACKEND` | No | `llama-server` (default) or `ollama` |
| `LLAMA_SERVER_URL` | No | llama.cpp server URL (default: `http://host.docker.internal:8070`) |
| `OLLAMA_MODEL` | No | Model name for cache keys and Ollama (default: `deepseek-r1:1.5b`) |
| `DATABASE_URL` | No | SQLite path (default: `sqlite:///data/modern-punk.db`) |
| `PIPELINE_CRON_SCHEDULE` | No | Cron schedule for nightly pipeline (default: `0 3 * * *`) |
| `PIPELINE_CACHE_TTL_HOURS` | No | API response cache TTL (default: `72`) |

## References

Full methodology with inline citations is available on the [About page](/about).
Key references:

- Bonica, A. (2014). Mapping the Ideological Marketplace. *AJPS*, 58(2), 367-386.
- Budge, I. et al. (2001). *Mapping Policy Preferences*. Oxford UP.
- Carson, J. et al. (2010). The Electoral Costs of Party Loyalty. *AJPS*, 54(3), 598-616.
- Clinton, J., Jackman, S. & Rivers, D. (2004). The Statistical Analysis of Roll Call Data. *APSR*, 98(2), 355-370.
- Cover, T. & Hart, P. (1967). Nearest Neighbor Pattern Classification. *IEEE Trans. Info Theory*, 13(1), 21-27.
- Efron, B. & Morris, C. (1975). Data Analysis Using Stein's Estimator. *JASA*, 70(350), 311-319.
- Grimmer, J. & Stewart, B. (2013). Text as Data. *Political Analysis*, 21(3), 267-297.
- Laver, M., Benoit, K. & Garry, J. (2003). Extracting Policy Positions from Political Texts. *APSR*, 97(2).
- Lewis, P. et al. (2020). Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks. *NeurIPS 2020*.
- Poole, K. & Rosenthal, H. (1985). A Spatial Model for Legislative Roll Call Analysis. *AJPS*, 29(2), 357-384.
- Reimers, N. & Gurevych, I. (2019). Sentence-BERT. *EMNLP 2019*, 3982-3992.
- Snell, J. et al. (2017). Prototypical Networks for Few-Shot Learning. *NeurIPS 2017*, 4077-4087.
- Stratmann, T. (2005). Some Talk: Money in Politics. *Public Choice*, 124(1-2), 135-156.
- Tauberer, J. (2012). *Open Government Data*. GovTrack.us ideology/leadership methodology.
- Brin, S. & Page, L. (1998). The Anatomy of a Large-Scale Hypertextual Web Search Engine. *Proc. WWW 1998*.

## License

Private repository.
