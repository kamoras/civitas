# Civitas — Political Representation Tracker

Civitas is an open-data AI/ML platform that aggregates data from official
U.S. government sources into unified transparency scorecards for senators,
House representatives, presidents, and Supreme Court justices. It also
features an Action Center that surfaces trending civic issues from news
analysis, auto-detects ongoing national concerns as trackable monitors,
and builds a year-in-review timeline. Voting records, campaign finance,
floor speeches, judicial opinions, and stated platforms are analyzed using
embedding-based classification, content-based party alignment, and
deterministic scoring — all running locally on a Raspberry Pi 5 with zero
external API calls to cloud AI services.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          DATA SOURCES (read-only)                            │
│                                                                              │
│  Congress.gov   FEC API    GovInfo API  Senate.gov  Oyez    BLS / BEA        │
│  (bills, votes, (campaign  (bill text,  (speeches,  (SCOTUS (jobs,           │
│   members)       finance)   histories)   remarks)    cases)  GDP)            │
│                                                                              │
│  AP / NPR / BBC / PBS (RSS)          Google Trends     Reddit r/politics     │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │  rate-limited HTTP
                           │  (Congress 1.2 RPS, FEC 0.25 RPS, GovInfo 1.0 RPS)
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                   NIGHTLY PIPELINE  (APScheduler, default 3 AM UTC)          │
│                                                                              │
│  ┌─────────┐   ┌───────────┐   ┌───────────────────────────────────────┐    │
│  │ 1.FETCH │──▶│2.TRANSFORM│──▶│            3. ANALYZE                 │    │
│  └─────────┘   └───────────┘   │                                       │    │
│                                │  ┌─────────────────┬────────────────┐ │    │
│                                │  │ Librarian thread│ Analyst thread │ │    │
│                                │  │ (batch embed)   │ (LLM + score)  │ │    │
│                                │  │                 │                │ │    │
│                                │  │ bill embeddings │ ◄── blocks on  │ │    │
│                                │  │ donor kNN       │   LLM response │ │    │
│                                │  │ lobbying match  │                │ │    │
│                                │  │ promise align   │ narrative synth│ │    │
│                                │  └─────────────────┴────────────────┘ │    │
│                                └───────────────────────────────────────┘    │
│                                                │                             │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   │   ┌──────────────────────┐ │
│  │4. EXPLORE│   │5.JUSTICES│   │6.PRESIDENTS   │   │    7. FINALIZE       │ │
│  │ (ChromaDB│   │ (Oyez)   │   │(BLS/BEA/ ◄────┘   │ (persist scores,    │ │
│  │  index)  │   │          │   │ Gallup)  │         │  PipelineRun record)│ │
│  └──────────┘   └──────────┘   └──────────┘         └──────────────────────┘ │
│                                                                              │
│  ──────────────────── HOUSE PIPELINE (runs after Senate) ──────────────────  │
│  Own ~6-phase pipeline for all 435 representatives (FETCH MEMBERS, NORMALIZE,│
│  FETCH BILLS & VOTES, CLASSIFY BILLS, SPONSORSHIP ANALYSIS, FEC + SCORING) —  │
│  reuses the unified EXPLORE pipeline for House floor speeches                │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │ writes
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                          PERSISTENCE LAYER                                   │
│                                                                              │
│  SQLite  (civitas.db)                    ChromaDB  (HNSW vector index)       │
│  ├── senators / representatives          └── ExploreDocument embeddings      │
│  ├── KeyVote / SponsoredBill                 (~384-dim, Snowflake Arctic-XS) │
│  ├── Donor / IndustryDonation / LobbyingMatch                                │
│  ├── CampaignPromise                                                         │
│  ├── ActionIssue / NationalMonitor / MonitorUpdate                           │
│  ├── TimelineEntry / WeekSummary / MonthSummary / YearSummary                │
│  ├── ScoreSnapshot  (historical score tracking per senator/rep)               │
│  ├── ApiCache  (raw API responses, TTL=72h, never cleared)                   │
│  ├── AnalysisCache  (LLM outputs, hash-keyed, cleared on code changes)       │
│  └── LearnedClassification  (cross-run entity learning store)                │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │ reads
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  FastAPI backend  (port 8000)                                                │
│  /api/senators      /api/representatives     /api/presidents                 │
│  /api/justices      /api/action              /api/explore                    │
│  /api/admin         /api/public/v1  (open, rate-limited)    /health          │
│                                              ┌────────────────────────┐      │
│  APScheduler ──▶ nightly pipeline            │  llama.cpp server      │      │
│  APScheduler ──▶ hourly action refresh  ────▶│  Qwen2.5 1.5B          │      │
│                                              │  port 8070 (ARM-native)│      │
└──────────────────────────┬───────────────────└────────────────────────┘──────┘
                           │ JSON
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  Next.js 16 frontend  (port 3000)                                            │
│  /politicians  /scorecard  /leaderboard  /action  /explore  /compare        │
│  /issue  /about  /admin  /environmental  /accessibility                      │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Hardware:** Raspberry Pi 5 (16 GB RAM), NVMe SSD. All models, databases, and services run on-device. No cloud GPU, no third-party AI APIs, no data leaves the device.

---

## Nightly Pipeline: Phase by Phase

The nightly pipeline processes every senator and House representative through six sequential phases before persisting scores and snapshots.

### Phase 1 — FETCH

Pulls raw data from each government API and stores the complete response verbatim in `ApiCache` before any transformation:

