# AGENTS.md — Civitas Project Guide

## Project Overview

Civitas is an AI/ML political transparency platform that scores U.S. senators on
how well they represent constituents. It aggregates voting records, campaign
finance, floor speeches, and stated platforms from official government sources,
then analyzes them using embedding-based classification, content-based party
alignment, and deterministic scoring. Everything runs locally on a Raspberry Pi 5
with zero cloud AI calls.

## Architecture

- **Frontend**: Next.js 14 (App Router), React 18, TypeScript, Tailwind CSS — port 3000
- **Backend**: FastAPI (Python 3.12), SQLAlchemy ORM, SQLite — port 8000
- **LLM**: Qwen 2.5 1.5B via llama.cpp (native ARM, port 8070) or Ollama (Docker, port 11434)
- **Embeddings**: sentence-transformers (all-MiniLM-L6-v2), runs in-process
- **Vector Store**: ChromaDB for semantic search document store
- **Deployment**: Docker Compose, blue/green zero-downtime via `deploy.sh`, nginx reverse proxy

All services, models, and data run on-device. No data leaves the Raspberry Pi.

## Repository Layout

```
modern-punk/
├── backend/
│   ├── app/
│   │   ├── api/                 # FastAPI route handlers
│   │   ├── pipeline/
│   │   │   ├── fetch/           # API clients (Congress.gov, FEC, GovInfo, Senate.gov)
│   │   │   ├── transform/       # Data normalization, industry/donor classification
│   │   │   ├── analyze/         # Bill analysis, scoring, cross-referencing, LLM narratives
│   │   │   ├── assemble/        # Scorecard builder + validator
│   │   │   ├── orchestrator.py  # Pipeline control flow (FETCH→TRANSFORM→ANALYZE→ASSEMBLE+SAVE)
│   │   │   └── vector_store.py  # ChromaDB + sentence-transformer model management
│   │   ├── services/            # Business logic layer between API and DB
│   │   ├── models.py            # SQLAlchemy ORM models
│   │   ├── schemas.py           # Pydantic response schemas
│   │   ├── database.py          # DB engine + session management
│   │   ├── config.py            # Pydantic settings from .env
│   │   ├── config_definitions.py # Enums, weights, industry codes (single source of truth)
│   │   └── main.py              # FastAPI app with lifespan hooks
│   ├── tests/                   # pytest test suite
│   ├── requirements.txt
│   ├── pytest.ini
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── app/                 # Next.js App Router pages (about, admin, explore, leaderboard, scorecard)
│   │   ├── components/          # React components
│   │   ├── hooks/               # Custom React hooks
│   │   ├── lib/                 # API client, utilities
│   │   └── types/               # TypeScript type definitions
│   ├── package.json
│   └── Dockerfile
├── docker-compose.yml
├── docker-compose.dev.yml
├── deploy.sh                    # Blue/green zero-downtime deployment
├── .env.example                 # Template for environment variables
└── README.md
```

## Core Design Principles

### 1. ML/AI over hardcoded rules

Classification decisions (donor industry, bill policy area, party alignment,
donor type) must use embedding-based similarity, kNN, or LLM — not regex
patterns or keyword lookups. Hardcoded string matching is acceptable only for
**preprocessing** (e.g., stripping "PAC" suffix from entity names before
embedding) but never for making classification decisions.

The tiered classification strategy reserves expensive techniques for cases
where cheaper methods fail:

| Tier | Technique | Used For |
|------|-----------|----------|
| 1 | FEC metadata / learning store exact match | Donor types, previously seen entities |
| 2 | Sentence-transformer embeddings (cosine similarity) | Industry classification, bill policy, party alignment |
| 3 | k-Nearest Neighbor in embedding space | Remaining unclassified donors and bills |
| 4 | LLM (Qwen 2.5 1.5B) | Narrative synthesis, promise analysis |

### 2. Self-correcting learning store

The persistent learning store (SQLite `learned_classifications` table)
accumulates labeled classifications across pipeline runs. When the embedding
model disagrees with a cached entry at high confidence, the embedding result
overrides the stale entry. This keeps the store self-correcting as industry
descriptions improve — no manual data cleanup needed.

### 3. Deterministic, auditable scoring

The four representation sub-scores (Funding Independence, Promise Persistence,
Independent Voting, Funding Diversity) use transparent formulas with no LLM
input. Missing data yields a neutral 50, never 0 or 100. Score formulas live
in `score_calculator.py` with inline academic citations.

### 4. Content-based party alignment

Party alignment for bills is determined by what the bill does (embedding
similarity to party platform positions), not how senators voted on it. Vote
tallies refine but do not override the content-based signal.

### 5. Config as single source of truth

