# Architecture

Moved from the README. AGENTS.md states the rules; this page keeps the longer description and the measurements behind them.

## System diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           DATA SOURCES (read-only)                           │
│                                                                              │
│  Congress.gov   FEC API    GovInfo API  Senate.gov  Oyez    BLS / FRED       │
│  (bills, votes, (campaign  (bill text,  (speeches,  (SCOTUS (jobs,           │
│   members)       finance)   histories)   remarks)    cases)  GDP)            │
│                                                                              │
│  AP / NPR / PBS / BBC / The Hill / Politico / Roll Call (RSS)                │
│  + 41 per-state newsrooms (election coverage)                                │
│  Google Trends     Bluesky            State election offices (statewide      │
│                    (trending)          ballot measures, read directly)       │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │  rate-limited HTTP
                           │  (Congress 1.2 RPS, FEC 0.25 RPS, GovInfo 1.0 RPS)
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                NIGHTLY PIPELINE  (APScheduler, default 3 AM UTC)             │
│                                                                              │
│  ┌─────────┐   ┌───────────┐   ┌───────────────────────────────────────┐     │
│  │ 1.FETCH │──▶│2.TRANSFORM│──▶│            3. ANALYZE                 │     │
│  └─────────┘   └───────────┘   │                                       │     │
│                                │  ┌──────────────────────────────────┐ │     │
│                                │  │ per member, in order:            │ │     │
│                                │  │ embed (batches) → score → persist│ │     │
│                                │  │                                  │ │     │
│                                │  │ bill embeddings   donor kNN      │ │     │
│                                │  │ lobbying match    promise align  │ │     │
│                                │  │ (deterministic — no LLM call)    │ │     │
│                                │  │                                  │ │     │
│                                │  └──────────────────────────────────┘ │     │
│                                └───────────────────────────────────────┘     │
│                                                │                             │
│  ┌──────────┐   ┌──────────┐   ┌────────────┐  │   ┌──────────────────────┐  │
│  │4. EXPLORE│   │5.JUSTICES│   │6.PRESIDENTS│  │   │     7. FINALIZE      │  │
│  │ (sqlite- │   │ (Oyez)   │   │(BLS/FRED/  │◄─┘   │ (persist scores,     │  │
│  │  vec)    │   │          │   │ UCSB)      │      │  PipelineRun record) │  │
│  └──────────┘   └──────────┘   └────────────┘      └──────────────────────┘  │
│                                                                              │
│  ──────────────────── HOUSE PIPELINE (runs after Senate) ──────────────────  │
│  Own ~6-phase pipeline for all 435 representatives (FETCH MEMBERS, NORMALIZE,│
│  FETCH BILLS & VOTES, CLASSIFY BILLS, SPONSORSHIP ANALYSIS, FEC + SCORING) — │
│  reuses the unified EXPLORE pipeline for House floor speeches                │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │ writes
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              PERSISTENCE LAYER                               │
│                                                                              │
│  SQLite  (civitas.db)                    sqlite-vec  (vectors.db)            │
│  ├── senators / representatives          ├── vec_explore  (documents)        │
│  ├── KeyVote / SponsoredBill             └── vec_bills    (classification)   │
│  ├── Donor / IndustryDonation / LobbyingMatch                                │
│  ├── CampaignPromise                                                         │
│  ├── ActionIssue / NationalMonitor / MonitorUpdate                           │
│  ├── TimelineEntry / WeekSummary / MonthSummary / YearSummary                │
│  ├── ScoreSnapshot  (historical score tracking per senator/rep)              │
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
│  /api/admin         /api/public/v1  (open, rate-limited)    /health          │
│                                              ┌────────────────────────┐      │
│  APScheduler ──▶ nightly pipeline            │  llama.cpp server      │      │
│  APScheduler ──▶ hourly action refresh  ────▶│  LFM2.5-1.2B-Instruct  │      │
│                                              │  port 8070 (Docker)    │      │
│                                              └────────────────────────┘      │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │ JSON
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  Next.js 16 frontend  (port 3000)                                            │
│  /congress  /congress/members[/measures]  /politicians/<id>  /action         │
│  /elections  /elections/states/<ST>  /issue  /about  /changelog  /admin       │
│  /search  /explore/<key>  /compare  /accessibility  /compare/presidents      │
└──────────────────────────────────────────────────────────────────────────────┘
```

> **Rendered diagrams:** [`docs/diagrams/`](diagrams/) has Mermaid versions of
> this and eight other views — pipeline internals, classification tiers, scoring
> composition, the schema, and the deploy sequence — with more detail than ASCII
> fits. They render as pictures on GitHub. The diagrams here stay ASCII so the
> README reads the same in a terminal.

## Hardware

**Hardware:** Raspberry Pi 5 (16 GB RAM), NVMe SSD. All models, databases, and services run on-device. No cloud GPU, no third-party AI APIs, no data leaves the device. The backend image sets `HF_HUB_DISABLE_TELEMETRY=1`, so the Hugging Face libraries that fetch the embedding models send no usage reports.

## Caching

Three independent caching systems serve different purposes:

```
┌────────────────────────────────────────────────────────────────────┐
│  ApiCache  (SQLite: api_cache)                                     │
│  Key: (tier, endpoint+params_hash)   TTL: 72h (configurable)*      │
│  Stores: raw API JSON, verbatim                                    │
│  Cleared: never (source data is immutable; re-fetched after TTL)   │
│  Purpose: replay pipeline without re-hitting external APIs         │
└────────────────────────────────────────────────────────────────────┘
* Per-entity fetches expire at a per-key point in the last third of
  their TTL, so one run's entries don't all come due on one night; a
  bill's own records are kept until the hourly bill refresh sees the
  bill change (30-day backstop). See AGENTS.md, "Data Pipeline".