| Source | What is fetched | Rate limit |
|--------|-----------------|------------|
| Congress.gov | Bills sponsored/cosponsored (last 2 years), roll-call votes | 1.2 RPS |
| FEC API | Campaign finance transactions, committee receipts, outside spending | 0.25 RPS |
| GovInfo API | Full bill text for key votes (PDF → text extraction) | 1.0 RPS |
| Senate.gov | Floor speeches, press remarks (scraped; no public API) | polite crawl |
| Oyez / SCOTUS | Justice voting records, case metadata | 0.5 RPS |
| BLS | Unemployment, inflation, job growth by administration | batch |
| BEA | GDP growth by quarter | batch |
| Federal Register | Executive orders signed per administration | 1.0 RPS |

Nothing from the API cache is ever cleared — it represents immutable source data. A fresh run can replay against the cached responses without re-hitting the external APIs (controlled by `PIPELINE_CACHE_TTL_HOURS`).

### Phase 2 — TRANSFORM

Normalizes raw API payloads into typed domain objects. Key operations:

- **FEC deduplication**: FEC records contain duplicate committee entries from amended filings. The transform layer resolves these by matching on committee ID and keeping the most recent amendment.
- **Bill title normalization**: Congress.gov returns bill titles in several formats (official, short, display, popular). Transform picks the most human-readable, falling back through the hierarchy.
- **Employer name normalization**: FEC contributor employer fields are free text. A batch embedding pass maps variant spellings (e.g. "Goldman Sachs & Co", "GOLDMAN SACHS") to a canonical form before classification.
- **Memo text parsing**: FEC memo text fields encode earmarks and transfers in free-form text. A batch skip-entity classifier separates genuine contributions from administrative memo entries.

### Phase 3 — ANALYZE

The heaviest phase. Runs the producer-consumer pipeline (Librarian + Analyst threads) for each member:

**Librarian thread (batch embedding, runs one member ahead):**
1. Embed all sponsored/cosponsored bill titles → classify policy areas (nearest centroid, 14 prototypes)
2. Embed all donor employer names → classify industries (tiered: exact match → embedding → kNN)
3. Compute lobbying conflicts: cosine similarity between donor industries and bill policy areas
4. Select key votes: composite score = party deviation + donor industry overlap
5. Embed platform text → extract policy topics (sentence-transformer, not LLM)
6. Embed floor speeches → compute party alignment (nearest centroid vs. party platform corpora)

**Analyst thread (LLM, one call at a time):**
1. Classify PAC donors that evaded the embedding classifier (LLM PAC identification)
2. Evaluate campaign promises: compare platform commitments vs. voting record (LLM, compressed context)
3. Generate narrative summary: synthesize key votes, donor conflicts, promise status (LLM, ~500 tokens out)

The Librarian runs one member ahead of the Analyst. While the Analyst blocks on LLM I/O (~15–30s), the Librarian completes the embedding batch for the next member. Results are passed via a `threading.Event` + shared dict — no queue needed since lookahead is exactly 1.

### Phase 4 — EXPLORE

Builds the semantic search index over government activity documents — floor
speeches (Senate and House), presidential actions (executive orders,
proclamations, memoranda), Supreme Court opinions, and Federal Register
rulemaking documents (including ones still open for public comment):
- One embedding per document — no chunking — over `title + summary + body[:800 chars]`
- Encodes with the sentence-transformer (384-dim, Snowflake Arctic-XS)
- Upserts into ChromaDB with metadata: doc type, source, date, politician name/ID, chamber
- At query time: embed query → HNSW approximate nearest-neighbor search → return top-k documents, filterable by doc type, chamber, and open-for-comment status

This is a separate index from the tier-3 bill-classification embeddings used
in Phase 3 — it's for browsing/searching primary-source government documents,
not for scoring.

### Phase 5 — JUSTICES

Fetches and scores Supreme Court justices from Oyez:
- Pulls all majority/dissent/concurrence votes for the current term
- Scores ideological consistency: deviation from the justice's historical median position
- Scores impartiality: proportion of cases where the justice's coalition crossed party-appointment lines

### Phase 6 — PRESIDENTS

Scores sitting and historical presidents from a mix of live and archival sources:
- **Live**: BLS employment rate, BEA GDP growth, Federal Register order count
- **Historical**: C-SPAN Presidential Historians Survey (competence/leadership), Gallup approval archive, FiveThirtyEight election margin data
- All six score dimensions (Independence, Follow-Through, Public Mandate, Effectiveness, Competence, Agency Alignment) are computed from source data with no LLM involvement.

### Phase 7 — FINALIZE

Persists everything computed in phases 3–6:
- Writes senator/representative scores, key votes, lobbying matches, campaign promises, sponsored bills to SQLite
- Appends a `ScoreSnapshot` record (all 5 sub-scores + overall) for each member — enables historical score trend charts
- Records a `PipelineRun` with phase timings, counts, and any per-member errors
- Runs a SHA-256 fingerprint over all analysis source files; if changed since last run, clears `AnalysisCache` and `LearnedClassification` so stale results from the old code are not served

---

## Caching Architecture

Three independent caching systems serve different purposes:

```
┌────────────────────────────────────────────────────────────────────┐
│  ApiCache  (SQLite: api_cache)                                     │
│  Key: (tier, endpoint+params_hash)   TTL: 72h (configurable)       │
│  Stores: raw API JSON, verbatim                                     │
│  Cleared: never (source data is immutable; re-fetched after TTL)   │
│  Purpose: replay pipeline without re-hitting external APIs          │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│  AnalysisCache  (SQLite: analysis_cache)                           │
│  Key: (prompt_version, SHA-256(input))                             │
│  Stores: LLM JSON output, verbatim                                 │
│  Cleared: when source file fingerprint changes (code update)       │
│  Purpose: skip LLM calls for unchanged inputs on warm reruns       │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│  LearnedClassification  (SQLite: learned_classifications)          │
│  Key: (entity_text, entity_type)                                   │
│  Stores: classification + confidence + source                      │
│  Cleared: when source file fingerprint changes                     │
│  Purpose: cross-run entity memory (kNN bootstrapping, audit trail) │
└────────────────────────────────────────────────────────────────────┘
```