All dynamic enums, category codes, industry definitions, score weights, and
policy areas are defined in `config_definitions.py`. The frontend fetches
these from `GET /api/config`. Never duplicate these definitions.

## Data Pipeline

The pipeline runs nightly (configurable via `PIPELINE_CRON_SCHEDULE`) or can
be triggered manually via `POST /api/admin/pipeline/trigger`. It executes in
4 phases defined in `orchestrator.py`:

1. **FETCH** — Pull senators, bills, roll-call votes, floor speeches, FEC
   financial data from Congress.gov, Senate.gov, GovInfo, and FEC APIs
2. **TRANSFORM** — Normalize financial records, classify industries and donor
   types using FEC metadata + embeddings
3. **ANALYZE** — Classify bill policy areas and party alignment via embeddings,
   classify remaining donors via kNN, cross-reference donors with votes,
   analyze campaign promises (LLM), generate per-senator narratives (LLM),
   compute four representation sub-scores
4. **ASSEMBLE + SAVE** — Build senator scorecards, validate via
   `assemble/validator.py`, persist to SQLite

Each senator is processed independently. The pipeline uses `PipelineRun`
records to track progress and supports resumption.

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

**Note:** `test_bill_analyzer.py` has an unrelated import issue
(`PROCEDURAL_KEYWORDS`) — exclude it with `--ignore=tests/test_bill_analyzer.py`
if it blocks the rest of the suite.

### Environment variables

See `.env.example` for all options. Key variables:

| Variable | Required | Description |
|---|---|---|
| `DATA_GOV_API_KEY` | Yes | API key from api.data.gov |
| `ADMIN_TOKEN` | Yes | Bearer token for admin panel |
| `LLM_BACKEND` | No | `llama-server` (default) or `ollama` |
| `LLAMA_SERVER_URL` | No | llama.cpp server URL |
| `DATABASE_URL` | No | SQLite path (default: `sqlite:////data/modern-punk.db`) |

### Database

SQLite at `/data/modern-punk.db` inside the container (Docker volume
`modern-punk_app_data`). On the host:
```bash
sudo sqlite3 /var/lib/docker/volumes/modern-punk_app_data/_data/modern-punk.db
```

SQLAlchemy ORM models are in `backend/app/models.py`. Key tables: `senators`,
`key_votes`, `donors`, `industry_donations`, `campaign_promises`,
`lobbying_matches`, `learned_classifications`, `explore_documents`,
`pipeline_runs`.

## Key Modules — Where to Find Things

| What | Where |
|------|-------|
| Pipeline orchestration | `backend/app/pipeline/orchestrator.py` |
| Scoring formulas | `backend/app/pipeline/analyze/score_calculator.py` |
| Industry classification (embeddings) | `backend/app/pipeline/transform/industry_classifier.py` |
| Donor type classification (tiered) | `backend/app/pipeline/analyze/donor_classifier_ai.py` |
| Bill policy area + party alignment | `backend/app/pipeline/analyze/bill_analyzer.py`, `party_platform.py` |
| kNN classifier | `backend/app/pipeline/analyze/nn_classifier.py` |
| LLM narrative generation | `backend/app/pipeline/analyze/cross_reference.py` |
| Donor-vote cross-referencing | `backend/app/pipeline/analyze/policy_alignment.py` |
| Finance normalization | `backend/app/pipeline/transform/normalize_finance.py` |
| Data validation | `backend/app/pipeline/assemble/validator.py` |
| Enums, weights, industry codes | `backend/app/config_definitions.py` |
| API routes | `backend/app/api/` (senators, admin, explore, health, etc.) |
| Frontend pages | `frontend/src/app/` (scorecard, leaderboard, explore, about, admin) |
| Frontend API client | `frontend/src/lib/api.ts` |
| Frontend types | `frontend/src/types/` |

## Conventions

### Backend (Python)

- Python 3.12+, type hints throughout
- FastAPI for HTTP, SQLAlchemy 2.0 ORM (mapped_column style), Pydantic v2 for schemas
- `async def` for API routes and fetch functions; pipeline orchestrator runs synchronously in a background thread
- Logging via `logging.getLogger(__name__)` — structured, no print statements
- All pipeline modules use dependency injection for DB sessions
- Never store secrets in source code — all credentials come from `.env` via `pydantic-settings`
- Use parameterized queries via SQLAlchemy ORM; never concatenate user input into SQL

### Frontend (TypeScript)

- Next.js 14 App Router with server components where possible
- TypeScript strict mode, types in `src/types/`
- Tailwind CSS for styling
- API calls go through `src/lib/api.ts`
- Dynamic configuration fetched from `GET /api/config` — never hardcode industry codes, score weights, or category labels

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
