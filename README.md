# Civitas — Political Representation Tracker

Civitas is an open-data AI/ML platform that aggregates data from official
U.S. government sources into a unified transparency scorecard for elected
officials. Voting records, campaign finance, floor speeches, and stated
platforms are analyzed using embedding-based classification, content-based
party alignment, and deterministic scoring — all running locally on a
Raspberry Pi 5 with zero external API calls to cloud AI services.

## Architecture

```
┌───────────────┐     ┌───────────────┐     ┌──────────────────┐
│   Frontend    │────▶│    Backend    │────▶│  llama-server    │
│  (Next.js 14) │     │  (FastAPI)    │     │  (native ARM)    │
│   port 3000   │     │   port 8000   │     │  port 8070       │
└───────────────┘     └───────┬───────┘     └──────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │  SQLite + ChromaDB │
                    │  /data volume      │
                    └───────────────────┘
```

**Hardware:** Raspberry Pi 5 (16 GB RAM), NVMe SSD.  All models,
databases, and services run on-device.  No cloud GPU, no third-party
AI APIs, no data leaves the device.

### Data Pipeline

The pipeline runs nightly (or manually triggered) in 5 phases:

1. **FETCH** — Pull senator info, bills (with sponsor party), roll-call
   votes, floor speeches, and FEC financial data from Congress.gov,
   Senate.gov, GovInfo, and FEC APIs
2. **TRANSFORM** — Normalize financial records, classify industries and
   donor types using FEC metadata and deterministic rules
3. **ANALYZE** — Classify bill policy areas and party alignment via
   embeddings (zero LLM), classify remaining donors via kNN,
   cross-reference donors with votes, analyze campaign promises (LLM),
   generate per-senator narratives (LLM)
4. **SCORE** — Compute four representation sub-scores from real data
   using deterministic, auditable formulas
5. **ASSEMBLE + SAVE** — Build senator scorecards and persist to database

### Classification Strategy

The pipeline uses a tiered classification strategy that reserves expensive
techniques for cases where cheaper methods fail, following the computational
parsimony principle from Jurafsky & Martin (2023):

| Tier | Technique | Speed | Used For |
|------|-----------|-------|----------|
| 1 | FEC metadata / learning store exact match | Instant | Donor types, previously classified bills and donors |
| 2 | Sentence-transformer embeddings (cosine similarity) | Fast | Bill policy areas, industry classification, party alignment, semantic search |
| 3 | k-Nearest Neighbor in embedding space | Fast | Remaining unclassified donors (~5%), bill classification from reference corpus |
| 4 | LLM (Qwen 2.5 1.5B via llama.cpp) | Slow | Narrative synthesis, promise analysis, PAC identification |

A persistent learning store (SQLite) accumulates labeled classifications
across pipeline runs. A vector reference corpus (ChromaDB) grows with
each run. Together they implement a retrieval-augmented classification
(RAC) pattern: past decisions inform future ones, reducing both latency
and error rate over time (Lewis et al. 2020).

### Party Alignment (Content-Based)

Party alignment for bills is determined by **what the bill does**, not
how senators voted on it. This addresses a fundamental limitation of
roll-call-based ideology measures (Poole & Rosenthal 1985; Clinton,
Jackman & Rivers 2004): vote outcomes reflect party discipline, logrolling,
and strategic calculation as much as the bill's ideological content.

The system implements a nearest-centroid classifier (Rocchio 1971) in
sentence-embedding space:
1. Each party's platform positions per policy area are embedded as centroids
2. Bill text is embedded and compared to both party centroids
3. Stance direction (pro/anti) disambiguates policy-area overlap
4. Vote tallies refine (not override) the content-based classification
5. Sponsor party data serves as supervised ground truth for adaptive learning

This follows the manifesto analysis tradition (Budge et al. 2001; Laver,
Benoit & Garry 2003) extended to individual bills.

### Academic Grounding

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

