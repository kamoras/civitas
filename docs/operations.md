# Operations

Moved from the README. AGENTS.md states the rules; this page keeps the longer description and the measurements behind them.

To stand up a server from nothing, start with [self-hosting.md](self-hosting.md).

## LLM backend options

The pipeline supports two LLM backends, configured via `LLM_BACKEND` in `.env`:

**Option A: llama.cpp via Docker (default, what production runs)**

`docker-compose.yml` already includes a `llama-server` service running the
official [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) server
image — nothing to build. Download a `.gguf` model (e.g. from Hugging Face)
into a local directory and point `.env` at it:

```bash
mkdir -p llama-models
# download your .gguf model into llama-models/, matching LLAMA_MODEL_FILE in .env
# LLAMA_MODELS_DIR=./llama-models
# LLAMA_MODEL_FILE=lfm2.5-1.2b-instruct-q4_k_m.gguf
# LLM_BACKEND=llama-server (default)
```

Dependabot tracks this image the same way it tracks every other
dependency in this repo (`.github/dependabot.yml`'s `docker` ecosystem) —
version bumps arrive as ordinary PRs, no manual "check for a new
llama.cpp release" step. A from-source ARM-optimized build
(`-mcpu=cortex-a76+dotprod+fp16`) was live-benchmarked against this image
on the production Pi 5 (2026-08-29): prompt-processing was ~12% faster,
but token-generation speed — what actually dominates wall-clock for every
real call here — was statistically identical, so the custom build wasn't
worth losing automatic updates over. If your workload is more
concurrency- or prefill-heavy than this project's once-daily batch
pipeline, building from source may still be worth it for you. On a Pi 5,
skip the compile: grab the precompiled binary from
[Releases](https://github.com/kamoras/civitas/releases/tag/llama-server-pi5-v1)
(checksum included; Cortex-A76 only, won't run on a Pi 4 or other boards).

```bash
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp && mkdir build && cd build
cmake .. -DCMAKE_C_FLAGS="-mcpu=cortex-a76+dotprod+fp16" \
         -DCMAKE_CXX_FLAGS="-mcpu=cortex-a76+dotprod+fp16"
cmake --build . --config Release -j4
# Point LLAMA_SERVER_URL at wherever you run it instead of using the
# bundled Docker service.
```

**Option B: Ollama (simpler setup)**
```bash
# Ollama is not bundled in docker-compose.yml — run your own instance
# (host install or a compose service you add yourself), then:
# Set LLM_BACKEND=ollama and OLLAMA_BASE_URL in .env
ollama pull LiquidAI/lfm2.5-1.2b-instruct
```

The data pipeline runs automatically on the cron schedule in `.env`
(default: 3 AM daily). To trigger it manually:

```bash
curl -X POST http://localhost:8000/api/admin/pipeline/trigger \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

## Operator alerts

Pipeline problems (a failed step in a run that carries on, a skipped phase of
the nightly chain, an overrun, stale data) go through
`app/ops_alerts.py:send_ops_alert`. Each alert is logged at ERROR, listed in the
admin Overview's **Ops alerts** panel (the ten newest), and pushed to
[ntfy](https://ntfy.sh) when `ALERT_NTFY_URL` is set, which is the only channel
that reaches a phone. Container logs rotate at 30 MB, about a day, so the panel
or ntfy is where an overnight alert is still found the next afternoon. Each
alert carries a dedupe key (per day, per run or per cycle), so a recurring
problem notifies once for that span.

An alert about an ongoing condition names it and stays **active** until the
code that detects the condition sees it gone and calls `resolve_ops_alert`:
the overrun and staleness watchdogs on their next clean tick, a failed step
when that step next completes, a skipped nightly phase when it next runs, the
justice alert when loyalty is next measured, and so on for every alert. The
panel lists active alerts first, then resolved ones (with when) and one-off
events. A newer alert for the same condition replaces the older (shown as
replaced, not resolved) and leaves its dedupe key held, so details that swing
back and forth (failing states A, then B, then A) don't notify on every swing.
Resolving frees the keys of every alert for the condition, so a recurrence
alerts again. Open alerts are never pushed off the panel by newer history, and
never pruned from storage. Resolved, replaced and one-off alerts stay listed
for seven days (the slowest regular jobs run weekly, so every job's latest
outcome stays in view), at most ten.

## Docker Swarm layout

The project runs on a single-node Docker Swarm (`docker swarm init` is a
one-time host setup step, not part of any deploy script). Zero-downtime
deploys come from Swarm's own rolling-update mechanism, not a hand-rolled
blue/green script:

```
                    ┌──── nginx (in-stack, port 8081) ────┐
                    │  upstream backend { }               │
                    │  upstream frontend { }              │
                    └─────────────────┬───────────────────┘
                                      │ proxy_pass (overlay network DNS)
                 ┌────────────────────┴────────────────────┐
                 ▼                                         ▼
   backend (task: read-only API)   pipeline (task)      frontend (task)
    start-first rolling update    stop-first update    start-first update
                                         │
                                         ▼
                                 llama-server (task)
                              stop-first rolling update
```

`backend` and `pipeline` run the same image in two roles
(`PROCESS_ROLE=api` / `worker`): nginx sends page reads to `backend` and
`/api/admin/` plus every pipeline-trigger endpoint to `pipeline`, which also
runs the nightly and hourly schedule. Pipelines therefore never share a
Python process, or a memory limit, with the requests visitors make.

A deploy (`check-and-deploy.sh`: the two compose files merged by
`docker compose config`, then `docker stack deploy` of the result) follows
this sequence, all built into Swarm rather than scripted:
1. Build the new Docker image locally, tagged with the deploying commit's
   short SHA (GHCR pull is disabled — see `AGENTS.md` "CI/CD" for why)
2. Swarm starts a new task on the same overlay network (`update_config.order:
   start-first`)
3. Swarm waits for the new task's `HEALTHCHECK` to pass
4. Traffic shifts to the new task via the overlay network's own service DNS
   — nginx's config never changes, `backend`/`frontend` always resolve to
   whichever task is currently healthy
5. The old task is stopped and removed
6. If the new task never becomes healthy, `failure_action: rollback`
   reverts to the previous image automatically — no manual intervention

Data is persisted in the `civitas_app_data` Docker named volume, mounted at
`/data` inside the backend and pipeline containers. The SQLite databases (`civitas.db`,
`vectors.db`) and model-version marker
live here and survive image rebuilds and stack redeploys — the volume is
`external: true` in `docker-compose.swarm.yml`, so `docker stack deploy`
never recreates or touches it directly.

### Service layout

```
Host ports    Swarm service   Purpose
───────────────────────────────────────────────────────────────────────────────
8081          nginx           Reverse proxy + caching (the only service
                              published to the host — port-forwarded
                              externally, don't change without updating
                              that forwarding rule)
—             backend         FastAPI read-only API, 2 worker processes
                              (overlay-network only)
—             pipeline        Same image: scheduler, pipelines, admin API
                              (overlay-network only)
—             frontend        Next.js frontend (overlay-network only)
—             llama-server    llama.cpp inference (overlay-network only)
```

Backend, pipeline, frontend, and llama-server publish **no host port** — Swarm's
host-mode port publishing can't restrict to `127.0.0.1` the way plain
`docker run -p 127.0.0.1:PORT:PORT` can (confirmed live: it always binds
`0.0.0.0`), so rather than accept LAN-wide exposure of ports that were
deliberately loopback-only before, none of them are published to the host
at all — nginx, inside the same overlay network, is the only path to any
of them.

llama-server is its own Swarm service (not baked into the backend image)
with its own rolling-update lifecycle, so its model weights don't reload
every time backend redeploys — only when llama-server's own image or
config changes. It updates `stop-first` rather than `start-first` like
backend/frontend (see `docker-compose.swarm.yml`): a brief restart gap is
an acceptable trade for not running two model copies in memory at once on
the Pi, since it isn't on any live user-facing request path. The backend
connects to it via `http://llama-server:8070` (overlay-network service
DNS). If llama-server is unavailable, LLM calls fail with a timeout and each
caller degrades on its own: the member pipelines never call it, the
Action Center publishes no issue from a cluster it can't read (never an
unverified one).

## Health check

`GET /api/health` returns:
```json
{
  "status": "ok" | "degraded",
  "database": "ok" | "unavailable",
  "ollama": "ok" | "unavailable",
  "lastPipelineRun": "2026-07-12T03:00:00"
}
```

Swarm considers a task healthy purely by its Docker `HEALTHCHECK` exit code
(`curl -sf http://localhost:8000/api/live` for the backend) — the same
"HTTP 200, don't parse the body" criterion the old deploy script used.
`/api/live` answers from the event loop alone, with no database read and no
llama-server call: `/api/health` waits up to 5s on llama-server and the check
times out at 5s, so a slow llama-server could get a healthy backend replaced,
killing any pipeline run in it (suspected 2026-09-29: a House run stopped
7 minutes in, its task ending "Complete" on the same image). A loop frozen by
blocking work still fails `/api/live`.
`database`/`ollama` in the response body are informational for the admin
dashboard and don't gate the rolling update. The `ollama` key name is
historical (kept for API/dashboard compatibility even though Ollama isn't
bundled anymore): it reports whichever backend `LLM_BACKEND` selects, so
under the default it is the llama.cpp server's health, not Ollama's.

## Continuous integration

CI runs the fast suite behind a 46% total-coverage floor plus a 60%
changed-lines gate (`diff-cover`), and runs the `slow` suite on pushes to
`main`. See `.github/workflows/ci.yml`.

## Environment variables

See `.env.example` for all options. Key variables:

| Variable | Required | Description |
|---|---|---|
| `DATA_GOV_API_KEY` | Yes | API key from api.data.gov (covers Congress.gov, FEC, GovInfo) |
| `BLS_API_KEY` | No | Free BLS v2 registration key (data.bls.gov/registrationEngine): 500 jobs-API requests a day instead of 25 |
| `ADMIN_TOKEN` | Yes | Bearer token for the admin panel and admin API (falls back to `PIPELINE_TRIGGER_TOKEN`) |
| `PIPELINE_TRIGGER_TOKEN` | Yes | Bearer token for the public `/api/pipeline/trigger` routes. For both tokens, `.env.example`'s `change-me-to-a-random-token` counts as unset and is refused (`app/api/auth.py`) |
| `SITE_URL` | No | The site's public address (default `https://civitas-research.org`): links, feeds, cards, canonicals, `robots.txt`. Typed only in `backend/app/site_url.py` and `frontend/src/lib/site.ts` (`tests/test_site_url.py`); the frontend gets it at build as `NEXT_PUBLIC_SITE_URL` |
| `REAL_IP_FROM` / `REAL_IP_HEADER` | No | The proxy in front of nginx allowed to name the visitor (`nginx/real-ip.sh`; default loopback only, header `X-Real-IP`). Required behind a reverse proxy: production sets the Pi's LAN address |
| `CONTACT_EMAIL` | Yes | Your instance's contact address, named in the User-Agent of every request the pipeline, scripts and the frontend's photo route send: `Civitas/1.0 (+<SITE_URL>/about/crawler; <CONTACT_EMAIL>)` (`backend/app/contact.py`; `/about/crawler` describes the crawler). Unset or malformed, requests name Civitas with no address, the SEC (whose fair-access policy requires a contact) is not fetched and raises an ops alert, and the pipeline logs a warning at start |
| `LLM_BACKEND` | No | `llama-server` (default) or `ollama` |
| `LLAMA_SERVER_URL` | No | llama-server URL (default: `http://llama-server:8070`, the in-stack service) |
| `LLAMA_MODELS_DIR` | No | Host directory bind-mounted into llama-server at `/models` (default: `./llama-models`) |
| `LLAMA_MODEL_FILE` | No | `.gguf` filename inside `LLAMA_MODELS_DIR` (default: `lfm2.5-1.2b-instruct-q4_k_m.gguf`) |
| `OLLAMA_MODEL` | No | Model name for cache keys and Ollama (default: `LiquidAI/lfm2.5-1.2b-instruct`) |
| `DATABASE_URL` | No | SQLite path. `docker-compose.yml` sets `sqlite:////data/civitas.db` (the `/data` volume); the code default `sqlite:///data/civitas.db` is relative to the working directory, for running outside Docker |
| `PIPELINE_CRON_SCHEDULE` | No | Cron schedule for nightly pipeline (default: `0 3 * * *`) |
| `PIPELINE_CACHE_TTL_HOURS` | No | API response cache TTL (default: `72`) |
| `GOOGLE_CIVIC_API_KEY` | No | Google Civic Information API key — enables the optional town-level local-races selector on the state ballot pages (a small hand-curated pilot list, not all US towns). Unset means the selector doesn't appear |
| `BSKY_HANDLE` | No | Bluesky handle (e.g. `civitas-research.org`) |
| `BSKY_APP_PASSWORD` | No | Bluesky app password (from Settings → App Passwords) |
| `FEEDBACK_TOKEN` | No | Fine-grained GitHub PAT (Issues: write only) for the on-site feedback form; leave unset to disable it (returns 503, never silently drops) |
| `GITHUB_FEEDBACK_REPO` | No | Repo the feedback form files issues against (default: `kamoras/civitas`) |