┌────────────────────────────────────────────────────────────────────┐
│  AnalysisCache  (SQLite: analysis_cache)                           │
│  Key: (prompt_version, SHA-256(input))                             │
│  Stores: LLM JSON output, verbatim                                 │
│  Cleared: when source file fingerprint changes (code update)       │
│  Purpose: skip LLM calls for unchanged inputs on warm reruns       │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│  LearnedClassification  (SQLite: learned_classifications)          │
│  Key: (entity_text, entity_type)                                   │
│  Stores: classification + confidence + source                      │
│  Cleared: when source file fingerprint changes                     │
│  Purpose: cross-run entity memory (kNN bootstrapping, audit trail) │
└────────────────────────────────────────────────────────────────────┘
```

The fingerprint check at pipeline start compares a SHA-256 of every analysis module's docstring-stripped AST to the hash stored in the last `PipelineRun`. If they differ, `AnalysisCache` and `LearnedClassification` are cleared so updated logic produces fresh results. `ApiCache` is never cleared by fingerprint — source data doesn't change when analysis code does.

In front of all three, public read endpoints answer with an ETag hashed from the uncompressed response body, and a matching `If-None-Match` gets a 304 (`app/api/cache_headers.py`). The ETag middleware runs inside gzip, whose header carries a timestamp; hashed outside it, the same body got a new ETag every second.

## Project structure

```
civitas/
├── backend/
│   ├── app/
│   │   ├── api/              # FastAPI route handlers
│   │   │   ├── senators.py, representatives.py, presidents.py, justices.py
│   │   │   ├── action.py     # Action center issues, monitors, timeline
│   │   │   ├── explore.py    # Hybrid (semantic + keyword) document search
│   │   │   ├── public.py     # Open read-only API (rate-limited, no auth)
│   │   │   └── admin.py      # Pipeline control panel
│   │   ├── services/         # Business logic (senator_service, representative_service)
│   │   ├── pipeline/
│   │   │   ├── fetch/        # API clients (Congress.gov, FEC, GovInfo, Senate.gov,
│   │   │   │                 #   Oyez, BLS, Federal Register, news RSS)
│   │   │   ├── transform/    # Data normalization + embedding-based industry classifier
│   │   │   ├── analyze/      # Scoring, classification, action center, Bluesky
│   │   │   │   ├── bill_analyzer.py          # Embedding-based bill classification + stance
│   │   │   │   ├── bill_learning.py          # Adaptive kNN reference corpus
│   │   │   │   ├── party_platform.py         # Party alignment (roll-call split, content fallback) + partisan depth
│   │   │   │   ├── nn_classifier.py          # kNN donor classifier + category normalization
│   │   │   │   ├── donor_classifier_ai.py    # Tiered donor classification + batch skip
│   │   │   │   ├── sponsorship_analysis.py   # PageRank leadership + SVD ideology
│   │   │   │   ├── policy_alignment.py       # Industry↔policy area mapping
│   │   │   │   ├── cross_reference.py        # Per-senator lobbying/key-vote analysis
│   │   │   │   ├── action_center.py          # News clustering, verbatim claims,
│   │   │   │   │                             #   national monitors, timeline
│   │   │   │   ├── score_calculator.py       # Deterministic scoring formulas
│   │   │   │   ├── ollama_client.py          # LLM backend abstraction
│   │   │   │   ├── bluesky_poster.py         # Post new/updated action issues
│   │   │   │   ├── bluesky_spotlight.py      # Daily member spotlight (templated)
│   │   │   │   ├── bluesky_engagement.py     # Repost/like outlet posts linking a cited article
│   │   │   │   ├── trend_poster.py           # "On the record" trend posts (templated)
│   │   │   │   ├── trend_evidence.py         # Pinned trend-to-record matching
│   │   │   │   ├── trend_relevance.py        # Trend gates: relevance, tragedy, loaded kNN
│   │   │   │   └── bluesky_utils.py          # Shared link-card builder
│   │   │   ├── assemble/     # Senator scorecard builder + validator
│   │   │   ├── vector_store.py  # sqlite-vec + both embedding models
│   │   │   ├── senate_pipeline.py, house_pipeline.py  # FETCH -> TRANSFORM ->
│   │   │   │                 #   ANALYZE -> ASSEMBLE+SAVE orchestration per chamber
│   │   │   ├── stock_pipeline.py  # STOCK Act trade-disclosure ingestion (sibling
│   │   │   │                 #   phase, runs after the member pipelines)
│   │   │   ├── supplementary_pipeline.py  # Explore docs, justices, PVI, presidents
│   │   │   ├── election_pipeline.py  # Candidates, FEC financials, ballots, coverage
│   │   │   └── president_pipeline.py, justice_pipeline.py, explore_pipeline.py
│   │   ├── scheduler.py      # Nightly cron entrypoint — calls the pipelines above
│   │   ├── models.py         # SQLAlchemy ORM (Senator, Representative, KeyVote,
│   │   │                     #   Justice, ActionIssue, NationalMonitor,
│   │   │                     #   TimelineEntry, ScoreSnapshot, etc.)
│   │   ├── schemas.py        # Pydantic response schemas
│   │   ├── database.py       # DB engine, session management, init_db (runs Alembic)
│   │   ├── config.py         # Pydantic settings from .env
│   │   └── config_definitions.py  # Score weights, industry codes, policy areas
│   ├── migrations/           # Alembic revisions (see its README)
│   ├── scripts/              # Calibration, research and audit scripts
│   ├── tests/                # pytest test suite
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/app/              # Next.js app router pages
│   │   ├── about/            # Methodology page with full citations
│   │   ├── action/           # Action Center (issues, monitors, timeline)
│   │   ├── issue/[id]/       # Permanent issue permalinks (Bluesky link target)
│   │   ├── politicians/      # Per-member profile (the directory moved to congress/members)
│   │   │   └── [id]/         #   (senator, representative, president, and justice
│   │   │                     #    scorecards all render into this one page)
│   │   ├── congress/         # Daily/weekly/monthly Congress reports; bills/ (in-motion list, any bill's page)
│   │   ├── compare/          # Side-by-side senator/representative comparison
│   │   ├── explore/          # Hybrid search over government documents
│   │   ├── elections/        # State index, per-state ballot pages (federal contests
│   │   │                     #   + statewide measures), race/candidate detail
│   │   ├── congress/members/ # Find your members (Senate, House; state, town, county)
│   │   │   └── measures/     #   The chamber measure by measure (replaced the leaderboard)
│   │   ├── environmental/    # What running the site costs (boxes from GET /api/environmental)
│   │   ├── accessibility/    # Accessibility statement
│   │   ├── feedback/         # On-site feedback form -> files a GitHub issue
│   │   ├── changelog/        # Scoring-algorithm version history
│   │   └── admin/            # Pipeline control panel with granular progress
│   ├── src/components/       # React components
│   └── Dockerfile
├── check-and-deploy.sh       # Cron poller: builds images + docker stack deploy
├── docker-compose.yml
├── docker-compose.swarm.yml  # Swarm-only overlay (production stack deploy)
├── nginx/                    # Dockerfile + static config (in-stack reverse proxy)
├── .env.example
├── AGENTS.md                 # Project design principles and developer guide
└── README.md
```