See the [Methodology page](/about) for full details and inline citations.

## Scoring Methodology

Each senator receives four sub-scores (0-100, higher = better):

| Metric | Weight | What It Measures | Key Reference |
|--------|--------|------------------|---------------|
| **Funding Independence** | 25% | PAC dependency + top-donor concentration | Bonica 2014; Stratmann 2005 |
| **Promise Persistence** | 25% | Campaign commitments kept + floor advocacy + participation | Naurin 2011; Martin 2011 |
| **Independent Voting** | 25% | Party-line breaks (state-adjusted) + donor independence | Carson et al. 2010 |
| **Funding Diversity** | 25% | Donor traceability + industry diversity (inverse HHI) | Rhoades 1993 |

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
│   │   │   ├── fetch/        # Congress.gov, FEC, GovInfo, Senate.gov clients
│   │   │   ├── transform/    # Data normalization + industry classifier
│   │   │   ├── analyze/      # Bill, donor, party alignment, scoring
│   │   │   │   ├── bill_analyzer.py       # Embedding-based bill classification
│   │   │   │   ├── bill_learning.py       # Adaptive kNN reference corpus
│   │   │   │   ├── party_platform.py      # Content-based party alignment
│   │   │   │   ├── nn_classifier.py       # kNN donor classifier
│   │   │   │   ├── donor_classifier_ai.py # Tiered donor classification
│   │   │   │   ├── policy_alignment.py    # Industry↔policy area mapping
│   │   │   │   ├── cross_reference.py     # Per-senator LLM narrative
│   │   │   │   ├── score_calculator.py    # Deterministic scoring formulas
│   │   │   │   └── ollama_client.py       # LLM backend abstraction
│   │   │   ├── assemble/     # Senator scorecard builder + validator
│   │   │   ├── vector_store.py  # ChromaDB + sentence-transformer
│   │   │   └── orchestrator.py  # Pipeline control flow
│   │   ├── models.py         # SQLAlchemy ORM (Senator, KeyVote, LearnedClassification)
│   │   ├── database.py       # DB engine + session management
│   │   └── config.py         # Pydantic settings from .env
│   ├── tests/
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/app/              # Next.js app router pages
│   │   ├── about/            # Methodology page with full citations
│   │   ├── explore/          # Semantic search over government documents
│   │   ├── scorecard/        # Senator scorecards
│   │   ├── leaderboard/      # Rankings
│   │   └── admin/            # Pipeline control panel
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

## References

Full methodology with inline citations is available on the [About page](/about).
Key references:

- Bonica, A. (2014). Mapping the Ideological Marketplace. *AJPS*, 58(2), 367-386.
- Budge, I. et al. (2001). *Mapping Policy Preferences*. Oxford UP.
- Carson, J. et al. (2010). The Electoral Costs of Party Loyalty. *AJPS*, 54(3), 598-616.
- Clinton, J., Jackman, S. & Rivers, D. (2004). The Statistical Analysis of Roll Call Data. *APSR*, 98(2), 355-370.
- Cover, T. & Hart, P. (1967). Nearest Neighbor Pattern Classification. *IEEE Trans. Info Theory*, 13(1), 21-27.
- Grimmer, J. & Stewart, B. (2013). Text as Data. *Political Analysis*, 21(3), 267-297.
- Laver, M., Benoit, K. & Garry, J. (2003). Extracting Policy Positions from Political Texts. *APSR*, 97(2).
- Lewis, P. et al. (2020). Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks. *NeurIPS 2020*.
- Reimers, N. & Gurevych, I. (2019). Sentence-BERT. *EMNLP 2019*, 3982-3992.
- Snell, J. et al. (2017). Prototypical Networks for Few-Shot Learning. *NeurIPS 2017*, 4077-4087.
- Stratmann, T. (2005). Some Talk: Money in Politics. *Public Choice*, 124(1-2), 135-156.

## License

Private repository.
