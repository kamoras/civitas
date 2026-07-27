# Nightly pipeline

Runs at 03:00 UTC by default (`PIPELINE_CRON_SCHEDULE`). Processes all 100
senators and 435 representatives, then a stock-disclosure pass.

```mermaid
flowchart TB
    START(["APScheduler cron tick"]) --> LOCK{"PipelineRun<br/>status = running?"}
    LOCK -->|yes, fresh| SKIP(["Skip this tick"])
    LOCK -->|yes, stale > 8h| MARK["Mark stale, proceed"]
    LOCK -->|no| FP
    MARK --> FP

    FP{"SHA-256 of analyze/*.py<br/>== last run's hash?"}
    FP -->|changed| CLEAR["Clear AnalysisCache<br/>+ LearnedClassification<br/>ApiCache untouched"]
    FP -->|same| P1
    CLEAR --> P1

    P1["<b>1. FETCH</b><br/>Congress · FEC · GovInfo · Senate.gov<br/>Voteview ideal points<br/>raw responses stored verbatim in ApiCache"]
    P2["<b>2. TRANSFORM</b><br/>FEC dedup by committee ID + amendment<br/>bill title normalisation<br/>employer name canonicalisation<br/>memo-text earmark separation"]
    P1 --> P2 --> P3

    subgraph P3["3. ANALYZE — producer/consumer, per member"]
        direction LR
        LIB["<b>Librarian thread</b><br/>runs one member ahead<br/>batches of 64<br/><br/>bill titles → policy areas<br/>employers → industries<br/>donor↔bill cosine conflicts<br/>key-vote selection<br/>platform topic extraction<br/>speech → party alignment"]
        ANA["<b>Analyst thread</b><br/>one LLM call at a time<br/>blocks 15-30s per call<br/><br/>PAC identification<br/>promise evaluation<br/>narrative synthesis"]
        LIB -->|"threading.Event + shared dict<br/>(lookahead is exactly 1, so no queue)"| ANA
    end

    P3 --> P4["<b>4. EXPLORE</b><br/>embed speeches, presidential actions,<br/>SCOTUS opinions, FR rulemaking<br/>→ sqlite-vec upsert"]
    P3 --> P5["<b>5. JUSTICES</b><br/>Oyez votes → consistency,<br/>independence, restraint"]
    P3 --> P6["<b>6. PRESIDENTS</b><br/>BLS · BEA/FRED · MeasuringWorth<br/>UCSB approval · C-SPAN survey"]

    P4 --> P7
    P5 --> P7
    P6 --> P7

    P7["<b>7. FINALIZE</b><br/>persist scores, key votes, lobbying matches,<br/>promises, sponsored bills<br/>append ScoreSnapshot per member<br/>record PipelineRun timings + errors"]

    P7 --> HOUSE["<b>House pipeline</b><br/>~6 phases, 435 members<br/>reuses the EXPLORE pipeline<br/>no LLM for promise analysis"]
    HOUSE --> STOCK["<b>Stock pipeline</b><br/>STOCK Act PTR ingestion<br/>House Clerk + Senate eFD"]
    STOCK --> DONE(["PipelineRun status = completed"])
```

## Why the Librarian runs one member ahead

The Analyst blocks on LLM HTTP for 15–30s per member. In that window the
Librarian computes the *next* member's embedding work (2–4s). Across 100
senators that recovers 200–400s of wall clock — a 10–15% reduction — at zero
extra peak memory, because the lookahead is exactly one member rather than a
full queue.

Peak memory during this phase, with embedding and LLM work overlapped, reaches
about 3 GB.

## The SQLite mutex

Concurrency control is a row in `pipeline_runs` (`status == "running"`), not a
process-level lock. That choice is what makes rolling deploys safe: a new
container starting mid-run finds the in-progress row and marks it `stale`
rather than blocking or double-running. See `backend/app/scheduler.py`.

## The fingerprint gate

At start, a SHA-256 over every file in `pipeline/analyze/` is compared to the
hash stored on the last `PipelineRun`. If the analysis code changed, derived
artifacts are cleared so updated logic can't serve results computed by the old
logic.

`ApiCache` is deliberately exempt — it holds raw source data, which doesn't
become wrong because analysis code changed. See [06 — Caching](06-caching.md).

## Source map

| Stage | Code |
|---|---|
| Orchestration | `backend/app/pipeline/senate_pipeline.py`, `house_pipeline.py` |
| Fetch clients | `backend/app/pipeline/fetch/` |
| Transform | `backend/app/pipeline/transform/` |
| Analyze | `backend/app/pipeline/analyze/` |
| Assemble + validate | `backend/app/pipeline/assemble/` |
| Run bookkeeping | `backend/app/pipeline/run_tracker.py`, `progress_tracker.py` |
