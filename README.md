# Civitas — Senator Representation Tracker

Civitas is an open-data platform that analyzes U.S. senators' voting records,
campaign donors, and stated platforms to produce a transparency scorecard showing
how well each senator represents their constituents. All scores are computed from
publicly available federal records using deterministic, auditable formulas.

## Architecture

```
┌───────────────┐     ┌───────────────┐     ┌──────────────────┐
│   Frontend    │────▶│    Backend    │────▶│  llama-server    │
│  (Next.js)    │     │  (FastAPI)    │     │  (native ARM)    │
│   port 3000   │     │   port 8000   │     │  port 8070       │
└───────────────┘     └───────┬───────┘     └──────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │  SQLite + ChromaDB │
                    │  /data volume      │
                    └───────────────────┘
```

### Data Pipeline

The pipeline runs nightly (or manually triggered) in 4 phases:

1. **FETCH** — Pull senator info, bills, votes, floor speeches, and FEC
   financial data from Congress.gov, GovInfo, and FEC APIs
2. **TRANSFORM** — Normalize financial records, classify industries and
   donor types using FEC metadata and deterministic rules
3. **ANALYZE** — Classify bills via embeddings (zero LLM), classify
   remaining donors via kNN, cross-reference donors with votes, analyze
   campaign promises (LLM), generate per-senator narratives (LLM)
4. **ASSEMBLE + SAVE** — Build senator scorecards with five representation
   sub-scores and persist to database

### Classification Strategy

The pipeline uses a tiered classification strategy that reserves expensive
techniques for cases where cheaper methods fail:

| Tier | Technique | Speed | Used For |
|------|-----------|-------|----------|
| 1 | FEC metadata / deterministic rules | Instant | Donor types, payment processors, party committees |
| 2 | Sentence-transformer embeddings (cosine similarity) | Fast | Bill policy areas, industry classification, semantic search |
| 3 | k-Nearest Neighbor in embedding space | Fast | Remaining unclassified donors (~5%) |
| 4 | LLM (Qwen 2.5 1.5B via llama.cpp) | Slow | Narrative synthesis, promise analysis, PAC identification |

A persistent learning store accumulates labeled classifications across runs,
so the system improves over time and requires fewer expensive classifications
on subsequent runs.

**Academic grounding:**
- Embedding classification: Reimers & Gurevych (2019), *Sentence-BERT*
- kNN in embedding space: Cover & Hart (1967), *Nearest Neighbor Pattern Classification*
- Dense passage retrieval: Karpukhin et al. (2020), *DPR for Open-Domain QA*

See the [Methodology page](/about) for full details and references.

## Scoring Methodology

Each senator receives five sub-scores (0-100, higher = better):

| Metric | Weight | What It Measures |
|--------|--------|------------------|
| **Funding Independence** | 25% | Small-dollar donors vs corporate PAC money |
| **Promise Persistence** | 25% | Campaign commitments kept vs broken + floor advocacy |
| **Independent Voting** | 25% | Party-line breaks on non-state-relevant votes + donor independence |
| **Transparency** | 15% | Donor traceability + industry diversity (inverse HHI) |
| **Accessibility** | 10% | Vote participation rate |

All scores default to 50 when data is insufficient. No LLM input is used
in score calculation — formulas are deterministic and auditable.

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
docker exec mp-ollama ollama pull qwen2.5:1.5b
```

The data pipeline runs automatically on the cron schedule in `.env`
(default: 3 AM daily). To trigger it manually from the admin panel
or via API:

```bash
curl -X POST http://localhost:8000/api/admin/pipeline/trigger \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

## Deployment

The project uses blue/green zero-downtime deployment via `deploy.sh`:

```bash
# Deploy backend (builds image, starts new container, health checks, swaps nginx)
./deploy.sh backend

# Deploy frontend
./deploy.sh frontend

# Deploy both
./deploy.sh
```

Data is persisted in Docker named volumes (`app_data`) that survive
container rebuilds and redeployments.

## Development Setup

For local development with hot-reload:

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
│   │   ├── api/              # FastAPI route handlers
│   │   ├── pipeline/
│   │   │   ├── fetch/        # Congress.gov, FEC, GovInfo API clients
│   │   │   ├── transform/    # Data normalization + industry classifier
│   │   │   ├── analyze/      # Bill, donor, cross-reference analysis
│   │   │   │   ├── bill_analyzer.py       # Embedding-based (zero LLM)
│   │   │   │   ├── nn_classifier.py       # kNN donor classifier
│   │   │   │   ├── cross_reference.py     # Per-senator LLM narrative
│   │   │   │   ├── score_calculator.py    # Deterministic scoring
│   │   │   │   └── ollama_client.py       # LLM backend abstraction
│   │   │   ├── assemble/     # Senator scorecard builder
│   │   │   └── orchestrator.py  # Pipeline control flow
│   │   ├── models.py         # SQLAlchemy ORM models
│   │   ├── database.py       # DB engine + session management
│   │   └── config.py         # Pydantic settings from .env
│   ├── tests/
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/app/              # Next.js app router pages
│   ├── src/components/       # React components
│   └── Dockerfile
├── deploy.sh                 # Blue/green zero-downtime deploy
├── docker-compose.yml
├── .env.example
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
| `OLLAMA_MODEL` | No | Model name for cache keys and Ollama (default: `qwen2.5:1.5b`) |
| `DATABASE_URL` | No | SQLite path (default: `sqlite:///data/modern-punk.db`) |

## License

Private repository.