The fingerprint check at pipeline start compares `SHA-256(all analysis/*.py files)` to the hash stored in the last `PipelineRun`. If they differ, `AnalysisCache` and `LearnedClassification` are cleared so updated logic produces fresh results. `ApiCache` is never cleared by fingerprint — source data doesn't change when analysis code does.

---

## Data Pipeline: Design Rationale

The pipeline is structured around a specific set of constraints that shape every decision.

### Why a Nightly Batch Pipeline?

A 100-senator + 435-representative full refresh requires 4–6 hours cold (warm: 45–90 minutes). Online/streaming processing is not viable at these volumes on the target hardware: the sentence-transformer model occupies ~90 MB, the LLM occupies ~900 MB, and peak memory during the analyze phase (overlapped embedding + LLM) reaches ~3 GB. Batching allows us to control memory precisely, while a separate hourly pipeline handles the Action Center's lower-latency requirements.

The pipeline uses a SQLite-level mutex (`PipelineRun.status == "running"`) rather than a process-level lock, making it safe to deploy in multi-container blue/green environments: any new container discovering an in-progress run on startup marks it `stale` rather than blocking.

### Why an Adversarial Data Architecture?

Government data sources are not designed for machine consumption. Congress.gov returns inconsistent bill title formats; FEC records contain duplicate committee entries; senate.gov lacks a public API. The **FETCH → TRANSFORM** separation isolates source-specific parsing from domain logic. Every raw API response is stored verbatim in `ApiCache` (never cleared, TTL=72h), so the pipeline can be replayed deterministically against historical snapshots — essential for debugging and auditing.

Rate limits are enforced at the source level (Congress.gov: 1.2 RPS, FEC: 0.25 RPS, GovInfo: 1.0 RPS) with per-API backoff, not global throttling. This prevents a slow FEC response from stalling the Congress.gov queue.

### Why a Producer-Consumer Analyze Phase?

The analyze phase uses a **producer-consumer pattern** to overlap embedding work with LLM inference:

- **Librarian thread** (producer): Runs ahead of the current senator, pre-computing all embedding-based analyses (lobbying matches, key vote selection, promise alignment, platform topic extraction) for the *next* senator in batches of 64.
- **Analyst thread** (consumer): Blocks on LLM HTTP responses (~15–30s each), then immediately consumes the pre-computed results from the Librarian.

On a Pi 5, this overlaps 2–4s of embedding work per senator with the LLM wait, saving 200–400s across 100 senators — a ~10–15% wall-clock reduction at zero additional peak memory cost, since the Librarian only runs one senator ahead.

LLM prompts use **context compression**: platform text is distilled into concise policy-topic bullets rather than raw scraped HTML, keeping prompts within the 2,048-token context budget of the 1.5B model.

### Why Embedding-First, LLM-Sparingly?

The most important architectural decision is what *not* to use the LLM for.

