# Contributing to Civitas

Thanks for your interest in contributing. Civitas is a civic transparency project — contributions that improve data accuracy, scoring methodology, or public usability are especially welcome.

## Before you start

- Read the [README](README.md) to understand how the pipeline and scoring work
- All scores are computed from public federal records. The platform is intentionally non-partisan — changes to scoring weights or methodology require clear academic or empirical justification
- No user accounts, no tracking, no PII processing — contributions should preserve this

## Development setup

### Prerequisites

- Docker and Docker Compose
- Node.js 20+
- Python 3.11+
- A local LLM: either [llama.cpp](https://github.com/ggerganov/llama.cpp) server or [Ollama](https://ollama.ai)
- A free [api.data.gov](https://api.data.gov/signup/) key (covers Congress.gov, FEC, GovInfo, Regulations.gov)

### Running locally

```bash
git clone https://github.com/kamoras/modern-punk.git
cd modern-punk

# Copy and fill in the environment file
cp .env.example .env
# Edit .env — set DATA_GOV_API_KEY and choose LLM_BACKEND

# Start all services
docker compose up -d

# Trigger the pipeline (generates all data)
curl -X POST http://localhost:8000/api/pipeline/run \
  -H "Authorization: Bearer <PIPELINE_TRIGGER_TOKEN>"

# Frontend
cd frontend && npm install && npm run dev
```

The first pipeline run takes several hours. Subsequent nightly runs are incremental.

### Project layout

```
backend/        FastAPI app + pipeline
  app/
    api/        HTTP endpoints
    pipeline/   Data ingestion and scoring
      analyze/  Score calculators, bill classifier, calibration
    models.py   SQLAlchemy models
frontend/       Next.js 14 app
  src/
    app/        Pages (App Router)
    components/ Reusable UI components
    hooks/      Custom React hooks
    lib/        API client, formatting utils
    types/      TypeScript types
```

## Making changes

1. Fork the repository and create a branch from `main`
2. Make your changes with focused commits
3. Run tests: `cd backend && python -m pytest tests/`
4. Run the frontend build: `cd frontend && npm run build`
5. Open a pull request with a clear description of what changed and why

## What to contribute

**High value:**
- Improving score accuracy — especially edge cases where the algorithm produces counterintuitive results
- Adding test coverage for pipeline phases
- Fixing data gaps (senators/reps with missing sponsored bills, platform data, etc.)
- Performance improvements to the nightly pipeline

**Out of scope:**
- User accounts, authentication flows, or any form of user tracking
- Features that require cloud AI services (the LLM runs locally by design)
- Partisan framing in UI copy or scoring logic

## Code style

**Backend (Python):** Follow the existing patterns — SQLAlchemy 2.0 declarative style, async FastAPI endpoints, type hints throughout. Run `ruff check` before submitting.

**Frontend (TypeScript):** Next.js 14 App Router patterns. Run `npm run build` — TypeScript errors block CI.

No AI-generated comments explaining what code does. If a line needs a comment, the comment should explain *why* something non-obvious is done.

## Scoring methodology changes

Changes to scoring weights (`config_definitions.py`), normalization anchors (`score_calculator.py`), or classification logic require a written rationale citing observable behavior or academic precedent. The methodology is documented in the About page and must be kept consistent.

## License

By contributing you agree your contributions will be licensed under the project's [MIT License](LICENSE).
