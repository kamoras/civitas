# System architecture

Everything runs on one Raspberry Pi 5 (16 GB, NVMe). No cloud GPU, no
third-party AI API, no data leaving the device.

```mermaid
flowchart TB
    subgraph SRC["External sources — read-only"]
        direction LR
        CONG["Congress.gov<br/>bills · votes · members<br/>1.2 RPS"]
        FEC["FEC API<br/>contributions · committees<br/>0.25 RPS"]
        GOVINFO["GovInfo<br/>bill text · Congressional Record<br/>1.0 RPS"]
        SENGOV["Senate.gov<br/>platform text · roll calls<br/>scraped, no API"]
        OYEZ["Oyez / supremecourt.gov<br/>SCOTUS votes · opinions"]
        ECON["BLS · BEA / FRED · MeasuringWorth<br/>employment · GDP"]
        FEDREG["Federal Register<br/>orders · rulemaking"]
        UCSB["UCSB American Presidency Project<br/>roster · approval · margins"]
        PTR["House Clerk · Senate eFD · OGE · SEC<br/>STOCK Act disclosures"]
        VOTEVIEW["Voteview<br/>DW-NOMINATE ideal points"]
        RSS["RSS — AP · NPR · PBS · BBC<br/>The Hill · Politico · Roll Call<br/>8 feeds, 7 newsrooms"]
        SOCIAL["Google Trends · Reddit"]
        VSMART["Vote Smart<br/>statewide ballot measures<br/>optional, keyed"]
        GCIVIC["Google Civic Info<br/>town-level local races<br/>optional, keyed, fixed address only"]
    end

    subgraph PIPE["Pipelines — APScheduler"]
        NIGHTLY["Nightly, 03:00 UTC<br/>senate → house → stock<br/>4-6h cold · 45-90m warm"]
        HOURLY["Hourly at :15<br/>Action Center refresh"]
        SUPP["Supplementary<br/>presidents · justices · explore"]
        ELECT["Election pipeline<br/>races · candidates · ballot measures · coverage"]
    end

    subgraph STORE["Persistence — /data volume"]
        SQLITE[("SQLite civitas.db<br/>44 tables")]
        VECDB[("sqlite-vec vectors.db<br/>vec_explore + vec_bills<br/>384-dim, cosine")]
    end

    subgraph SERVE["Serving"]
        API["FastAPI :8000<br/>/api/... · /api/public/v1 · /health"]
        WEB["Next.js 16 :3000<br/>App Router, RSC"]
        NGINX["nginx :8081<br/>reverse proxy + cache"]
    end

    LLAMA["llama.cpp :8070<br/>LFM2.5-1.2B-Instruct<br/>Docker, overlay-network only"]
    EMBED["Two sentence-transformers, in-process<br/>Arctic-XS — classification<br/>all-MiniLM-L6-v2 — index + similarity gates<br/>both 384-dim, ~22M params"]
    BSKY["Bluesky<br/>@civitas-research.org"]

    SRC -->|rate-limited HTTP| PIPE
    RSS --> HOURLY
    SOCIAL --> HOURLY
    VOTEVIEW --> NIGHTLY
    VSMART --> ELECT

    PIPE -->|writes| SQLITE
    PIPE -->|upserts embeddings| VECDB
    PIPE -.->|~100-400 calls/run| LLAMA
    PIPE -.->|~50,000 ops/run| EMBED
    HOURLY -->|posts| BSKY

    SQLITE --> API
    VECDB --> API
    API -.->|on-demand summaries| LLAMA
    API -->|on-demand, cached 12h| GCIVIC
    API -->|JSON| WEB
    WEB --> NGINX
    API --> NGINX
    NGINX -->|only host-published port| USERS(["Public internet"])
```

## Reading the diagram

**Solid edges are data flow; dotted edges are inference calls.** The ratio is
the point: roughly 50,000 embedding operations per run against 100–400 LLM
calls. Civitas is a semantic classification and retrieval system that uses a
language model only at the final synthesis step, not an LLM application.

**The two models live in different places.** The embedding model runs
in-process inside the backend container (~90 MB resident). The LLM runs as
its own Swarm service (`ghcr.io/ggml-org/llama.cpp:server`, overlay-network
only) with an independent rolling-update lifecycle, so model weights don't
reload every time backend redeploys — only when llama-server's own image or
config changes. The backend reaches it at `llama-server:8070` (service DNS).

**Only nginx is published to the host.** Backend and frontend bind to the
overlay network only — Swarm's host-mode publishing cannot restrict to
`127.0.0.1`, so rather than accept LAN-wide exposure they aren't published at
all. See [08 — Deployment](08-deployment.md).

**If llama.cpp is unavailable**, LLM calls fall through to a timeout and the
pipeline records a per-member failure without aborting the run. Scores still
compute — they are deterministic and take no LLM input.

## Source map

| Component | Code |
|---|---|
| Pipeline orchestration | `backend/app/pipeline/{senate,house,president,justice,explore,election,stock}_pipeline.py` |
| Scheduler | `backend/app/scheduler.py` |
| LLM client | `backend/app/pipeline/analyze/ollama_client.py` |
| Embeddings + sqlite-vec | `backend/app/pipeline/vector_store.py` |
| API routes | `backend/app/api/` |
| Frontend | `frontend/src/app/` |