The LLM is called only when the output requires:
- Natural language synthesis (per-senator narrative summaries, promise analysis text)
- Irreducibly context-dependent reasoning (reading the senator's full platform against their votes)

Everything else uses geometric methods in sentence-embedding space:

| Task | Method | Rationale |
|------|---------|-----------|
| Bill policy area | Nearest centroid (14 prototypes) | Deterministic, explainable, ~100ms vs. ~30s |
| Donor industry | k-NN (300+ labeled entities) | Generalizes from precedent; full audit trail |
| Party alignment | Nearest centroid (party platform corpora) | Content-based, not vote-based — avoids circular reasoning |
| Lobbying conflicts | Cosine similarity (donor industry ↔ bill policy) | Transparent, reproducible threshold |
| Key vote selection | Composite score: party deviation + donor overlap | Fully deterministic |
| Monitor deduplication | Pairwise cosine (full text + title-only) | Robust to surface paraphrase |
| Issue deduplication | Post-LLM title embedding similarity | Catches LLM-generated near-duplicates missed by pre-filtering |
| Issue topic continuity | Cosine similarity across 2-day lookback | Ensures same story maps to same DB row across runs and rank changes |

LLM calls per full run: ~100–400 (one narrative per senator/rep, plus daily action issues). Embedding operations per run: ~50,000. The pipeline is a **semantic classification and retrieval system** that uses a language model only at the final synthesis step.

### Why Local Inference?

1. **No data exfiltration.** Senator donor records, promise evaluations, and issue analyses never leave the local network.
2. **Cost at scale.** ~500 LLM calls/day × 365 = ~180,000 calls/year. At cloud API pricing, hundreds of dollars annually. At 0 marginal cost on the Pi.
3. **Reproducibility.** Model weights are pinned. An analysis run today produces identical output to one run six months ago on the same input. Cloud-hosted models update without notice.
4. **Latency independence.** No rate limits, no network jitter, no API quota.

The choice of Qwen2.5 1.5B over larger alternatives (7B+) is deliberate. The inference tasks here are structured extraction — completing a constrained template (list key facts, classify stance, extract promise text) — not open-ended generation. Empirically, a 1.5B-class model produces acceptable quality on these tasks at ~3s/call on ARM, vs. 25–45s for a 7B model. The quality ceiling is determined by the structure of the prompt, not model size.

---

## Classification Strategy

Classification decisions — what industry a donor belongs to, which direction a bill leans, whether an entity should be skipped from donor attribution — are made by embedding similarity against natural-language category prototypes, kNN over an accumulated reference corpus, or exact structured-data lookups, never by an arbitrary keyword-to-category judgment call. The pipeline uses a tiered strategy following computational parsimony (Jurafsky & Martin 2023):

| Tier | Technique | Speed | Used For |
|------|-----------|-------|----------|
| 1 | FEC metadata / learning store exact match | Instant | Donor types, previously classified bills and donors |
| 2 | Sentence-transformer embeddings (cosine similarity) | Fast | Bill policy areas, industry, party alignment, donor types, stance direction, procedural detection, skip entity detection, employer filtering, memo transfer detection |
| 2b | SVD / PageRank on cosponsorship matrix | Fast | Ideology scoring (Tauberer 2012), legislative leadership (Brin & Page 1998) |
| 3 | k-Nearest Neighbor in embedding space | Fast | Remaining unclassified donors (~5%), bill classification from reference corpus |
| 4 | LLM (Qwen2.5 1.5B via llama.cpp) | Slow | Narrative synthesis, promise analysis, PAC identification |

Key embedding-based classification features:
- **Semantic prototypes** define each category via natural-language descriptions, not keyword lists. The embedding model matches entities to the nearest prototype by cosine similarity.
- **PAC decontextualization** detects "[Industry] PAC" naming patterns via a PAC-context prototype and margin-based runner-up selection, replacing regex suffix stripping.
- **Self-funded detection** uses SequenceMatcher ratio (Ratcliff & Obershelp 1988) for fuzzy name similarity instead of exact string matching.
- **Batch skip detection** classifies employer names and memo texts against skip prototypes in vectorized batches for performance.
- **Semantic category normalization** maps stale/unknown category labels to valid industries via embedding similarity, replacing a hardcoded alias table.
- **Stance direction** is derived primarily from embedding similarity against pro/anti/neutral action prototypes — see the disclosed exception below.
- **Procedural bill detection** uses embedding similarity against a procedural prototype instead of substring matching.

### Disclosed exceptions

A small number of narrow, code-commented precision pre-filters run ahead of the embedding classifier for specific, measured embedding-model weaknesses. Each is a fast-path for a case the embedding model demonstrably mis-scores, not a replacement for it — everything not caught by the pre-filter still goes through the real classifier below it:
- **Bill stance direction** (`bill_analyzer.py::derive_stance`): a tier-0 check against the same word set used to build the pro/anti embedding prototypes, used only to break ties or lower the acceptance margin when the embedding result is already ambiguous. Verified empirically (2026-07, n=2979 real bill titles): removing it changes 1.5% of outcomes, always by recovering a genuinely directional bill ("STOP CCP Act") the embedding alone scored as neutral.
- **Hotel/lodging industry classification** (`industry_classifier.py::classify_industry_with_provenance`): hotel brand names measurably score as MEDIA rather than REAL_ESTATE in the embedding space (a specific, verified anomaly); a small brand/suffix check corrects this before the embedding path runs.
- **PAC and payment-processor detection** (`donor_classifier_ai.py`): an org name containing "PAC" (an FEC filing convention) or matching a handful of named payment-processor brands (ActBlue, WinRed, Anedot, etc. — a closed, real-world set, not a category judgment) is caught by a keyword check ahead of the embedding path, since ALL-CAPS FEC-formatted names score inconsistently against mixed-case prototypes.

These three are documented at the point of definition in code, alongside two purely structural lookups that decode already-known facts rather than classify anything: an FEC entity-type-code map (`CCM`→`CandidateAffiliated`, etc. — decoding an enum FEC itself assigned) and a Congress-number→majority-party table used for effectiveness baselines.

### Retrieval-Augmented Classification (RAC)

A persistent learning store (SQLite `LearnedClassification`) accumulates labeled classifications across pipeline runs. A vector reference corpus (ChromaDB) grows with each run. Together they implement a retrieval-augmented classification pattern: past decisions inform future ones, reducing both latency and error rate over time (Lewis et al. 2020).

Confidence levels distinguish source quality:
- `1.0` — rule-based (FEC metadata, exact match)
- `0.9` — embedding-based (cosine similarity classification)
- `0.7` — LLM-based (structured extraction)

This enables selective re-verification: low-confidence classifications from previous runs can be re-evaluated when related code changes.

**Version-aware artifact management** ensures updated analysis algorithms always produce fresh results. At pipeline start, a SHA-256 fingerprint of all analysis source files is compared to the stored hash from the last run. If the code has changed, stale artifacts (LLM cache, learned classifications, kNN reference corpus) are cleared so updated algorithms start clean. The API cache (raw Congress.gov / FEC / GovInfo responses) is never cleared — it reflects source data, not processing logic.

### Party Alignment (Content-Based)

Party alignment for bills is determined by **what the bill does**, not how senators voted on it. This addresses a fundamental limitation of roll-call-based ideology measures (Poole & Rosenthal 1985; Clinton, Jackman & Rivers 2004): vote outcomes reflect party discipline, logrolling, and strategic calculation as much as the bill's ideological content.

The system implements a nearest-centroid classifier (Rocchio 1971) in sentence-embedding space:
1. Each party's platform positions per policy area are embedded as centroids
2. Bill text is embedded and compared to both party centroids
3. Stance direction (pro/anti) disambiguates policy-area overlap
4. Vote tallies refine (not override) the content-based classification
5. Sponsor party data serves as supervised ground truth for adaptive learning

Independent senators have their caucus party inferred mathematically from voting patterns (proportion of votes aligning with each party), ensuring they are scored fairly against the party they actually caucus with.

---

## Action Center Pipeline (Hourly)

The Action Center is intentionally separate from the nightly pipeline because it operates on different timescales and data characteristics:

```
Every hour at :15
       │
       ▼
  1. FETCH ─── RSS (AP / NPR / BBC / PBS) + Google Trends + Reddit
       │         48-hour article window; direct URLs only (no redirect wrappers)
       ▼
  2. FILTER ── Embed each article against 18 US policy prototypes
       │         Discard cosine_sim < 0.22 (off-topic articles)
       ▼
  3. CLUSTER ─ Pairwise cosine similarity on title embeddings
       │         Merge clusters starting at centroid similarity 0.20, self-
       │         calibrated upward in 0.05 steps (to 0.61 max) to avoid
       │         collapsing everything into one mega-cluster
       ▼
  4. RANK ──── score = 0.40 × (civic actionability) + 0.35 × (source breadth) + 0.25 × (trending score)
       │         Actionability leads: officials mentioned + similarity to the
       │         ingested civic-document corpus, not hand-authored keywords
       │         Select top 4 clusters (MAX_ISSUES)
       ▼
  5. LLM ───── Per cluster: neutral summary + key facts + citizen actions
       │         Post-generation title deduplication (cosine_sim > 0.82)
       ▼
  6. PERSIST ─ Topic-keyed matching: each unique story maps to one permanent
       │         DB row regardless of rank changes or brief displacement.
       │         Same story + no new articles → update rank silently, no repost.
       │         Same story + new articles → update content, allow Bluesky repost.
       │         Brand new story → create new row.
       ▼
  7. ENRICH ── ChromaDB semantic search → link related bills/senators
       │         Resolve bill IDs mentioned in article text
       ▼
  8. MONITORS ─ Detect cross-day recurring topics (similarity > 0.50)
       │          Create/update NationalMonitor records (min 3 days)
       │          Re-merge duplicate monitors (title OR full sim > 0.50)
       ▼
  9. TIMELINE ─ Record daily TimelineEntry
       │          At week/month/year boundaries: LLM generates period summary
       ▼
 10. BLUESKY ── Post new/updated issues (LLM-written, with staleness framing
       │          if event predates today). Daily senator score spotlight.
       │          Weekly civic summary.
       │          Repost + like outlet posts that match active issues.
```

**Why cluster before ranking?** Articles about the same event arrive from multiple outlets within minutes. Without clustering, all 4 "top issues" would be the same story from AP, NPR, BBC, and PBS. Clustering first, then ranking by source breadth, surfaces the 4 most distinct newsworthy topics.

**Why filter at 0.22 cosine similarity?** The policy prototype filter is deliberately permissive. False negatives (dropping a real policy story) are worse than false positives. The LLM step handles borderline cases through its non-partisan framing constraint.

**Why a 3-day monitor threshold?** A topic appearing on 3+ distinct days is structurally different from a one-day news spike — it indicates a developing situation citizens may need to track. A 2-day threshold creates too many ephemeral monitors.

**Why post-LLM title deduplication?** Article clusters with overlapping coverage (different outlets, slightly different angles) can have pre-LLM embedding similarity below the merge threshold (< 0.40), yet the LLM generates near-identical titles for both. A post-generation cosine similarity check at 0.82 on title embeddings catches these cases and drops the duplicate before it reaches the database.

**Why topic-keyed matching instead of rank-slot matching?** The original design keyed issues by `(date, rank)`. When the same story briefly fell off the top 4 and returned, a new row was created with `bsky_posted_at=None`, triggering a duplicate Bluesky post. Topic-keyed matching (2-day lookback by cosine similarity) ensures the same story always maps to the same row. New articles advance `primary_article_date` and allow a repost; more outlets covering the same event do not.

---

## Bluesky Integration

The Civitas Bluesky account (`@civitas-research.org`) is updated automatically by the hourly pipeline:

| Post type | Trigger | Content |
|-----------|---------|---------|
| **Issue post** | New topic enters action center, or existing topic gets articles with a newer date | LLM-written 1–3 sentence summary. If event predates today, post opens with "Yesterday: …" or "On [date]: …" |
| **Senator spotlight** | Once per day (cycles highest → lowest scorers) | LLM-written score highlight with data from Civitas scorecard |
| **Weekly summary** | Once per week (6-day cooldown) | LLM-written condensed week-in-review from the timeline pipeline |
| **Repost + like** | Outlet post matches an active issue (cosine sim ≥ 0.78) | Reposts + likes posts from AP, BBC, NPR, PBS NewsHour; max 3 per hourly run |

Each issue links back to its permanent Civitas permalink (`/issue/<id>`). The permalink is stable — issue IDs never change even as content is updated.

---

## Scoring

### Non-Partisan by Construction

The scoring formulas deliberately avoid any ground truth that encodes partisan assumptions. Every dimension measures *structural behavior* against a neutral baseline, not agreement or disagreement with any policy position.

```
Overall = 0.25 × FundingIndependence
        + 0.20 × PromisePersistence
        + 0.20 × IndependentVoting
        + 0.15 × FundingDiversity
        + 0.20 × LegislativeEffectiveness

FundingIndependence = (1 − PAC_fraction) × 100
  where PAC_fraction = total_PAC_receipts / total_raised
  Outside spending (super PACs) blended at 0.5× since less direct than PAC contributions

PromisePersistence = (kept / (kept + broken + partial)) × 100
  with Bayesian shrinkage:
    α = 1 / (1 + n_promises/5)     [stronger shrinkage for fewer promises]
    score_shrunk = α × prior_mean + (1−α) × raw_score
  Prevents small-sample score inflation (Efron & Morris 1975)

IndependentVoting = (against_party_count / total_votes) × PVI_weight × 100
  where PVI_weight = 1 + (district_partisan_lean × 0.1)
  A Democrat in D+30 district needs higher deviation to reach same score
  as one in R+10 district (Carson et al. 2010)

FundingDiversity = (1 − HHI(industry_donations)) × 100
  where HHI = Σ(industry_share_i²)   [Herfindahl-Hirschman Index]
  High score = broadly-funded; low score = dominated by one sector

LegislativeEffectiveness = f(bills_sponsored, cosponsors, committee_passage_rate,
                             floor_passage_rate, leadership_score)
  Composite of bill throughput, peer support (PageRank-weighted cosponsorship),
  and committee + floor success rates
```

### Senate & House Scores

Each senator and House representative receives five sub-scores (0-100, higher = better):

| Metric | Weight | What It Measures | Key Reference |
|--------|--------|------------------|---------------|
| **Funding Independence** | 25% | PAC dependency + top-donor concentration + outside spending | Bonica 2014; Stratmann 2005 |
| **Promise Persistence** | 20% | Campaign commitments kept + floor advocacy + participation | Naurin 2011; Martin 2011 |
| **Independent Voting** | 20% | Seat-relative voting + cross-party coalition breadth + donor independence | Carson et al. 2010; Harbridge 2015 |
| **Funding Diversity** | 15% | Donor traceability + industry diversity (inverse HHI) | Rhoades 1993 |
| **Legislative Effectiveness** | 20% | Majority-status-benchmarked advancement + cosponsorship (PageRank) + volume | Volden & Wiseman 2014; Brin & Page 1998 |

House representatives use the same scoring framework, data sources, and classification pipeline as senators, ensuring comparable scores across chambers. One sourcing difference: senators' campaign commitments come from scraped senate.gov platform text (LLM-extracted, heuristic fallback), while representatives' positions are derived from the bills they sponsor and evaluated against their floor votes with embeddings only — the House pipeline makes no LLM calls for promise analysis.

Score history is tracked in `ScoreSnapshot` records so the frontend can render historical score trends per senator/representative.

Additional senator metrics (informational, not scored):

| Metric | What It Measures | Technique |
|--------|------------------|-----------|
| **Leadership Score** | Legislative influence — how many peers cosponsor this senator's bills | PageRank on cosponsorship graph (Brin & Page 1998) |
| **Ideology Score** | Behavioral ideological position derived from cosponsorship patterns | SVD on cosponsorship matrix (Tauberer 2012) |
| **Partisan Depth** | How deeply aligned with their party across policy areas | Content-based voting analysis with SVD ideology as Bayesian prior |

### Supreme Court Justice Scores

Each justice is scored on impartiality and ideological consistency based on case-level voting data from the Oyez Project and Supreme Court APIs.

### Presidential Scores

Presidents are scored on six dimensions (Independence, Follow-Through, Public Mandate, Effectiveness, Competence, Agency Alignment) using a mix of live API data (BLS employment, Federal Register executive orders) and historical records (C-SPAN Historians Survey, Gallup approval data, BEA GDP).

All scores default to 50 when data is insufficient. No LLM input is used in score calculation — formulas are deterministic and auditable.

---

## Semantic Search (Explore)

The Explore feature provides semantic search over government activity
documents without keyword matching. Documents are embedded offline (during
pipeline runs) and stored in ChromaDB; queries are embedded at request time.

**What is indexed:** Senate and House floor speeches, presidential actions
(executive orders, proclamations, memoranda), Supreme Court opinions, and
Federal Register rulemaking documents — five source types, not bill text.
Each document gets a single embedding (no chunking) over
`title + summary + body[:800 chars]`, stored with metadata: doc type, source,
date, politician name/ID, chamber.

**Query flow:**
```
User query
    │
    ▼ embed (Snowflake Arctic-XS, 384-dim)
    │
    ▼ HNSW approximate nearest-neighbor (ChromaDB, cosine distance)
    │
    ▼ top-k documents retrieved, filterable by doc type / chamber / politician / open-for-comment
    │
    ▼ ranked by relevance (default) or date
    │
    ▼ returned with excerpt, source URL, doc type, and (for open rulemakings) a comment link and deadline
```

The same embedding model used for classification is used for Explore — no
separate model is needed. Bill text itself is not indexed here; it's used
separately, title-only, for the tier-3 kNN bill-classification step in the
scoring pipeline (see Phase 3 above).

---

## Score Data Transparency

Each score shown in the UI links to a "data basis" view that surfaces the raw data behind the number. This is built without any additional data storage — the existing tables are queried at render time:

| Score | Data shown |
|-------|------------|
| Funding Independence | Top 10 donors by amount, PAC fraction, outside spending total |
| Promise Persistence | Per-promise verdict (kept/broken/partial), supporting vote or speech |
| Independent Voting | Key votes where member broke with party, bill title + vote |
| Funding Diversity | Industry breakdown pie chart from `IndustryDonation` records |
| Legislative Effectiveness | Sponsored bills, cosponsor counts, committee/floor passage rate |

The score transparency layer deliberately surfaces the underlying `KeyVote`, `Donor`, `CampaignPromise`, and `SponsoredBill` records rather than LLM-written explanations — the numbers are auditable against the source data.

---

## Public API

A rate-limited public read-only API is available without authentication at `/api/public/v1`:

```
GET /api/public/v1/senators                   All senators with scores
GET /api/public/v1/senators/{id}               Single senator
GET /api/public/v1/senators/{id}/history       Score history over time
GET /api/public/v1/representatives             All representatives with scores
GET /api/public/v1/representatives/{id}        Single representative
GET /api/public/v1/representatives/{id}/history Score history over time
GET /api/public/v1/states                      State metadata
GET /api/public/v1/search                      Semantic search over bills, lobbying records,
                                                and federal-register documents (not politician names)
```

Score weights, industry codes, and policy areas are available unauthenticated
at `GET /api/config` (not under `/api/public/v1` — it's a separate, lighter-
weight endpoint used by the frontend itself).

Rate limit: 60 requests/minute per IP. Headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.

---

## Academic Grounding

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
| Fuzzy name matching for self-funded detection | SequenceMatcher handles spelling variations, middle names | Ratcliff & Obershelp 1988 |
| PageRank for legislative leadership | Cosponsorship network centrality measures peer influence | Brin & Page 1998; Tauberer 2012 |
| SVD ideology as Bayesian prior for partisan depth | Behavioral ideological signal regularizes sparse vote data | Poole & Rosenthal 1985; Efron & Morris 1975 |

See the [Methodology page](/about) for full details and inline citations.

---

## Prerequisites

- **Docker** and **Docker Compose** (v2)
- A free **api.data.gov** API key — sign up at https://api.data.gov/signup/
- ~16 GB RAM recommended
- ~10 GB disk for Docker images and model weights
- For native LLM inference: llama.cpp compiled for your architecture

## Quick Start

```bash
# 1. Clone the repo
git clone git@github.com:kamoras/civitas.git
cd civitas

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
(default: 3 AM daily). To trigger it manually:

```bash
curl -X POST http://localhost:8000/api/admin/pipeline/trigger \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

## Deployment

### Blue/Green Architecture

The project uses zero-downtime blue/green deployment with nginx as the traffic router:

```
                    ┌─── nginx (port 80/443) ───┐
                    │   upstream backend { }     │
                    │   upstream frontend { }    │
                    └──────────┬────────────────┘
                               │ proxy_pass
              ┌────────────────┴────────────────┐
              ▼                                 ▼
   mp-backend-blue (8000)          mp-backend-green (8001)
   mp-frontend-blue (3000)         mp-frontend-green (3001)
        [idle]                          [live]
```

`deploy.sh` follows this sequence:
1. Build the new Docker image locally, or (in CI/CD) pull a pre-built image from GHCR — every push to `main` that passes CI is built once on GitHub-hosted ARM64 runners and pulled here instead of rebuilding on the Pi (`SKIP_BUILD=1`)
2. Start the new container on the idle port (e.g. green on 8001)
3. Poll `GET /health` until the container responds — up to 180s for the backend, 60s for the frontend
4. Rewrite the nginx upstream block to point to the new port
5. `nginx -s reload` — zero-downtime config swap (nginx drains in-flight requests)
6. Stop and remove the old container

The script auto-detects which slot is currently live by parsing the active nginx config, so it always starts the container on the idle slot without manual bookkeeping.

Data is persisted in a Docker named volume (`app_data`) mounted at `/app/data` inside every container. The SQLite database and ChromaDB files live here and survive container rebuilds. Both blue and green containers mount the same volume, so the new container has full access to the current database the moment it starts — no data migration needed on deploy.

### Service Layout

```
Host ports                 Container            Purpose
──────────────────────────────────────────────────────────
8000 or 8001               mp-backend-{slot}    FastAPI backend
3000 or 3001               mp-frontend-{slot}   Next.js frontend
8070                       llama-server         llama.cpp inference (systemd)
80 / 443                   nginx                Reverse proxy + TLS termination
```

llama.cpp runs as a systemd service (not Docker) so the model weights stay in RAM across backend redeploys. The backend connects to it via `http://host.docker.internal:8070`. If the llama.cpp server is unavailable, all LLM calls fall through to a timeout error and the pipeline records a per-member failure without aborting the run.

### Health Check

`GET /health` returns:
```json
{
  "status": "ok",
  "database": "ok" | "unavailable",
  "ollama": "ok" | "unavailable",
  "lastPipelineRun": "2026-07-12T03:00:00"
}
```

`deploy.sh` considers the new container healthy purely by HTTP status —
it polls until `GET /health` returns 200, without parsing the response body.
`database`/`ollama` are informational for the admin dashboard and don't
gate deployment.

## Development Setup

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
civitas/
├── backend/
│   ├── app/
│   │   ├── api/              # FastAPI route handlers
│   │   │   ├── senators.py, representatives.py, presidents.py, justices.py
│   │   │   ├── action.py     # Action center issues, monitors, timeline
│   │   │   ├── explore.py    # Semantic document search
│   │   │   ├── public.py     # Open read-only API (rate-limited, no auth)
│   │   │   └── admin.py      # Pipeline control panel
│   │   ├── services/         # Business logic (senator_service, representative_service)
│   │   ├── pipeline/
│   │   │   ├── fetch/        # API clients (Congress.gov, FEC, GovInfo, Senate.gov,
│   │   │   │                 #   Oyez, BLS, Federal Register, news RSS)
│   │   │   ├── transform/    # Data normalization + embedding-based industry classifier
│   │   │   ├── analyze/      # Scoring, classification, action center, Bluesky
│   │   │   │   ├── bill_analyzer.py          # Embedding-based bill classification + stance
│   │   │   │   ├── bill_learning.py          # Adaptive kNN reference corpus
│   │   │   │   ├── party_platform.py         # Content-based party alignment + partisan depth
│   │   │   │   ├── nn_classifier.py          # kNN donor classifier + category normalization
│   │   │   │   ├── donor_classifier_ai.py    # Tiered donor classification + batch skip
│   │   │   │   ├── sponsorship_analysis.py   # PageRank leadership + SVD ideology
│   │   │   │   ├── policy_alignment.py       # Industry↔policy area mapping
│   │   │   │   ├── cross_reference.py        # Per-senator LLM narrative
│   │   │   │   ├── action_center.py          # News clustering, LLM summarization,
│   │   │   │   │                             #   national monitors, timeline
│   │   │   │   ├── score_calculator.py       # Deterministic scoring formulas
│   │   │   │   ├── ollama_client.py          # LLM backend abstraction
│   │   │   │   ├── bluesky_poster.py         # Post new/updated action issues
│   │   │   │   ├── bluesky_spotlight.py      # Daily senator spotlight + weekly summary
│   │   │   │   ├── bluesky_engagement.py     # Repost/like matching outlet posts
│   │   │   │   └── bluesky_utils.py          # Shared link-card builder
│   │   │   ├── assemble/     # Senator scorecard builder + validator
│   │   │   ├── vector_store.py  # ChromaDB + sentence-transformer
│   │   │   └── orchestrator.py  # Pipeline control flow
│   │   ├── models.py         # SQLAlchemy ORM (Senator, Representative, KeyVote,
│   │   │                     #   Justice, ActionIssue, NationalMonitor,
│   │   │                     #   TimelineEntry, ScoreSnapshot, etc.)
│   │   ├── schemas.py        # Pydantic response schemas
│   │   ├── database.py       # DB engine, session management, lightweight migrations
│   │   ├── config.py         # Pydantic settings from .env
│   │   └── config_definitions.py  # Score weights, industry codes, policy areas
│   ├── tests/                # pytest test suite
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/app/              # Next.js app router pages
│   │   ├── about/            # Methodology page with full citations
│   │   ├── action/           # Action Center (issues, monitors, timeline)
│   │   ├── issue/[id]/       # Permanent issue permalinks (Bluesky link target)
│   │   ├── compare/          # Side-by-side senator/representative comparison
│   │   ├── explore/          # Semantic search over government documents
│   │   ├── scorecard/        # Senator, representative, president, and justice scorecards
│   │   ├── leaderboard/      # Rankings across all branches (House paginated)
│   │   ├── environmental/    # Environmental policy tracking
│   │   ├── accessibility/    # Accessibility statement
│   │   └── admin/            # Pipeline control panel with granular progress
│   ├── src/components/       # React components
│   └── Dockerfile
├── deploy.sh                 # Blue/green zero-downtime deploy
├── docker-compose.yml
├── .env.example
├── AGENTS.md                 # Project design principles and developer guide
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
| `DATABASE_URL` | No | SQLite path (default: `sqlite:///data/civitas.db`) |
| `PIPELINE_CRON_SCHEDULE` | No | Cron schedule for nightly pipeline (default: `0 3 * * *`) |
| `PIPELINE_CACHE_TTL_HOURS` | No | API response cache TTL (default: `72`) |
| `BSKY_HANDLE` | No | Bluesky handle (e.g. `civitas-research.org`) |
| `BSKY_APP_PASSWORD` | No | Bluesky app password (from Settings → App Passwords) |

## References

Full methodology with inline citations is available on the [About page](/about).
Key references:

- Bonica, A. (2014). Mapping the Ideological Marketplace. *AJPS*, 58(2), 367-386.
- Budge, I. et al. (2001). *Mapping Policy Preferences*. Oxford UP.
- Brin, S. & Page, L. (1998). The Anatomy of a Large-Scale Hypertextual Web Search Engine. *Proc. WWW 1998*.
- Carson, J. et al. (2010). The Electoral Costs of Party Loyalty. *AJPS*, 54(3), 598-616.
- Clinton, J., Jackman, S. & Rivers, D. (2004). The Statistical Analysis of Roll Call Data. *APSR*, 98(2), 355-370.
- Cover, T. & Hart, P. (1967). Nearest Neighbor Pattern Classification. *IEEE Trans. Info Theory*, 13(1), 21-27.
- Efron, B. & Morris, C. (1975). Data Analysis Using Stein's Estimator. *JASA*, 70(350), 311-319.
- Grimmer, J. & Stewart, B. (2013). Text as Data. *Political Analysis*, 21(3), 267-297.
- Laver, M., Benoit, K. & Garry, J. (2003). Extracting Policy Positions from Political Texts. *APSR*, 97(2).
- Lewis, P. et al. (2020). Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks. *NeurIPS 2020*.
- Poole, K. & Rosenthal, H. (1985). A Spatial Model for Legislative Roll Call Analysis. *AJPS*, 29(2), 357-384.
- Reimers, N. & Gurevych, I. (2019). Sentence-BERT. *EMNLP 2019*, 3982-3992.
- Snell, J. et al. (2017). Prototypical Networks for Few-Shot Learning. *NeurIPS 2017*, 4077-4087.
- Stratmann, T. (2005). Some Talk: Money in Politics. *Public Choice*, 124(1-2), 135-156.
- Tauberer, J. (2012). *Open Government Data*. GovTrack.us ideology/leadership methodology.

## License

Licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).
Unlike a permissive license (MIT, Apache), AGPL requires that anyone who runs
a modified version of Civitas as a network service — not just anyone who
redistributes it — must also publish their modifications. That closes the
loophole a permissive license leaves open: someone could otherwise fork the
scoring algorithm, quietly bias it, and host it without ever disclosing what
changed. If you can't see the source, you can't trust the score — AGPL keeps
that true for every fork, not just this repository.
