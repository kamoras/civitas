# Deployment

Single-node Docker Swarm. Zero-downtime comes from Swarm's own rolling-update
mechanism, not a hand-rolled blue/green script — that script was retired in
2026-07.

## Topology

```mermaid
flowchart TB
    NET(["Public internet"]) -->|"port-forwarded"| NGINX

    subgraph HOST["Raspberry Pi 5 host"]
        NGINX["<b>civitas_nginx</b> :8081<br/>the only host-published service<br/>upstream backend / upstream frontend"]

        subgraph OVERLAY["Swarm overlay network"]
            BE["<b>civitas_backend</b><br/>FastAPI :8000<br/>no host port"]
            FE["<b>civitas_frontend</b><br/>Next.js :3000<br/>no host port"]
            OL["<b>civitas_ollama</b><br/>:11434, fallback LLM<br/>no host port"]
        end

        LLAMA["<b>llama-server</b> :8070<br/>systemd, NOT in the stack<br/>weights stay resident across redeploys"]
        VOL[("civitas_app_data<br/>external volume → /data<br/>civitas.db + vectors.db")]
    end

    NGINX -->|"proxy_pass via service DNS"| BE
    NGINX -->|"proxy_pass via service DNS"| FE
    FE -->|"JSON"| BE
    BE -->|"host.docker.internal:8070"| LLAMA
    BE -.->|"only when LLM_BACKEND=ollama"| OL
    BE --> VOL
```

**Backend and frontend publish no host port.** Swarm's host-mode publishing
cannot bind to `127.0.0.1` the way `docker run -p 127.0.0.1:PORT:PORT` can — it
always binds `0.0.0.0` (confirmed live). Rather than accept LAN-wide exposure of
ports that were loopback-only before, they simply aren't published. nginx,
inside the same overlay network, is the only path to either.

**The volume is `external: true`**, so `docker stack deploy` never recreates or
touches it. Both SQLite files (`civitas.db`, `vectors.db`) survive image rebuilds and
stack redeploys.

**llama.cpp is not containerised** and not in the stack, so model weights stay
in RAM across backend redeploys. If it's unreachable, LLM calls time out and the
pipeline records a per-member failure without aborting the run.

## Rolling update

```mermaid
sequenceDiagram
    autonumber
    participant Cron as check-and-deploy.sh
    participant Swarm
    participant Old as backend task (old)
    participant New as backend task (new)
    participant Nginx as civitas_nginx

    Cron->>Cron: poll for new commits
    Cron->>Cron: build image, tag with short SHA
    Note over Cron: built locally — GHCR pull is disabled
    Cron->>Swarm: docker stack deploy -c compose -c swarm

    Swarm->>New: start new task (update_config.order: start-first)
    Note over Old,New: both running — no gap in service
    New->>New: HEALTHCHECK: curl -sf localhost:8000/api/health

    alt healthcheck passes
        Swarm->>Nginx: service DNS now resolves to the new task
        Note over Nginx: nginx config never changes
        Swarm->>Old: stop and remove
        Cron->>Swarm: poll UpdateStatus.State until "completed"
    else healthcheck never passes
        Swarm->>Swarm: failure_action: rollback
        Swarm->>Old: keep serving, revert to previous image
        Note over Cron: rollout failed — Swarm auto-rolled back
    end
```

Health is judged purely on the Docker `HEALTHCHECK` exit code — the same
"HTTP 200, don't parse the body" criterion the old deploy script used. The
`database` and `ollama` fields in the response body are informational for the
admin dashboard and do not gate the rollout.

`ollama` is a historical key name: it reports whichever backend `LLM_BACKEND`
selects, so under the default it is llama-server's health.

Deploying restarts the backend, which kills any in-flight pipeline run — hence
the stale-run detection in [02](02-nightly-pipeline.md).

## Service reference

| Host port | Swarm service | Purpose |
|---|---|---|
| 8081 | `civitas_nginx` | Reverse proxy + caching. The only published port — don't change without updating the external forwarding rule. |
| — | `civitas_backend` | FastAPI, overlay-network only |
| — | `civitas_frontend` | Next.js, overlay-network only |
| — | `civitas_ollama` | Fallback LLM backend, overlay-network only |
| 8070 | *(none — systemd)* | llama.cpp inference, outside the stack |

`docker swarm init` is a one-time host setup step, not part of any deploy
script.

## Source map

| Concern | File |
|---|---|
| Deploy poller | `check-and-deploy.sh` (+ `check-and-deploy.test.sh`) |
| Base stack | `docker-compose.yml` |
| Swarm overlay | `docker-compose.swarm.yml` |
| Local dev overrides | `docker-compose.dev.yml` |
| Reverse proxy | `nginx/civitas.conf` |
| Health endpoint | `backend/app/api/health.py` |
