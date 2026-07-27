# Explore — semantic search

Free-text search over primary-source government documents, with no keyword
matching. Documents are embedded offline during pipeline runs; queries are
embedded at request time.

```mermaid
flowchart TB
    subgraph INDEX["Indexing — during pipeline runs"]
        direction TB
        S1["Senate floor speeches<br/>GovInfo CREC packages"]
        S2["House floor speeches<br/>GovInfo CREC packages"]
        S3["Presidential actions<br/>executive orders, proclamations, memoranda"]
        S4["Supreme Court opinions<br/>Oyez + supremecourt.gov"]
        S5["Federal Register rulemaking<br/>proposed + final rules"]

        S1 --> DOC
        S2 --> DOC
        S3 --> DOC
        S4 --> DOC
        S5 --> DOC

        DOC["ExploreDocument row<br/>doc_type · source · title · summary · body<br/>date · politician_name/id · chamber<br/>agency_name · comment_url · comments_close_on"]
        DOC --> EMB["Embed title + summary + body[:800]<br/><b>one embedding per document — no chunking</b>"]
        EMB --> UPSERT[("sqlite-vec upsert into vec_explore<br/>384-dim, all-MiniLM-L6-v2")]
    end

    subgraph QUERY["Query — at request time"]
        direction TB
        Q(["User query"]) --> QEMB["Embed with all-MiniLM-L6-v2<br/>(same model that built the index)"]
        QEMB --> ANN["sqlite-vec KNN<br/>embedding MATCH ? AND k = ?<br/>cosine distance"]
        ANN --> FILT["Filter: doc type · chamber ·<br/>politician · open-for-comment"]
        FILT --> SORT["Rank by relevance (default) or date"]
        SORT --> OUT["Return excerpt, source URL, doc type<br/>+ comment link and deadline for open rulemakings"]
        OUT -.->|"optional, streamed"| SUM["LLM summary of how this<br/>document relates to the query"]
    end

    UPSERT --> ANN
```

## Design notes

**No chunking, deliberately.** One embedding per document over
`title + summary + body[:800]`. Chunking would improve recall on long documents
but multiplies index size and query cost — a real constraint when the whole
index lives on a Pi alongside everything else. The truncation is a disclosed
trade: a document whose relevant passage sits past 800 characters of body may be
missed.

**Bill text is not in this index.** Explore covers primary-source *government
activity* documents. Bill text is used separately, title-only, for the tier-3
kNN bill-classification step in the scoring pipeline
([04 — Classification tiers](04-classification-tiers.md)). Two different
corpora for two different jobs.

**The index is not built with the classification model.** Indexing and querying
both use `all-MiniLM-L6-v2`, while bill/donor classification stays on
`Snowflake/snowflake-arctic-embed-xs`. Arctic is retrieval-*asymmetric* and
packs same-register text into a narrow ~0.55-0.87 raw-cosine band, which left
several similarity gates unable to separate real matches from noise; MiniLM
measured roughly 4x the separation margin on document anchoring against this
platform's own live failure cases. Classification stays on Arctic because its
thresholds were calibrated against that model's geometry. Both are ~22M
parameters and 384-dim, so carrying two costs little.

**The index knows which model built it.** `vectors.db` stores the index model
id in a `meta` table; a mismatch at startup drops the vec tables and kicks off a
background reindex from the `ExploreDocument` rows in the app database. Search
returns "index not ready" until it completes, which callers already handle.

**Open rulemakings are a first-class filter.** Federal Register documents still
open for public comment carry `comment_url` and `comments_close_on`, and the
result surfaces both — the one place on the platform where search leads
directly to an action with a deadline.

**Summarisation is on-demand and streamed.** It uses `stream_llm` rather than
`call_llm`, so text renders progressively; the call site handles its own caching
and retry, because "retry" means something different once a partial response is
already on screen.

## Why dense retrieval over BM25

Keyword search fails on conceptual queries where exact term overlap is low —
"climate policy" against a document that says "greenhouse gas emissions
standards" scores near zero on BM25 and high on cosine similarity in embedding
space (Karpukhin et al. 2020, dense passage retrieval).

## Source map

| Concern | Code |
|---|---|
| Indexing pipeline | `backend/app/pipeline/explore_pipeline.py` |
| Vector store | `backend/app/pipeline/vector_store.py` |
| Query API | `backend/app/api/explore.py` |
| Public search endpoint | `GET /api/public/v1/search` |
| Document model | `backend/app/models.py::ExploreDocument` |
| Frontend | `frontend/src/app/explore/` |
