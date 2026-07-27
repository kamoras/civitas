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
        PTR["House Clerk · Senate eFD · SEC<br/>STOCK Act disclosures"]
        VOTEVIEW["Voteview<br/>DW-NOMINATE ideal points"]
        RSS["RSS — AP · NPR · PBS · BBC<br/>The Hill · Politico · Roll Call<br/>8 feeds, 7 newsrooms"]
        SOCIAL["Google Trends · Reddit"]
    end

    subgraph PIPE["Pipelines — APScheduler"]
        NIGHTLY["Nightly, 03:00 UTC<br/>senate → house → stock<br/>4-6h cold · 45-90m warm"]
        HOURLY["Hourly at :15<br/>Action Center refresh"]
        SUPP["Supplementary<br/>presidents · justices · explore"]
        ELECT["Election pipeline<br/>races · candidates · coverage"]
    end

    subgraph STORE["Persistence — /data volume"]
        SQLITE[("SQLite civitas.db<br/>42 tables")]
        CHROMA[("ChromaDB<br/>384-dim HNSW")]
    end

    subgraph SERVE["Serving"]
        API["FastAPI :8000<br/>/api/... · /api/public/v1 · /health"]
        WEB["Next.js 16 :3000<br/>App Router, RSC"]
        NGINX["nginx :8081<br/>reverse proxy + cache"]
    end

    LLAMA["llama.cpp :8070<br/>LFM2.5-1.2B-Instruct<br/>systemd, ARM-native"]
    EMBED["Snowflake Arctic-XS<br/>22M params, in-process"]
    BSKY["Bluesky<br/>@civitas-research.org"]

    SRC -->|rate-limited HTTP| PIPE
    RSS --> HOURLY
    SOCIAL --> HOURLY
    VOTEVIEW --> NIGHTLY

    PIPE -->|writes| SQLITE
    PIPE -->|upserts embeddings| CHROMA
    PIPE -.->|~100-400 calls/run| LLAMA
    PIPE -.->|~50,000 ops/run| EMBED
    HOURLY -->|posts| BSKY

    SQLITE --> API
    CHROMA --> API
    API -.->|on-demand summaries| LLAMA
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
in-process inside the backend container (~90 MB resident). The LLM runs as a
*host* systemd service outside Docker, so model weights stay in RAM across
backend redeploys. The backend reaches it at `host.docker.internal:8070`.

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
| Embeddings + ChromaDB | `backend/app/pipeline/vector_store.py` |
| API routes | `backend/app/api/` |
| Frontend | `frontend/src/app/` |
