# Civitas Research

**Live at [civitas-research.org](https://civitas-research.org)** · Bluesky [@civitas-research.org](https://bsky.app/profile/civitas-research.org)

[![CI](https://github.com/kamoras/civitas/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kamoras/civitas/actions/workflows/ci.yml)
[![Lighthouse: accessibility and SEO](https://github.com/kamoras/civitas/actions/workflows/lighthouse.yml/badge.svg?branch=main)](https://github.com/kamoras/civitas/actions/workflows/lighthouse.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

Civitas Research ("Civitas" for short) is an open-data site that puts the
public record of the U.S. federal government in one place. It reads official
government records and named public sources (surveys, academic datasets,
Wikipedia, newsrooms), shows each figure with its source, and builds one
record for each senator, House representative, president and Supreme Court
justice.

Members of Congress are compared with their peers measure by measure, with no
composite grade on the site. Justices' votes are recorded but not scored.
Classification uses sentence-transformer embeddings and nearest-neighbour
votes, scoring is deterministic arithmetic, and a small local LLM is used
only to locate and quote text. Everything runs on one Raspberry Pi 5 with no
calls to cloud AI services, and the site asks visitors for no account,
address or personal data.

## Main features

- **Member records:** votes, bills, campaign money, stock trades, holdings and committee seats for every member of Congress, each measure set against the member's peers.
- **Congress:** daily, weekly and monthly reports from the Congressional Record, every bill's stage, roll calls, committees and presidential nominations.
- **Executive branch:** every president's record against the other presidencies, executive orders, and federal agencies with their budgets and documents.
- **Supreme Court:** the justices' positions, who votes with whom, each decision's lineup, and each justice's appointer estimate (not a score).
- **Action Center:** the day's civic issues from news feeds, in the sources' own words, with what a reader can do about each; ongoing national stories; a year-in-review archive.
- **Elections:** every state's ballot (federal contests, statewide offices, legislative seats and ballot measures quoted verbatim), voter deadlines, a pre-election Senate and House map, and live results on election night.
- **Search:** one search box over every page and over federal documents (Federal Register, Congressional Record, court opinions), with hybrid keyword and semantic ranking.
- **Feeds and API:** Atom feeds, Bluesky posts, and an open read-only public API with an MCP server.

## Architecture

- **Frontend:** Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS.
- **Backend:** FastAPI on Python 3.13 with SQLAlchemy and SQLite. In production one image runs as two services: a read-only API and the pipeline process (scheduler, jobs, admin API).
- **Pipeline:** a nightly chain (Senate, Supplementary, House, Stock trades, Election) plus hourly and half-hourly jobs for the Action Center, the Congressional Record and data refreshes.
- **Models:** two sentence-transformer models in process (Snowflake Arctic-XS for classification, all-MiniLM-L6-v2 for search) and LFM2.5-1.2B-Instruct served by llama.cpp.
- **Search indexes:** sqlite-vec for vectors, SQLite FTS5 (BM25F) for keywords, and a separate site search index.
- **Deployment:** single-node Docker Swarm behind an in-stack nginx reverse proxy with caching.

More detail:

| Topic | Where |
|---|---|
| System diagram, caching, project structure | [docs/architecture.md](docs/architecture.md) |
| Rendered Mermaid diagrams | [docs/diagrams/](docs/diagrams/) |
| Nightly pipeline phases, data sources, design rationale | [docs/data-pipeline.md](docs/data-pipeline.md) |
| Classification tiers and disclosed exceptions | [docs/methodology/classification.md](docs/methodology/classification.md) |
| Scoring and peer comparisons | [docs/methodology/scoring.md](docs/methodology/scoring.md), [docs/methodology/](docs/methodology/) |
| Action Center | [docs/action-center.md](docs/action-center.md) |
| Congress record, votes with the president, agencies | [docs/congress.md](docs/congress.md) |
| Elections, the map and live results | [docs/elections.md](docs/elections.md) |
| Site search and Explore's hybrid ranking | [docs/search.md](docs/search.md) |
| Feeds and Bluesky | [docs/publishing.md](docs/publishing.md) |
| Public API and MCP server | [docs/public-api.md](docs/public-api.md) |
| Frontend layout, type and performance notes | [docs/frontend.md](docs/frontend.md) |
| Operations: LLM options, alerts, Swarm, health checks, environment variables | [docs/operations.md](docs/operations.md) |
| Research notes behind method changes | [docs/research/](docs/research/) |
| Academic references | [docs/references.md](docs/references.md) |

[AGENTS.md](AGENTS.md) is the developer guide: design principles, conventions
and where each module lives.

## Run it locally

You need Docker with Compose v2, about 16 GB of RAM, about 10 GB of disk, and
a free [api.data.gov](https://api.data.gov/signup/) key.

```bash
git clone https://github.com/kamoras/civitas.git
cd civitas
cp .env.example .env
# Edit .env. At minimum set DATA_GOV_API_KEY, ADMIN_TOKEN,
# PIPELINE_TRIGGER_TOKEN and CONTACT_EMAIL (the template's tokens are refused).

# Download the LLM's weights (about 731 MB)
mkdir -p llama-models
curl -fL -o llama-models/lfm2.5-1.2b-instruct-q4_k_m.gguf \
  https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF/resolve/main/LFM2.5-1.2B-Instruct-Q4_K_M.gguf

docker compose up -d
docker compose ps
```

The frontend is at http://localhost:3000 and the API docs at
http://localhost:8000/docs. The database starts empty. The pipeline runs on
`PIPELINE_CRON_SCHEDULE` (default 03:00 UTC); to start it now:

```bash
curl -X POST http://localhost:8000/api/admin/pipeline/trigger \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

A first full load takes hours; [docs/self-hosting.md](docs/self-hosting.md)
says what fills in when. For hot reload during development:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

Ollama or your own llama.cpp build can replace the bundled LLM service: see
[docs/operations.md](docs/operations.md#llm-backend-options). Every
environment variable is listed in `.env.example`.

## Run the tests

Backend:

```bash
# Everything
docker compose run --rm --no-deps backend python -m pytest tests/ -v

# Fast tests only: no embedding model, no network
docker compose run --rm --no-deps backend python -m pytest tests/ -v -m "not slow" -n 4

# Only the embedding-model tests
docker compose run --rm --no-deps backend python -m pytest tests/ -v -m slow
```

Frontend:

```bash
cd frontend
npm run lint          # eslint, including jsx-a11y
npm test              # vitest, including the axe and contrast tests
npm run build         # type errors fail CI
npm run format:check  # prettier
```

CI runs the fast backend suite, the frontend checks and a coverage gate on
every pull request, and the slow suite on pushes to `main`
(`.github/workflows/ci.yml`). AGENTS.md's "Testing" section covers the clock
and data-volume rules every test follows.

## Deployment

Production is a single-node Docker Swarm. A cron job on the server runs
`check-and-deploy.sh` every five minutes: when `main` has a new commit with a
green CI run, it builds the images on the server, checks their trust stores,
and runs `docker stack deploy`. Swarm's start-first updates and automatic
rollback give zero-downtime deploys. Nothing in GitHub Actions runs on the
server.

To stand up a server from nothing (hardware, keys, the Swarm setup, the first
data load, domains, alerts and recovery), follow
[docs/self-hosting.md](docs/self-hosting.md). The service layout, health
checks and operator alerts are in [docs/operations.md](docs/operations.md).

## Data sources and licences

Civitas reads official sources directly: Congress.gov, the Senate and House
Clerk, GovInfo, the Federal Register, the FEC, the Lobbying Disclosure Act
registry, STOCK Act and financial disclosures, OGE, the SEC, BLS and FRED,
USAspending, Oyez and the Supreme Court Database, Voteview, the Cooperative
Election Study, state election offices, and news feeds. The full list, with
what each supplies and how recent it is, is on the site at
[/about/data](https://civitas-research.org/about/data); the fetch details are
in [docs/data-pipeline.md](docs/data-pipeline.md).

Civitas's own data (scores, breakdowns, classifications, the computed lean,
API answers and CSVs) is CC BY 4.0. Outputs built on sources with their own
terms carry those terms: the Supreme Court record is non-commercial only
(Supreme Court Database CC BY-NC 3.0 US, Oyez CC BY-NC 4.0), and
Wikipedia-derived figures are CC BY-SA 4.0. See
[/about/data#license](https://civitas-research.org/about/data#license).

## Contributing

Contributions that improve data accuracy, methodology or public usability are
welcome.

- [CONTRIBUTING.md](CONTRIBUTING.md): setup, the CI gates, and what a methodology change needs to argue.
- [AGENTS.md](AGENTS.md): the design principles and conventions a change must follow.
- [Code of Conduct](CODE_OF_CONDUCT.md): Contributor Covenant 2.1, plus a section on political subject matter.
- [Security policy](SECURITY.md): report vulnerabilities privately, not in a public issue.

## Licence

The code is licensed under the [GNU Affero General Public License v3.0](LICENSE)
(AGPL-3.0). Anyone who runs a modified version of Civitas as a network service
must publish their changes, not only anyone who redistributes it. That keeps
every hosted fork's scoring open to inspection: if you can't see the source,
you can't trust the numbers.
