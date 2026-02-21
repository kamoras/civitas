# Civitas — Senator Representation Tracker

Civitas is an AI-powered platform that analyzes U.S. senators' voting records,
campaign donors, and stated platforms to produce a transparency scorecard showing
how well each senator represents their constituents.

## Architecture

```
┌───────────────┐     ┌───────────────┐     ┌───────────────┐
│   Frontend    │────▶│    Backend    │────▶│    Ollama     │
│  (Next.js)    │     │  (FastAPI)    │     │  (Local LLM)  │
│   port 3000   │     │   port 8000   │     │  port 11434   │
└───────────────┘     └───────┬───────┘     └───────────────┘
                              │
                    ┌─────────┴─────────┐
                    │   SQLite + ChromaDB │
                    │   /data volume      │
                    └─────────────────────┘
```

**Data pipeline** (runs nightly via cron, or manually triggered):

1. **FETCH** — Pull senator info, bills, votes, and FEC financial data from
   Congress.gov, GovInfo, and FEC APIs
2. **TRANSFORM** — Normalize financial records, classify industries (embeddings),
   classify donor types (FEC metadata + rules)
3. **ANALYZE** — Classify bill policy areas (embeddings + LLM), analyze PACs,
   summarize votes, cross-reference donors with votes
4. **ASSEMBLE + SAVE** — Build senator scorecards with five representation
   sub-scores and persist to database

The pipeline uses a tiered classification strategy to minimize LLM calls:
structured data first, then deterministic rules, then embedding similarity,
with LLM reserved as a fallback. A learning store persists classifications
across runs so the system improves over time.

## Prerequisites

- **Docker** and **Docker Compose** (v2)
- A free **api.data.gov** API key — sign up at https://api.data.gov/signup/
- ~16 GB RAM recommended (Ollama needs ~4 GB for the LLM model)
- ~10 GB disk for Docker images and model weights

## Quick Start

```bash
# 1. Clone the repo
git clone git@github.com:kamoras/modern-punk.git
cd modern-punk

# 2. Create your env file from the template
cp .env.example .env

# 3. Edit .env — at minimum set your API key
#    DATA_GOV_API_KEY=your-key-from-api-data-gov
nano .env

# 4. Start all services
docker compose up -d

# 5. Pull the LLM model (first time only, ~1.6 GB download)
docker exec mp-ollama ollama pull gemma2:2b

# 6. Verify everything is running
docker compose ps
# All three services should show "healthy" or "running"

# 7. Open the app
#    Frontend: http://localhost:3000
#    API docs: http://localhost:8000/docs
```

The data pipeline runs automatically on the cron schedule in `.env`
(default: 3 AM daily). To trigger it manually:

```bash
curl -X POST http://localhost:8000/api/pipeline/run \
  -H "Authorization: Bearer $PIPELINE_TRIGGER_TOKEN"
```

## Development Setup

For local development with hot-reload:

```bash
# Start Ollama + deps, then run backend and frontend with live reloading
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

This mounts `backend/app/` and `frontend/src/` as volumes so code changes
take effect immediately without rebuilding images.

### Running Backend Tests

```bash
# Run the full test suite inside Docker (includes embedding model)
docker compose run --rm --no-deps backend python -m pytest tests/ -v

# Run only fast tests (no embedding model needed)
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
│   │   │   ├── analyze/      # Bill, donor, vote, PAC analysis
│   │   │   ├── assemble/     # Senator scorecard builder
│   │   │   └── orchestrator.py  # Pipeline control flow
│   │   ├── models.py         # SQLAlchemy ORM models
│   │   ├── database.py       # DB engine + session management
│   │   └── config.py         # Pydantic settings from .env
│   ├── tests/                # pytest test suite
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── app/              # Next.js app router pages
│   │   ├── components/       # React components
│   │   └── types/            # TypeScript types
│   ├── package.json
│   └── Dockerfile
├── nginx/
│   └── nginx.conf            # Reverse proxy config (optional)
├── docker-compose.yml        # Production compose
├── docker-compose.dev.yml    # Dev overrides (hot-reload)
├── .env.example              # Template for environment variables
└── .github/workflows/
    ├── ci.yml                # Lint + test on push/PR
    └── deploy.yml            # Build ARM64 images on tags
```

## Environment Variables

See `.env.example` for all options. The only required variable is:

| Variable | Required | Description |
|---|---|---|
| `DATA_GOV_API_KEY` | Yes | API key from api.data.gov (covers Congress.gov, FEC, GovInfo) |
| `OLLAMA_MODEL` | No | LLM model name (default: `gemma2:2b`) |
| `PIPELINE_TRIGGER_TOKEN` | No | Bearer token for manual pipeline triggers |
| `DATABASE_URL` | No | SQLite path (default: `sqlite:///data/modern-punk.db`) |

## Reverse Proxy (Optional)

For production deployments behind a reverse proxy, an nginx config is provided
in `nginx/nginx.conf`. It routes `/api/*` to the backend and everything else
to the frontend. You can either:

- Use the host's nginx and point it at ports 3000/8000
- Add an nginx container to `docker-compose.yml` using the provided config

## Scoring Methodology

Each senator receives five sub-scores (0-100, higher = better):

- **Constituent Funding** — Ratio of small donors vs PAC money
- **Independence Index** — Joint signal of PAC dependence and pro-corporate voting
- **Donor Diversity** — Inverse Herfindahl-Hirschman Index of industry concentration
- **Promise Fulfillment** — Campaign platform alignment with voting record
- **Accountability** — Tenure, industry concentration, and PAC dependence composite

## License

Private repository.
