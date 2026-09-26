# Civitas — Political Representation Tracker

[![CI](https://github.com/kamoras/civitas/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kamoras/civitas/actions/workflows/ci.yml)
[![Lighthouse — Accessibility & SEO](https://github.com/kamoras/civitas/actions/workflows/lighthouse.yml/badge.svg?branch=main)](https://github.com/kamoras/civitas/actions/workflows/lighthouse.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

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
│                           DATA SOURCES (read-only)                           │
│                                                                              │
│  Congress.gov   FEC API    GovInfo API  Senate.gov  Oyez    BLS / BEA        │
│  (bills, votes, (campaign  (bill text,  (speeches,  (SCOTUS (jobs,           │
│   members)       finance)   histories)   remarks)    cases)  GDP)            │
│                                                                              │
│  AP / NPR / PBS / BBC / The Hill / Politico / Roll Call (RSS)                │
│  + 41 per-state newsrooms (election coverage)                                │
│  Google Trends     Bluesky            Vote Smart (statewide ballot           │
│                    (trending)          measures — optional, keyed)           │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │  rate-limited HTTP
                           │  (Congress 1.2 RPS, FEC 0.25 RPS, GovInfo 1.0 RPS)
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                NIGHTLY PIPELINE  (APScheduler, default 3 AM UTC)             │
│                                                                              │
│  ┌─────────┐   ┌───────────┐   ┌───────────────────────────────────────┐     │
│  │ 1.FETCH │──▶│2.TRANSFORM│──▶│            3. ANALYZE                 │     │
│  └─────────┘   └───────────┘   │                                       │     │
│                                │  ┌──────────────────────────────────┐ │     │
│                                │  │ per member, in order:            │ │     │
│                                │  │ embed (batches) → score → persist│ │     │
│                                │  │                                  │ │     │
│                                │  │ bill embeddings   donor kNN      │ │     │
│                                │  │ lobbying match    promise align  │ │     │
│                                │  │ (deterministic — no LLM call)    │ │     │
│                                │  │                                  │ │     │
│                                │  └──────────────────────────────────┘ │     │
│                                └───────────────────────────────────────┘     │
│                                                │                             │
│  ┌──────────┐   ┌──────────┐   ┌────────────┐  │   ┌──────────────────────┐  │
│  │4. EXPLORE│   │5.JUSTICES│   │6.PRESIDENTS│  │   │     7. FINALIZE      │  │
│  │ (sqlite- │   │ (Oyez)   │   │(BLS/BEA/   │◄─┘   │ (persist scores,     │  │
│  │  vec)    │   │          │   │ UCSB)      │      │  PipelineRun record) │  │
│  └──────────┘   └──────────┘   └────────────┘      └──────────────────────┘  │
│                                                                              │
│  ──────────────────── HOUSE PIPELINE (runs after Senate) ──────────────────  │
│  Own ~6-phase pipeline for all 435 representatives (FETCH MEMBERS, NORMALIZE,│
│  FETCH BILLS & VOTES, CLASSIFY BILLS, SPONSORSHIP ANALYSIS, FEC + SCORING) — │
│  reuses the unified EXPLORE pipeline for House floor speeches                │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │ writes
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              PERSISTENCE LAYER                               │
│                                                                              │
│  SQLite  (civitas.db)                    sqlite-vec  (vectors.db)            │
│  ├── senators / representatives          ├── vec_explore  (documents)        │
│  ├── KeyVote / SponsoredBill             └── vec_bills    (classification)   │
│  ├── Donor / IndustryDonation / LobbyingMatch                                │
│  ├── CampaignPromise                                                         │
│  ├── ActionIssue / NationalMonitor / MonitorUpdate                           │
│  ├── TimelineEntry / WeekSummary / MonthSummary / YearSummary                │
│  ├── ScoreSnapshot  (historical score tracking per senator/rep)              │
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
│  APScheduler ──▶ hourly action refresh  ────▶│  LFM2.5-1.2B-Instruct  │      │
│                                              │  port 8070 (Docker)    │      │
│                                              └────────────────────────┘      │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │ JSON
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  Next.js 16 frontend  (port 3000)                                            │
│  /politicians  /bills  /leaderboard  /action  /explore  /compare             │
│  /elections  /elections/states/<ST>  /issue  /about  /changelog  /admin       │
│  /accessibility  /feedback                                                   │
└──────────────────────────────────────────────────────────────────────────────┘
```

> **Rendered diagrams:** [`docs/diagrams/`](docs/diagrams/) has Mermaid versions of
> this and eight other views — pipeline internals, classification tiers, scoring
> composition, the schema, and the deploy sequence — with more detail than ASCII
> fits. They render as pictures on GitHub. The diagrams here stay ASCII so the
> README reads the same in a terminal.

**Server rendering:** `/action`, `/leaderboard`, `/bills`, `/explore` and
`/compare` keep their view state in the address bar, and `useSearchParams()` has
no value during SSR — so React suspends and the server renders the nearest
`<Suspense>` fallback. Each of those pages supplies a real one
(`components/layout/PageFallback.tsx`: navbar, masthead, content skeleton), so
the frame arrives in the first paint. They previously wrapped the *entire* page
in `<Suspense fallback={null}>`, which made the server send a ~12KB body with no
chrome and nothing visible until ~653KB of JavaScript had parsed and hydrated.
The data itself still arrives after hydration: these routes are prerendered and
the content is per-request.

**Hardware:** Raspberry Pi 5 (16 GB RAM), NVMe SSD. All models, databases, and services run on-device. No cloud GPU, no third-party AI APIs, no data leaves the device.

---

## Nightly Pipeline: Phase by Phase

The nightly pipeline processes every senator and House representative through seven sequential phases before persisting scores and snapshots. Those phases are one link in a five-pipeline **chain**, run in this order by `scheduler.py`:

```
Senate ──▶ Supplementary ──▶ House ──▶ Stock trades ──▶ Election
           (explore docs,              (stock_pipeline)  (election_pipeline)
            SCOTUS, PVI,
            presidents)
```

**Each link starts only if the one before it finished, so a failure partway down means every pipeline after it silently does not run at all** — not a degraded run, no run. That is an operational property worth knowing before reading the phases below: in September 2026 the chain stopped inside Supplementary and House, Stock trades and Election did not execute for 19 nights, because a pipeline that never starts leaves no row behind to look wrong. `ops_alerts.check_pipeline_staleness` exists specifically to catch that shape (a pipeline with no successful completion in `PIPELINE_STALE_ALERT_DAYS`), alongside `check_pipeline_overrun`, which catches the opposite case of a run that started and is taking too long.

### Phase 1 — FETCH

Pulls raw data from each government API and stores the complete response verbatim in `ApiCache` before any transformation:

| Source | What is fetched | Rate limit |
|--------|-----------------|------------|
| Congress.gov | Bills sponsored/cosponsored (last 2 years), roll-call votes | 1.2 RPS |
| FEC API | Campaign finance transactions, committee receipts, PAC committee types | 0.25 RPS |
| GovInfo API | Full bill text for key votes (PDF → text extraction) | 1.0 RPS |
| Senate.gov | Floor speeches, press remarks (scraped; no public API) | polite crawl |
| Oyez / SCOTUS | Justice voting records, case metadata | 0.5 RPS |
| BLS | Unemployment, inflation, job growth by administration | batch |
| BEA | GDP growth by quarter | batch |
| Federal Register | Executive orders signed per administration | 1.0 RPS |
| House Clerk / Senate eFD | STOCK Act periodic transaction reports (PDF/HTML, parsed) | 1.0 / 0.5 RPS |
| OGE | Sitting president's OGE Form 278-T periodic transaction reports — disclosed securities and virtual-currency buys/sells (PDF, parsed) | 0.5 RPS |
| SEC | Ticker -> company name resolution for trade-industry classification | batch |
| Voteview | Congress-specific (Nokken-Poole) member ideal points (per-congress CSV exports), feeding Constituent Alignment's position-congruence component | batch |

Nothing from the API cache is ever cleared — it represents immutable source data. A fresh run can replay against the cached responses without re-hitting the external APIs (controlled by `PIPELINE_CACHE_TTL_HOURS`).

### Phase 2 — TRANSFORM

Normalizes raw API payloads into typed domain objects. Key operations:

- **FEC deduplication**: FEC records contain duplicate committee entries from amended filings. The transform layer resolves these by matching on committee ID and keeping the most recent amendment.
- **Bill title normalization**: Congress.gov returns bill titles in several formats (official, short, display, popular). Transform picks the most human-readable, falling back through the hierarchy.
- **Employer name normalization**: FEC contributor employer fields are free text. A batch embedding pass maps variant spellings (e.g. "Goldman Sachs & Co", "GOLDMAN SACHS") to a canonical form before classification.
- **Memo text parsing**: FEC memo text fields encode earmarks and transfers in free-form text. A batch skip-entity classifier separates genuine contributions from administrative memo entries.

### Phase 3 — ANALYZE

The heaviest phase, run per member. Fully deterministic — no LLM call. (Two
LLM-based steps used to live here: PAC-donor classification and narrative
summary generation. Both were removed in 2026-07 after live audits found
their output unreliable — generic boilerplate, occasionally fabricated
per-vote reasoning — regardless of prompting approach. See
`cross_reference.py`'s module docstring.)

1. Embed all sponsored/cosponsored bill titles → classify policy areas (nearest centroid, 18 prototypes)
2. Embed all donor employer names → classify industries (tiered: exact match → embedding → kNN)
3. Compute lobbying conflicts: cosine similarity between donor industries and bill policy areas
4. Select key votes: composite score = party deviation + donor industry overlap
5. Embed platform text → extract policy topics (sentence-transformer, not LLM)
6. Embed floor speeches → compute party alignment (nearest centroid vs. party platform corpora)
7. Compute corruption/representation sub-scores and persist the member's scorecard

### Phase 4 — EXPLORE

Builds the search indexes over government activity documents — floor
speeches (Senate and House), presidential actions (executive orders,
proclamations, memoranda), Supreme Court opinions, and Federal Register
rulemaking documents (including ones still open for public comment):
- One embedding per document — no chunking — over `title + summary + body[:800 chars]`
- Encodes with the **index** sentence-transformer (384-dim, all-MiniLM-L6-v2)
- Upserts into the `vec_explore` sqlite-vec table with metadata: doc type, source, date, politician name/ID, chamber
- Rebuilds the `explore_fts` FTS5 keyword index (BM25F over title/summary/body)
- Recomputes citation-graph PageRank into `explore_documents.authority`
- At query time: both channels run, filters pushed into each, results fused by weighted reciprocal rank fusion with recency and authority priors — see "Hybrid Search (Explore)" below

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
- **Live**: BLS employment rate, BEA/FRED GDP growth, Federal Register rulemaking counts, UCSB American Presidency Project approval polling
- **Historical**: C-SPAN Presidential Historians Survey (historical legacy), UCSB election-margin data, MeasuringWorth real-GDP series (1790–present)
- All four score dimensions (Public Mandate, Effectiveness, Agency Alignment, Historical Legacy) are computed from source data with no LLM involvement. Dimensions a president has no real data source for are left N/A rather than filled with a placeholder.

### Phase 7 — FINALIZE

Persists everything computed in phases 3–6:
- Writes senator/representative scores, key votes, lobbying matches, campaign promises, sponsored bills to SQLite
- Appends a `ScoreSnapshot` record (all 5 sub-scores + overall) for each member — enables historical score trend charts
- Records a `PipelineRun` with phase timings, counts, and any per-member errors
- Runs a SHA-256 fingerprint over all analysis source files (docstring-stripped ASTs, so comment-only edits don't count); if changed since last run, clears `AnalysisCache` and `LearnedClassification` so stale results from the old code are not served

---

## Caching Architecture

Three independent caching systems serve different purposes:

```
┌────────────────────────────────────────────────────────────────────┐
│  ApiCache  (SQLite: api_cache)                                     │
│  Key: (tier, endpoint+params_hash)   TTL: 72h (configurable)       │
│  Stores: raw API JSON, verbatim                                    │
│  Cleared: never (source data is immutable; re-fetched after TTL)   │
│  Purpose: replay pipeline without re-hitting external APIs         │
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

The fingerprint check at pipeline start compares a SHA-256 of every analysis module's docstring-stripped AST to the hash stored in the last `PipelineRun`. If they differ, `AnalysisCache` and `LearnedClassification` are cleared so updated logic produces fresh results. `ApiCache` is never cleared by fingerprint — source data doesn't change when analysis code does.

---

## Data Pipeline: Design Rationale

The pipeline is structured around a specific set of constraints that shape every decision.

### Why a Nightly Batch Pipeline?

A 100-senator + 435-representative full refresh requires 4–6 hours cold (warm: 45–90 minutes). Online/streaming processing is not viable at these volumes on the target hardware: the sentence-transformer model occupies ~90 MB, the LLM occupies ~900 MB, and peak memory during the analyze phase (overlapped embedding + LLM) reaches ~3 GB. Batching allows us to control memory precisely, while a separate hourly pipeline handles the Action Center's lower-latency requirements.

The pipeline uses a SQLite-level mutex (`PipelineRun.status == "running"`) rather than a process-level lock, making it safe to deploy in multi-container blue/green environments: any new container discovering an in-progress run on startup marks it `stale` rather than blocking.

### Why an Adversarial Data Architecture?

Government data sources are not designed for machine consumption. Congress.gov returns inconsistent bill title formats; FEC records contain duplicate committee entries; senate.gov lacks a public API. The **FETCH → TRANSFORM** separation isolates source-specific parsing from domain logic. Every raw API response is stored verbatim in `ApiCache` (never cleared, TTL=72h), so the pipeline can be replayed deterministically against historical snapshots — essential for debugging and auditing.

Rate limits are enforced at the source level (Congress.gov: 1.2 RPS, FEC: 0.25 RPS, GovInfo: 1.0 RPS) with per-API backoff, not global throttling. This prevents a slow FEC response from stalling the Congress.gov queue.

### Why Embedding-First, LLM-Sparingly?

The most important architectural decision is what *not* to use the LLM for.

Per-senator/rep analysis (Phase 3) is fully deterministic — no LLM call at
all. Two LLM-based steps used to live there (PAC-donor classification,
narrative summary generation, plus a separate campaign-promise-tracking
feature); all were removed in 2026-07 after live audits found their output
unreliable regardless of prompting approach. See `cross_reference.py`'s
module docstring for the measured cost/quality numbers that motivated the
removal.

The LLM is still used where the output genuinely requires natural-language
synthesis from unstructured input: Action Center issue generation (daily
news clustering → structured facts/summary) and Supreme Court justice
profile summaries (9 justices, from pre-computed voting statistics).

Everything else uses geometric methods in sentence-embedding space:

| Task | Method | Rationale |
|------|---------|-----------|
| Bill policy area | Nearest centroid (18 prototypes) | Deterministic, explainable, ~100ms vs. ~30s |
| Donor industry | k-NN (300+ labeled entities) | Generalizes from precedent; full audit trail |
| Party alignment | Nearest centroid (party platform corpora) | Content-based, not vote-based — avoids circular reasoning |
| Lobbying conflicts | Cosine similarity (donor industry ↔ bill policy) | Transparent, reproducible threshold |
| Key vote selection | Composite score: party deviation + donor overlap | Fully deterministic |
| Monitor deduplication | Pairwise cosine (full text + title-only) | Robust to surface paraphrase |
| Issue deduplication | Post-LLM title embedding similarity | Catches LLM-generated near-duplicates missed by pre-filtering |
| Issue topic continuity | Cosine similarity across 2-day lookback | Ensures same story maps to same DB row across runs and rank changes |

LLM calls per full nightly run: 0 (Phase 3 is fully deterministic, see above). The LLM runs on its own schedule for Action Center issue generation (hourly, a handful of calls per run) and the Supreme Court justice pipeline (9 calls, once per nightly run). Embedding operations per full nightly run: ~50,000. The pipeline is a **semantic classification and retrieval system** that uses a language model only where natural-language synthesis is unavoidable.

### Why Local Inference?

1. **No data exfiltration.** Senator donor records and issue analyses never leave the local network.
2. **Cost at scale.** Cloud API pricing for even a modest daily call volume runs to hundreds of dollars annually. At 0 marginal cost on the Pi.
3. **Reproducibility.** Model weights are pinned. An analysis run today produces identical output to one run six months ago on the same input. Cloud-hosted models update without notice.
4. **Latency independence.** No rate limits, no network jitter, no API quota.

The choice of LFM2.5-1.2B-Instruct over larger alternatives (7B+) is deliberate. The inference tasks here are structured extraction — completing a constrained template (list key facts, classify stance, extract promise text) — not open-ended generation. Empirically, a ~1B-class model produces acceptable quality on these tasks in a few seconds per call on ARM, vs. 25–45s for a 7B model. The quality ceiling is determined by the structure of the prompt, not model size.

---

## Classification Strategy

Classification decisions — what industry a donor belongs to, which direction a bill leans, whether an entity should be skipped from donor attribution — are made by embedding similarity against natural-language category prototypes, kNN over an accumulated reference corpus, or exact structured-data lookups, never by an arbitrary keyword-to-category judgment call. The pipeline uses a tiered strategy following computational parsimony (Jurafsky & Martin 2023):

| Tier | Technique | Speed | Used For |
|------|-----------|-------|----------|
| 1 | FEC metadata / learning store exact match | Instant | Donor types, previously classified bills and donors |
| 2 | Sentence-transformer embeddings (cosine similarity) | Fast | Bill policy areas, industry, party alignment, donor types, stance direction, procedural detection, skip entity detection, employer filtering, memo transfer detection |
| 2b | SVD / PageRank on cosponsorship matrix | Fast | Ideology scoring (Tauberer 2012), legislative leadership (Brin & Page 1998) |
| 3 | k-Nearest Neighbor in embedding space | Fast | Remaining unclassified donors (~5%), bill classification from reference corpus |
| 4 | LLM (LFM2.5-1.2B-Instruct via llama.cpp) | Slow | Action Center issue synthesis, justice profile summaries |

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

These three are documented at the point of definition in code, alongside two purely structural lookups that decode already-known facts rather than classify anything: an FEC entity-type-code map (`CCM`→`CandidateAffiliated`, etc. — decoding an enum FEC itself assigned) and a Congress-number→majority-party table for past congresses (the current congress's majority is read from the live roster each run) used for effectiveness baselines.

### Retrieval-Augmented Classification (RAC)

A persistent learning store (SQLite `LearnedClassification`) accumulates labeled classifications across pipeline runs. A vector reference corpus (the `vec_bills` sqlite-vec table) grows with each run. Together they implement a retrieval-augmented classification pattern: past decisions inform future ones, reducing both latency and error rate over time (Lewis et al. 2020).

Confidence levels distinguish source quality:
- `1.0` — rule-based (FEC metadata, exact match)
- `0.9` — embedding-based (cosine similarity classification)
- `0.7` — LLM-based (structured extraction)

This enables selective re-verification: low-confidence classifications from previous runs can be re-evaluated when related code changes.

**Version-aware artifact management** ensures updated analysis algorithms always produce fresh results. At pipeline start, a SHA-256 fingerprint of all analysis source files (their docstring-stripped syntax trees, so comment edits don't count) is compared to the stored hash from the last run. If the code has changed, stale artifacts (LLM cache, learned classifications, kNN reference corpus) are cleared so updated algorithms start clean. The API cache (raw Congress.gov / FEC / GovInfo responses) is never cleared — it reflects source data, not processing logic.

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
  1. FETCH ───── RSS (AP, NPR, PBS, BBC, The Hill, Politico, Roll Call —
       │         9 feeds across 7 newsrooms; NPR's two desks count as one)
       │         + Google Trends + Bluesky trending
       │         48-hour article window; direct URLs only (no redirect wrappers)
       │         Reddit was a third trending source until 2026-09: it now
       │         requires OAuth for datacenter traffic and 403s everything
       │         else, so it was retired rather than left silently empty.
       │         A source that FAILS is reported as failed, never merged in
       │         as a source that simply had nothing to say.
       ▼
  2. FILTER ──── Embed each article against 24 policy prototypes (19 US, 5 intl.)
       │         Discard cosine_sim < 0.22 (off-topic articles)
       ▼
  3. CLUSTER ─── Pairwise cosine similarity on title embeddings
       │         Merge clusters starting at centroid similarity 0.20, self-
       │         calibrated upward in 0.05 steps (to 0.61 max) to avoid
       │         collapsing everything into one mega-cluster
       ▼
  4. RANK ────── score = 0.40 × (civic actionability)
       │                 + 0.35 × (source breadth)
       │                 + 0.25 × (trending score)
       │         Actionability leads: officials mentioned + similarity to the
       │         ingested civic-document corpus, not hand-authored keywords
       │         Select the top clusters (MAX_ISSUES = 2)
       ▼
  5. EXTRACT ─── The model LOCATES an assertion in one article; it never
       │         writes the sentence. post_composer.py checks both spans
       │         appear verbatim, that the source asserts one OF the other
       │         (adjacency — two true fragments can otherwise be assembled
       │         into one false sentence), that the span runs to the end of
       │         its clause, and only then renders "actor predicate."
       │         Title = the top article's real headline. Summary = the
       │         single best claim. Facts = the supporting claims, each
       │         carrying the outlet it came from.
       │         A cluster with no attributable assertion produces NO issue:
       │         MAX_ISSUES is a ceiling, not a quota. Publishing anyway is
       │         what produced "This coverage tracks the race and related
       │         developments."
       │         Post-composition title deduplication (cosine_sim > 0.92)
       ▼
  6. PERSIST ─── Topic-keyed matching: each unique story maps to one permanent
       │         DB row regardless of rank changes or brief displacement.
       │         Same story → update content; repost only when a newer article
       │         also brings new information (a name, figure, or development).
       │         Otherwise the rank updates silently and nothing is posted.
       │         Brand new story → create new row.
       ▼
  7. ENRICH ──── sqlite-vec semantic search → link related bills/senators
       │         Resolve bill IDs mentioned in article text
       ▼
  8. MONITORS ── Detect cross-day recurring topics (title similarity ≥ 0.83)
       │         Create/update NationalMonitor records (min 5 distinct
       │         days in 14, ≥3 unique sources, LLM significance gate)
       │         Re-merge duplicate monitors (title OR full sim > 0.50)
       ▼
  9. TIMELINE ── Record daily TimelineEntry
       │         At week/month/year boundaries: LLM generates period summary
       ▼
 10. BLUESKY ─── Post new/updated issues (LLM-written, with staleness framing
       │         if event predates today). Daily senator score spotlight.
       │         Weekly civic summary.  (spotlight + weekly also run on the
       │         early-abort paths above — neither depends on the news)
       │         Repost + like outlet posts that match active issues.
```

**Why cluster before ranking?** Articles about the same event arrive from multiple outlets within minutes. Without clustering, every "top issue" would be the same story from AP, NPR, BBC, and PBS. Clustering first, then ranking by source breadth, surfaces the most distinct newsworthy topics.

**Why filter at 0.22 cosine similarity?** The policy prototype filter is deliberately permissive. False negatives (dropping a real policy story) are worse than false positives. Borderline cases are handled downstream by extraction rather than by asking a model to be neutral: a cluster that yields no verbatim, adjacently-asserted claim simply produces no issue.

**Why a 5-day monitor threshold?** A topic appearing in the top issues on 5+ distinct days within two weeks is structurally different from a one-day news spike — it indicates a developing situation citizens may need to track. Shorter thresholds created too many ephemeral monitors.

**Why topic-keyed matching instead of rank-slot matching?** The original design keyed issues by `(date, rank)`. When the same story briefly fell off the top slots and returned, a new row was created with `bsky_posted_at=None`, triggering a duplicate Bluesky post. Topic-keyed matching (2-day lookback by cosine similarity) ensures the same story always maps to the same row. New articles advance `primary_article_date`; more outlets covering the same event do not.

**What makes a repost?** A newer article date is necessary but not sufficient — recap coverage rewords the same story under a fresher timestamp, which used to repost with nothing new to say. The new facts must also introduce either a named entity/figure or a story development (a veto, a court blocking an order, a failed override) that the facts *as of the last post* lacked. The baseline is `bsky_posted_facts`, not the live `facts` column: `facts` is rewritten on every hourly refresh whether or not anything was posted, so baselining on it let a development that surfaced between posts be absorbed and never read as new again. Rows that predate the column are backfilled from `facts` at startup — the poster is the only writer of `bsky_posted_facts` and it only ever sees issues the repost gate has already released, so a NULL baseline on an already-posted row could never resolve itself. The backfill is unconditional rather than fired once on the migration: the poster writes `bsky_posted_at` and `bsky_posted_facts` in the same commit, so a row with the first set and the second NULL can only predate the column, and a seeded row stops matching.

**How do you tell a quiet news day from an over-suppressing gate?** Both look the same from outside: nothing on the Action Center, nothing on Bluesky. Roughly ten independent checks in this pipeline fail closed — the right default when the platform publishes under its own name, but it means silence is the shared failure mode of all of them. Every refresh records what came in (`articles_fetched`, `articles_policy_relevant`, `clusters_considered`), what published (`issues_new_topic`, `issues_matched_existing`, `bsky_reposts_allowed`), and what each gate dropped, including on the two abort paths that publish nothing at all. `GET /api/admin/action-metrics` reads the window back with those three groups totalled: healthy intake against near-zero output is a suppression problem, near-zero intake is a quiet cycle or a broken feed. Runs are hourly, so a gap in the series is itself a signal — a refresh that crashed or was still holding the lock leaves no row.

---

## Election Pipeline (Nightly)

An independent pipeline (`app/pipeline/election_pipeline.py`) with no data dependency on the Senate/House/President runs. Six phases, each fault-isolated so one failure doesn't take the others down:

```
1. ROSTER SYNC ──── every declared FEC candidate for the cycle → Race / Candidate rows
2. FINANCIAL ────── prioritized, watermarked batch of 500 (FEC allows 0.25 req/s and a
   REFRESH            midterm cycle has ~6,900 candidates — the roster rotates over runs)
3. BALLOT ───────── statewide ballot measures per state (Vote Smart), + a liveness
   MEASURES           check on the official-ballot links the site hands users
4. COVERAGE ─────── RSS matched to races by candidate name with mandatory
   INGESTION          corroboration; 9 national feeds + 41 per-state newsrooms;
                      tighter cadence in election season. The open Bluesky
                      name search is DISABLED — see below.
5. BLUESKY ──────── one grounded, source-backed sentence per notable coverage item
6. SNAPSHOT ─────── changed-only fundraising snapshots for trend charts
```

### Confirmed candidates (who is actually on the ballot)

The FEC roster in phase 1 lists everyone who *filed* — including candidates who already lost their primary months ago. Showing all of them on a ballot page is not a cosmetic problem: it presents losers as options. A second, independent layer answers "who is really on the November ballot" from each state's own election authority.

The whole flow — sources, matching, and what a race page may show — is drawn in [`docs/diagrams/10-elections.md`](docs/diagrams/10-elections.md).

The constraint that shapes it: **no 50 bespoke scrapers.** Adapters are per *vendor*, not per state, so a state whose vendor is already supported is a JSON entry in `backend/app/data/state_candidate_sources.json`, never new code. Only a genuinely different vendor earns a module. **No adapter branches on a state's name** — every URL, slug, election-name pattern and runoff threshold lives in that file, whose own `_contract` key documents which keys each strategy honours. A state that needs a knob nobody has needed yet gets that knob added to its adapter for everyone, never an `if state ==` special case.

Current coverage: **50 states configured across 27 strategies.** Some serve many states (`tabular` 15, `clarity` 3, `tally_enr` 2, `totalvote_enr` 2); many are a single state whose election authority genuinely is unlike anyone else's. Six states (MI, NV, NY, OH, OK, WI) have no usable per-state source yet and fall back to `google_civic`, a national source keyed on one fixed, publicly-known address per state — never a visitor's.

**The certified ballot beats primary results wherever a state publishes it.** Louisiana, South Carolina and Missouri sat on `google_civic` until 2026-09-26, and all three turned out to publish their November ballot directly: Louisiana's results portal stages the general election's candidate list (`voterportal`), South Carolina's candidate-tracking system lists it by office (`vrems`), and Missouri's Secretary of State certifies it to the counties as a PDF (`certified_pdf` — a list of names, so none of the name-to-vote misalignment that makes results PDFs unsafe). Reading the ballot rather than inferring it from vote totals matters beyond third parties: South Carolina's Lindsey Graham won the June Republican primary outright, then a special Senate primary and runoff nominated Darline Graham — a results reader stopping at June publishes the wrong nominee. All four ballot-list states (these three and South Dakota) carry `general_ballot_complete`.

What the remaining six have in common is not missing data but the door to it: their election sites answer server requests with a bot challenge (Cloudflare for NY, MI and WI; Incapsula for NV; Ohio's every SOS host returned a "maintenance" page), and Oklahoma's results API requires logging in with a credential embedded in its page script. Passing a challenge or using a credential not issued to us is not something this pipeline does. Where the same authority publishes a plain file on an unprotected path — Wisconsin's official canvass is one — that file is the way in.

**Matching a ballot to FEC records** surfaced two general bugs, fixed for every state: FEC files some surnames with the suffix attached (`CLEAVER II, EMANUEL`), which made a sitting congressman unmatchable against a ballot's "Emanuel Cleaver, II"; and FEC sometimes holds one person under two candidate ids (`BERRY, PAUL` twice in MO-1), which made the matcher refuse both and leave a nominee unconfirmed. Identical name and party inside one race is now treated as one person, confirming the record that raised money. A ballot candidate with no FEC filing at all still cannot be shown — the race list is built from FEC records — and that is a known gap: Louisiana's ballot names 41 federal candidates and 7 have no FEC record.

**A runoff can take a results source away.** South Dakota sat on `totalvote_enr` returning nothing for 116 days after its primary, and neither the adapter nor the vendor was broken: that vendor serves whatever election is CURRENT, and SD's 2026-07-28 runoff carried only Governor and Secretary of State, so the June primary's federal results left the view — with no archive or election picker to reach them. Montana and Nebraska run the identical strategy unaffected, because neither held a runoff. SD now reads `vip.sdsos.gov/candidatelist.aspx`, the Secretary of State's list of who is ON the November ballot, which answers `confirmed_general` directly instead of inferring it from vote counts. That also surfaces a general-only independent (Brian Bengs, US Senate) that no primary-results source can structurally see — which is why `normalize_party` gained a `ballot_list` mode: reading "Independent" as a party is wrong for primary results and right for a certified ballot.

Three rules do most of the work, all for the same reason — a wrong name here changes a vote:

- **Federal contests only, and the label must say so positively.** `parse_office` refuses anything not explicitly federal rather than guessing. Rhode Island is the sharpest case: its *state* legislature is named the "General Assembly", so `Senator in General Assembly District 5` sits on the same primary ballot as the real `Senator in Congress`, and 140 of that ballot's 192 contests are state seats that must not leak into a federal race.
- **A sub-threshold leader is not a nominee.** States that send a plurality leader to a runoff carry `runoff_threshold_pct`, and a contest whose leader is under it yields nothing rather than a guess — the same under-include-rather-than-fabricate rule applied throughout.
- **Certification is a signal, not a promise.** Where a vendor publishes an official/certified flag it is honoured, but `settle_days` sits underneath as a failsafe, because that flag is not reliably flipped (Utah's stayed false a month after its own signed canvass was published on the same portal).

What a visitor sees follows directly: a state with confirmed nominees renders a flat, confirmed list; a state without them falls back to FEC filers ranked by money raised, and the page says so rather than implying the ranking means anything about who will win.

**And the calendar decides whether that fallback is still honest.** Filers
are the right answer while a primary is ahead — nobody knows the ballot
yet. Once it has been held, the same list means the ballot *has* been
decided and this platform does not have it, which is a different claim
and a much worse one to leave unsaid. Measured across all 50 states on
2026-09-26: 39 had certified candidates, and eleven were still showing
filers after their own primary — Ohio by 144 days, Louisiana 133, New
York 95, with one New York race listing 25 filers for a ballot that holds
about two. The page had been saying those nominees "aren't confirmed
*yet*".

`_ballot_basis` (api/elections.py) now reports the WEAKEST basis across a
state's races together with whether its primary has passed, and the state
page leads with that rather than footnoting it — a state whose ballot is
certified says nothing at all, because a notice on every page is one
readers learn to skip. Of those eleven, South Dakota, Louisiana, South
Carolina and Missouri now read their certified ballots, and New Hampshire
unlocks on its own once its 21-day settle window passes.

### Finding your district without being asked where you live

Civitas never asks for an address. An address box lived on the state
ballot page until 2026-09 and was removed; a text filter replaced it,
which is better but still a chore — you have to know what to type, and
typing is the step people skip.

Counties are pickable instead, because a person knows theirs without
looking it up where almost nobody knows their district NUMBER. The index
is built from the rows already on the page (every race carries its county
list), so it needs no new data, no lookup service and no network call.
**13% of US counties span more than one district** (409 of 3,142) — those
offer the two or three as a second tap rather than sending the reader
elsewhere. The text filter stays for anyone who prefers it.

Where a county answers nothing — a county split between districts, or a
city holding several — a **map of the districts themselves** does. Every
multi-district state page draws its districts, shaded by the same PVI
rule as the district list (red R, blue D, paler = closer); hovering or
tabbing to one previews its race, and clicking narrows the page to it.
The outlines are the Census 119th-Congress cartographic boundary file,
split per state and vendored under `frontend/public/data/cd/` (all 50
states, 435 districts, ~207KB total; a page loads only its own state) by
`backend/scripts/build_district_topology.py`, which refuses to write
unless every state's district numbers match
`county_district_crosswalk.json` and none was lost to simplification.
At-large states get no map — one shape is nothing to choose between.
Regenerate after redistricting:

```bash
python backend/scripts/build_district_topology.py
```

### Race coverage: what counts as coverage

A story is attached to a race by candidate name, and a bare surname is never
treated as identifying — with thousands of FEC candidates the roster's surname
set covers a large fraction of common English surnames. A match therefore needs
the surname **plus** corroboration in the same text:

- `full_name` — the candidate's first name appears with it, as a name (up to two
  intervening tokens, so "Robert F. Kennedy" matches), and
- `surname_context` — the candidate's state name appears.

Only `full_name` items are eligible for the Bluesky posting path; the weaker
basis is display-only.

**The state-name corroboration goes vacuous on a state's own newsroom.** Adding
41 per-state outlets broke an assumption the rule had always relied on: the
Kentucky Lantern says "Kentucky" in virtually every article it publishes, so the
check fires on all of them and `surname_context` silently degenerates into the
bare-surname match it exists to prevent. Measured over the live corpus:

| | n | mean relevance | below 0.1 |
|---|---|---|---|
| national / `full_name` | 351 | 0.301 | 6% |
| national / `surname_context` | 112 | 0.258 | 14% |
| state / `full_name` | 30 | 0.295 | 3% |
| **state / `surname_context`** | **128** | **0.166** | **37%** |

`full_name` is indifferent to which feed an item came from; the weaker rule
degrades 2.6x. That 37% was the Williams sisters' doubles comeback on OH-9, a
Minnesota salmonella outbreak on SEN-MN, and a Kentucky man with a meat allergy
on KY-1. Those matches now have to clear the relevance bar `race_relevance`
derives by Otsu from the corpus — and only where the corroboration is
independently known to be vacuous. Gating the *whole* news feed on relevance was
measured too and rejected: it emptied 64 of 174 races.

They are **hidden, not deleted**. The article is still in the outlet's RSS feed,
so removing the row is exactly what stops `_already_ingested` from blocking it:
deleting them churned, re-ingesting and re-deleting the same items every 15
minutes.

**Syndication defeats a URL-keyed dedupe.** States Newsroom distributes one piece
to its whole network, so it arrives from twenty-odd outlets at twenty-odd
legitimate URLs. One race held 22 copies of a single headline. Deduping on the
headline keeps the outlet that ran it first.

**The open Bluesky name search is disabled.** Searching the whole network for a
candidate's name produced 7,740 of 8,239 stored coverage items — 94% — and the
content was not coverage. Four successive filters were built against it and each
failed in a different direction: source-type discarded real local newsrooms;
relevance admitted campaign material (maximally on-topic for a campaign);
no-advocacy still admitted mockery and a football post; and a DNS-verified
domain-handle rule does not catch a shitposter who owns a domain. A name mention
is not coverage, and four filters could not make it one. Widening the *sources*
(the 41 state newsrooms) is the fix that filtering was standing in for.

### Ballot measures

Statewide measures are shown at `/elections/states/<ST>` alongside that state's federal races. The rules the code enforces, all of which exist because this is the surface where an error can change a vote:

- **Everything is verbatim.** Official ballot title, official summary, fiscal statement and the state's own YES/NO descriptions are stored and rendered exactly as published, with the source linked. **No LLM touches ballot content at any stage**, and there is no plain-language rewrite — `grounding.py` verifies that tokens in generated text came from the source, not that a claim points the same *direction* as its source, so a summary that inverts a measure's meaning ("a YES vote repeals this" where approval *retains*) would pass every check we have. `ungrounded_electoral_claims` is additionally inert on this corpus: it only fires when "ballot"/"voters" are absent from the source.
- **Yes/no framing is lifted or left NULL**, never derived. The intuitive derivation inverts on a veto referendum.
- **Keyed on election date, not cycle year.** Ohio can run an "Issue 1" in a May primary and a different "Issue 1" in November.
- **Drafter attribution renders with each quote** (`title_authority` / `fiscal_authority`). Ballot titles are litigated precisely because they are contested, so naming the author is more neutral than the bare quote.
- **Removed measures render as removed** for a 45-day grace window rather than disappearing, and a sync returning implausibly fewer measures than are on file is treated as a bad response, not as news.
- **"No measures" and "not ingested" are different states.** `MeasureCoverage` carries `covered` / `confirmed_none` / `not_yet_covered` / `ingest_failed` per state per election, because an empty section on a state's ballot page reads as "nothing to research".
- **Scope is stated on the page, and the statement shrinks.** A ballot is defined per *ballot style*, not per state, so state legislative seats, county/municipal offices, judicial questions and local measures are enumerated as omissions above the content — and each entry is removed as that gap actually closes, rather than standing as permanent boilerplate (statewide executive offices came off the list this way; see below), with a link to the voter's own election office. Per-state official links are only shown after an automated check confirms they resolve; otherwise the page falls back to the USAGov directory.

`VOTESMART_API_KEY` is optional — without it the phase is skipped and every state reports `not_yet_covered` rather than rendering an empty section.

An optional town selector on the same page surfaces LOCAL races (city council, school board, local measures) that a statewide page structurally can't show — a real ballot is defined per precinct, not per state, and the only address-keyed source (Google Civic's `voterInfoQuery`) requires an address. Rather than a visitor's own address, which this platform's architecture exists to never send off-box, it's called with a fixed, publicly-known representative address per town (town hall) from a small hand-curated pilot list (`backend/app/data/town_directory.json`) — every visitor who picks the same town sends the identical request, so nothing visitor-specific ever leaves the server. This is a real approximation, not a precinct-accurate lookup (a town can contain more than one precinct), and the page says so next to the selector. Gated on `GOOGLE_CIVIC_API_KEY`; unset means the selector doesn't appear and the statewide page above is unaffected.

### Statewide executive offices

Governor, Lieutenant Governor, Attorney General, Secretary of State and Treasurer are confirmed through the same adapters, the same config file and the same `_contract` described above — no new source, no new vendor, no new key. A state that publishes its primary results publishes these contests **in the same response** as the federal ones; they were simply being parsed and discarded.

- **Two gates, opposite directions.** The federal-only rule above still holds — `parse_office` must never read a state legislative seat as federal. Its new sibling `parse_statewide_office` must never read a county or municipal office as statewide. Against Rhode Island's real 192-contest primary the second finds exactly 9 and refuses all 177 others (133 General Assembly seats, 21 party committees, 23 local offices). A joint "Governor and Lieutenant Governor" ticket resolves to the top of the ticket.
- **Stored as `StatewideNominee`, not `Candidate`.** A state office has no FEC filing, no finance totals and no Representation Score, so forcing one into the federal table means a row whose every financial field is permanently null. For the same reason the name is kept whole rather than reduced to a surname — there is no FEC row to match it to. Ballot annotations still come off (Rhode Island marks party-endorsed candidates with a bare asterisk).
- **`statewide_offices` is a truth condition, not a feature toggle.** Every adapter returns only what its own state's feed contains, so a state nobody has checked returns zero executive contests — indistinguishable from a state that genuinely elects none. The flag says that state's real contest labels were checked against the parser, which is what makes a count of zero a claim rather than an admission.
- **`omits` shrinks as the gaps close.** The page's "Governor and other statewide executive contests" omission is dropped for any state actually covered. A disclaimer list that keeps disclaiming what the page now shows stops describing the page and becomes boilerplate a reader learns to skip — past the entries that are still true.

### State legislative seats

The same feeds carry the state's own legislature — 133 of the 192 contests on Rhode Island's real 2026 primary ballot are General Assembly seats — so these are read by a third parser gate beside the federal and executive ones, and stored as `StateLegNominee` under the same `statewide_offices` opt-in.

The hard part was not the data. It was **how a reader finds their seat without being asked where they live** (see core design principle 8). A U.S. House row can name its counties; a state legislative district is far smaller than a county, so the useful unit is the town — and no ready-made town-to-district list exists at the right vintage:

- **The results feed** reports a legislative contest as a single reporting unit. No locality breakdown.
- **Census Block Assignment Files** are ideal in shape and join cleanly (Rhode Island: exactly 75 lower and 38 upper districts), but the newest was published February 2021, so its boundaries predate nearly every state's 2021–22 redistricting.
- **A plain spatial `intersects` query** is hopelessly over-broad, because a district that merely *touches* a town counts: Jamestown (~5,500 people, comfortably inside one ~14,000-person district) came back as 7 districts.

`scripts/fetch_state_leg_crosswalk.py` instead takes the **current** district polygons from Census TIGERweb, lays a grid over each town, and asks which district contains each interior sample point. There is no sliver problem in that formulation — districts tile the state, so every sample is inside exactly one district and every count is a real overlap measure. A scanline makes it tractable (~12 seconds per state rather than billions of edge tests).

Validated against the independent block-assignment answer: 97 of 113 Rhode Island districts match exactly, 7 more are safely over-inclusive, and **all 16 differences were confirmed to be genuine redistricting** — each re-measured at 4× resolution and found to have exactly 0.000% overlap under the current map. Nothing is under-inclusive, which is the direction that would hide a reader's race.

The result is that typing "jamestown" returns House 74 and Senate 13, and nothing else, without a visitor ever entering an address.

Coverage is not limited to one vendor: the bulk `tabular` exports carry the same contests, so a state there is a config opt-in too. Minnesota's real 2026 export resolves 18 federal, 8 statewide (including its joint `Governor & Lt Governor` ticket) and every one of the 32 legislative contests the file contains — while refusing `County Attorney` and `County Auditor/Treasurer`, which contain the exact words the statewide gate keys on. Note that some `tabular` entries deliberately fetch a federal-only export (New Mexico's URL carries `type=FED`), so opting such a state in means widening its discovery URL first.

Georgia is the third state live, and the most complete: 30 federal races, all 236 legislative seats (exactly its 180 House + 56 Senate), and every one of its statewide contests — the eight constitutional offices plus both Public Service Commission seats, which are elected statewide but *held by district*, so each is its own row.

Getting Georgia right needed two things the first two states didn't. Its county subdivisions are Census County Divisions named `Fairburn-Union City CCD` — names on no sign and in no address — so place-based states use incorporated places instead, chosen by Census's own "strong MCD" distinction. And three of its House districts lie entirely in unincorporated land with no place at all, so those fall back to their county; the county suffix is kept there, because Georgia has both a Forsyth County and a Forsyth city and the list would otherwise show the same word for two different places.

Idaho and Washington are the fourth and fifth, and they needed a distinction the first three didn't. Both elect **two representatives from one district** — Idaho as `Seat A`/`Seat B`, Washington as `Pos. 1`/`Pos. 2` — so the seat, not the district, is what tells their contests apart. Census settles which is which: it draws 134 lower-chamber polygons for Minnesota (`10A`, `10B`, …) but only 35 for Idaho and 49 for Washington. So Minnesota's letter belongs to the *district* and Idaho's to the *seat*, and the two seats of an Idaho district correctly share one town list. Read as one district instead, half of each state's House would simply have vanished.

North Carolina is the sixth, and it exposed a gap that had been silently halving it: its lower chamber is labelled `NC HOUSE OF REPRESENTATIVES DISTRICT 1`, with no "State" anywhere, so only its Senate was matching. A **bare** "House of Representatives" is exactly what the federal gate must always refuse — it is a state chamber's name in most of the country — and that is what makes it safe to claim as a state seat here, since the legislative gate refuses anything the federal one recognises before testing a pattern at all.

California, Florida and Utah bring it to nine. Each was one vocabulary gap from honest coverage: California's entire **Assembly** was unmatched (`State Assembly Member District N`), as were its bare `Treasurer` and `Controller` — it prints them with no "State" prefix at all — so it looked like a 20-seat legislature rather than the 100 seats actually up. Florida was missing `Chief Financial Officer`, its fourth cabinet officer; Utah its `State Board of Education`, seated by district like Georgia's PSC.

California is also the first **top-two** state covered, where two candidates of the *same party* legitimately advance from one contest — its Board of Equalization District 2 sends two Democrats to November. That only survives because a nominee's name is part of the storage key.

Maryland makes ten, and brought the **third** multi-member shape. Not Idaho's `Seat A`/`Seat B`, nor Washington's `Pos. 1`/`Pos. 2`, but `House of Delegates District 10 … Vote for up to 3` — three members from *one* contest with no seat designator, so the number advancing is a property of the contest and is read from its own label. That is applied only where a state runs one-nominee party primaries: under top-two exactly two advance no matter how many seats are filled, so a seat count there would answer the wrong question. Its District 3 correctly shows three Democratic and two Republican nominees.

Nebraska is the eleventh, and the first on a different vendor — the TotalVote platform, which mixes state offices onto the same pages as the federal races. It needed one addition: `Auditor of Public Accounts`, Nebraska's own name for the office and the last of its five statewide contests. Its Legislature is unicameral **and officially non-partisan**, so no legislative contests reach the gates at all and its page correctly keeps the "State legislative districts" omission — five omissions rather than four, which is the honest count.

Montana and South Dakota run on that same vendor and are deliberately **not** covered. South Dakota is the instructive one: its page yields exactly one contest, Governor, with nothing unmatched — so coverage would *look* complete while silently omitting the Attorney General, Secretary of State, Auditor and Treasurer it also elects. A clean unmatched list proves nothing when the page itself is scoped.

Colorado, Iowa and West Virginia complete the Clarity vendor, bringing it to fourteen states. Each was blocked by exactly one wording: Colorado's `Regent of the University of Colorado — Congressional District N`, a statewide body seated by *congressional* district; Iowa's `Secretary of Agriculture` and `Auditor of State`, its own names for offices already known under others; and West Virginia's `HOUSE OF DELEGATES, 1st District`, which puts the number **before** the word — so the `District N` pattern read nothing and all 117 of its seats were invisible.

Arkansas and North Dakota make sixteen. Their vendor pre-filtered contests by type — Arkansas to `Federal`, North Dakota's results to `SW` — which discarded every state contest before a gate could see one. Those filters exist only to narrow the federal case cheaply, so they are dropped for a state reading its own offices; the unscoped fetch was measured first at 1.9 MB in under a second. North Dakota also states a contest's seat count as a **structured field** (`voteFor: 2`) rather than in the label, which is how its two-members-per-district House reads correctly: District 1 nominates two Democrats and two Republicans.

Arkansas exposed something the earlier states had hidden. It publishes only two of its seven statewide offices, because the other five were unopposed and its feed does not itemise them — so the statewide section now carries the same caveat the legislative one always had: only offices the state's own feed names appear, and an uncontested primary is often not published at all.

Minnesota is also why a district identifier is a **string**: it splits each of its 67 senate districts into two house districts, `10A` and `10B`. And why the town list is truncated for display but searched in full — one of its senate districts covers 292 townships, so the row shows three and "& 133 more", yet typing the 136th town still finds the seat.

---

## Bluesky Integration

The Civitas Bluesky account (`@civitas-research.org`) is updated automatically by the hourly pipeline:

| Post type | Trigger | Content |
|-----------|---------|---------|
| **Issue post** | New topic enters action center, or existing topic gets articles with a newer date | Verbatim claims extracted from the sources and rendered by `post_composer` — the model locates spans, it does not write the sentence. If the event predates today, the post opens with "Yesterday: …" or "On [date]: …" |
| **Senator spotlight** | Once per day (random pick from those not yet spotlighted, cycling through all before repeating) | LLM-written score highlight with data from Civitas scorecard |
| **Weekly summary** | Once per week (6-day cooldown) | LLM-written condensed week-in-review from the timeline pipeline |
| **Repost + like** | Outlet post matches an active issue (cosine sim ≥ 0.78) | Reposts + likes posts from AP News, NPR, and PBS NewsHour (`NEWS_OUTLET_HANDLES` — a narrower list than the RSS feed set, since it needs a Bluesky presence); posts under 24h old; max 3 per hourly run |

The spotlight pick is deliberately *not* the highest or lowest scorer. Always picking an extreme, combined with framing it as praise or criticism, produced a real incident: a "praise" post about a senator's score read as badly out of touch after negative news broke about him the same day. A random pick with unevaluative framing can't fail that way.

The spotlight and the weekly summary read senator scores and the timeline, not the news, so they run on every refresh — including the two paths that abort early because no articles arrived or none were policy-relevant. That makes a missing spotlight a usable signal in its own right: if it hasn't posted, the pipeline isn't completing, and the quiet isn't a slow news day.

Each issue links back to its permanent Civitas permalink (`/issue/<id>`). The permalink is stable — issue IDs never change even as content is updated, and any issue that has ever been published is retained indefinitely. The 14-day cleanup of old issues keys on `bsky_last_post_text` (only ever written on a successful publish) rather than `bsky_posted_at`, which the repost path clears and so does not mean "never published".

---

## Scoring

### Non-Partisan by Construction

The scoring formulas deliberately avoid any ground truth that encodes partisan assumptions. Every dimension measures *structural behavior* against a neutral baseline, not agreement or disagreement with any policy position.

```
Overall = 0.33 × FundingIndependence
        + 0.33 × ConstituentAlignment
        + 0.34 × LegislativeEffectiveness
```

The weights live in `SCORE_WEIGHTS` (`backend/app/config_definitions.py`) and are served at `GET /api/config` — that dict, not this file, is authoritative. Two former dimensions are still computed, stored, and displayed but excluded from the weighted overall: **Promise Persistence** (removed in v6.0 — a live measurement found 0 of 100 senators reached even "medium" evidence confidence, so the dimension had collapsed to its neutral prior) and **Funding Diversity** (folded into Funding Independence in v6.5 after an audit found the two correlated at r=0.72 — the same underlying funding-profile signal measured twice).

The exact formulas are actively iterated (v1 → v6.13 as of this writing, each change measured against real data) and are documented in full — every component's weight, calibration source, and academic citation — in the module docstring of `backend/app/pipeline/analyze/score_calculator.py`, which is the source of truth for the current rule; why each version changed is in `docs/methodology/` (one decision record per version). Rather than duplicate formulas here that will drift out of sync as the algorithm evolves (as this section previously did), a summary:

- **Funding Independence**: PAC dependency (share scaled by how close contributing PACs run to their legal caps, chamber-specific multiplier), state-relative small-donor share, relative top-donor concentration, and inverse-HHI industry concentration (folded in from the former Funding Diversity dimension). v6.13 removed outside spending and source breadth after testing both against FEC data: outside spending tracks race competitiveness, and breadth was the small-donor share counted again (see `docs/research/funding-independence.md`).
- **Constituent Alignment** (keyed `constituentAlignment`; it was `independentVoting` until 2026-09, and the public API still emits that key as a deprecated alias — the dimension was rebuilt in v4.2): how a member's voting compares to what their *seat* elected them to do. Party-line voting in a safe seat that elected that platform scores as representation, not as a failure of independence — the delegate model of representation (Miller & Stokes 1963), not independence as an intrinsic virtue. The member's break rate is scored against the break rate same-party members show at the same seat lean, measured from the chamber every run (`compute_constituent_reference`); the member's Nokken-Poole roll-call position (Voteview) is scored against a seat-conditional per-party expectation (Canes-Wrone, Brady & Cogan 2002's district-relative extremity). Both are symmetric and apply the same way in safe and competitive seats — v6.13 decided each of those choices by testing them against House re-election results (see `docs/research/constituent-alignment.md`). Ideal points are ingested automatically every pipeline run (`app/pipeline/fetch/voteview.py`, ingestion-gated) — no manual step.
- **Legislative Effectiveness**: significance-weighted, cumulative-stage bill credit (Volden & Wiseman 2014-based, benchmarked against the sponsor's chamber and majority/minority baseline, not an absolute threshold — the chamber's reference is re-measured from its current members every pipeline run), cosponsorship-network leadership (PageRank, tenure-confidence-scaled), and bipartisan coalition attraction (v6.11, moved from Constituent Alignment — the receive-only share of cross-party cosponsors a member attracts to their own bills, the construct Harbridge-Yong, Volden & Wiseman 2023 show predicts lawmaking success).

### Senate & House Scores

Each senator and House representative carries five sub-scores (0-100, higher = better); the three in `SCORE_WEIGHTS` are weighted into the overall, the other two are informational:

| Metric | Weight | What It Measures | Key Reference |
|--------|--------|------------------|---------------|
| **Funding Independence** | 33% | PAC dependency + small-donor share + top-donor concentration + industry concentration | Stratmann 2005; Parmigiani 2025 |
| **Constituent Alignment** | 33% | Break rate vs. same-party members in same-lean seats + roll-call position congruence (Nokken-Poole vs. seat-conditional norm) | Carson et al. 2010; Canes-Wrone, Brady & Cogan 2002; Nokken & Poole 2004 |
| **Legislative Effectiveness** | 34% | Significance-weighted stage credit (majority-status-benchmarked) + cosponsorship leadership (PageRank) + bipartisan coalition attraction | Volden & Wiseman 2014; Harbridge-Yong, Volden & Wiseman 2023 |
| Promise Persistence | unweighted (v6.0) | Campaign commitments kept vs. broken + vote participation | Naurin 2011; Martin 2011 |
| Funding Diversity | unweighted (v6.5, folded into FI) | Donor traceability + industry diversity (inverse HHI) | Rhoades 1993; Parmigiani 2025 |

House representatives use the same scoring framework, data sources, and classification pipeline as senators, ensuring comparable scores across chambers (with chamber-specific calibration constants where the chambers' real baselines genuinely differ — PAC-ratio multiplier and effectiveness baselines). One sourcing difference: senators' campaign commitments come from scraped senate.gov platform text (LLM-extracted, heuristic fallback), while representatives' positions are derived from the bills they sponsor and evaluated against their floor votes with embeddings only — the House pipeline makes no LLM calls for promise analysis.

Score history is tracked in `ScoreSnapshot` records so the frontend can render historical score trends per senator/representative.

Additional senator metrics (informational, not scored):

| Metric | What It Measures | Technique |
|--------|------------------|-----------|
| **Leadership Score** | Legislative influence — how many peers cosponsor this senator's bills | PageRank on cosponsorship graph (Brin & Page 1998) |
| **Ideology Score** | Behavioral ideological position derived from cosponsorship patterns | SVD on cosponsorship matrix (Tauberer 2012) |
| **Partisan Depth** | How deeply aligned with their party across policy areas | Content-based voting analysis with SVD ideology as a prior (linear blend) |

### Supreme Court Justice Scores

Each justice is scored on impartiality and ideological consistency based on case-level voting data from the Oyez Project and Supreme Court APIs.

### Presidential Scores

Presidents are scored on four dimensions — Public Mandate (21.67%), Effectiveness (21.67%), Agency Alignment (21.67%), and Historical Legacy (35%) — using a mix of live API data (BLS employment, BEA/FRED GDP, Federal Register rulemaking) and historical records (C-SPAN Historians Survey, UCSB American Presidency Project approval and election margins, MeasuringWorth GDP). Independence, Follow-Through, and Competence were removed in 2026-07 rather than left as hand-set values with no live formula behind them; a president with no data source for a dimension shows N/A and the overall score renormalizes over whichever dimensions actually apply. See the [scoring changelog](/changelog) for the full account.

All scores default to 50 when data is insufficient. No LLM input is used in score calculation — formulas are deterministic and auditable.

---

## Hybrid Search (Explore)

The Explore feature searches primary-source government documents. It is not a
single similarity score: general web search has never been one, and neither is
this. Four independent rankers are combined — two retrieval channels and two
query-independent priors.

**What is indexed:** Senate and House floor speeches, presidential actions
(executive orders, proclamations, memoranda), Supreme Court opinions, and
Federal Register rulemaking documents — five source types, not bill text.
Every document feeds three structures, all rebuilt from the
`explore_documents` table at the end of each ingest run:

| Structure | Where | What it holds |
|---|---|---|
| `vec_explore` | `/data/vectors.db` (sqlite-vec) | One 384-dim embedding per document (no chunking) over `title + summary + body[:800 chars]`, with doc type / chamber / politician as filterable metadata |
| `explore_fts` | app DB (SQLite FTS5) | A BM25F inverted index over title, summary and body. External-content, so the text is not duplicated; triggers keep it live between runs |
| `authority` / `cited_by_count` | `explore_documents` columns | PageRank over the citation graph between these documents |

**Query flow:**
```
User query
    │
    ├─▶ semantic  — embed (all-MiniLM-L6-v2) → sqlite-vec KNN, cosine
    │
    └─▶ keyword   — parse to a safe FTS5 MATCH → BM25F, title-weighted
              │
              ▼  filters (doc type / chamber / politician / open-for-comment)
              │   pushed into BOTH channels, not applied afterwards
              ▼
        weighted reciprocal rank fusion over four rankers:
        semantic · keyword · freshness · citation authority
              │
              ▼  collapse near-duplicate documents
              ▼  cap results per member/agency (host crowding)
              │
              ▼ returned with a keyword-in-context excerpt (matched terms
                marked), source URL, doc type, citation count, and — for
                open rulemakings — a comment link and deadline
```

**Why two retrieval channels.** A 384-dim bi-encoder embeds "Executive Order
14110" and "Executive Order 13985" to almost the same point: the number
carries the meaning and the model never saw it. The same failure covers docket
numbers, RINs, agency acronyms, statutory citations, and member surnames —
exactly the queries where the user knows precisely what they want. Classical
inverted-index retrieval is best at those and weakest where the embedding is
strong (paraphrase, synonymy, topical queries). Running both and fusing them
is why this is a hybrid engine rather than a bigger embedding model.

**Why rank fusion rather than score blending.** Cosine distance and BM25 live
on unrelated scales; min-max normalising each makes the blend depend on
whatever the best and worst scores happened to be for that one query.
Reciprocal rank fusion (Cormack, Clarke & Büttcher 2009) discards the scores
and fuses the rankings: `score(d) = Σ w_r / (K + rank_r(d))`, K = 60. A ranker
that didn't return a document contributes nothing for it.

**Citation authority is the PageRank analogue.** Federal documents cite each
other constantly and by canonical identifier — executive order numbers,
"volume FR page" citations, RINs. Those formats are published in the Office of
the Federal Register's Document Drafting Handbook, so they are parsed, not
classified. An executive order agencies keep invoking a decade later is doing
the same job as a heavily-linked web page. A document only enters the
authority ranking if the corpus actually cites it: citability is unevenly
distributed by document type (a rule carries an FR citation, a floor speech
carries nothing), so the prior can lift a well-cited document and can never
push down one that had no way to earn the signal. On a corpus with no
cross-references the ranking is empty and the prior does nothing.

**Ranking weights are measured, not asserted.** They live in
`config_definitions.py` under "Explore search ranking".
`backend/scripts/evaluate_explore_search.py` measures MRR and Recall@k for
semantic, keyword and hybrid separately, using known-item retrieval over four
query styles (title, paraphrase, identifier, rare-term) with relevance
judgments derived from the corpus rather than hand-labelled. Change a weight,
re-run it.

Bill text itself is not indexed here; it's used separately, title-only, for
the tier-3 kNN bill-classification step in the scoring pipeline (see Phase 3
above).

### Two embedding models

The platform loads **two** sentence-transformers, both 384-dim and both in the
~22M size class, split by what they are good at:

| Model | Used for | Why |
|---|---|---|
| `Snowflake/snowflake-arctic-embed-xs` (primary) | Classification and the learning store — bill policy areas, donor/industry kNN, party alignment, the numpy batch-similarity paths | The classification thresholds were measured and calibrated against this model's geometry |
| `sentence-transformers/all-MiniLM-L6-v2` (index) | The semantic-search index, plus the Action Center's similarity gates (policy filter, trending mask, explore re-rank, title dedup) | Arctic is retrieval-*asymmetric*: it packs same-register text into a narrow ~0.55–0.87 raw-cosine band, which left several similarity thresholds unable to separate real matches from noise. MiniLM measured ~4x the separation margin on explore-doc anchoring and ~3x on policy relevance against this platform's own live failure cases |

They coexist deliberately rather than as a migration left half-finished:
swapping a classification gate to a different embedding space without
re-measuring its threshold is how thresholds go vacuous, so the classification
subsystem stays on the primary model until its own recalibration pass. Both are
~22M parameters, so carrying two costs little on the Pi.

The index records which model built it (in `vectors.db`'s `meta` table). On a
mismatch at startup the vec tables are dropped and a background reindex runs
from the `ExploreDocument` rows already in the app database; search returns
"index not ready" until it finishes.

---

## Score Data Transparency

Each score shown in the UI links to a "data basis" view that surfaces the raw data behind the number. This is built without any additional data storage — the existing tables are queried at render time:

| Score | Data shown |
|-------|------------|
| Funding Independence | Top 10 donors by amount, PAC fraction |
| Promise Persistence | Per-promise verdict (kept/broken/partial), supporting vote or speech |
| Constituent Alignment | Key votes where member broke with party, bill title + vote |
| Funding Diversity | Industry breakdown pie chart from `IndustryDonation` records |
| Legislative Effectiveness | Sponsored bills, cosponsor counts, committee/floor passage rate |

The score transparency layer deliberately surfaces the underlying `KeyVote`, `Donor`, `CampaignPromise`, and `SponsoredBill` records rather than LLM-written explanations — the numbers are auditable against the source data.

---

## Public API

A rate-limited public read-only API is available without authentication at `/api/public/v1`:

```
GET /api/public/v1/senators                      All senators with scores
GET /api/public/v1/senators/{id}                 Single senator
GET /api/public/v1/senators/{id}/history         Score history over time
GET /api/public/v1/representatives               All representatives with scores
GET /api/public/v1/representatives/{id}          Single representative
GET /api/public/v1/representatives/{id}/history  Score history over time
GET /api/public/v1/states                        State metadata
GET /api/public/v1/search                        Semantic search over bills, lobbying
                                                 records, and federal-register documents
                                                 (not politician names)
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
| Linear shrinkage toward 50 for promise scores (fixed rate `min(n/k, 1)`, not empirical Bayes) | Prevents inflation when few promises are evaluable | Stein-type shrinkage idea: Efron & Morris 1975 |
| Cook PVI adjustment for independence | Raw party-break rates mislead without constituency context | Carson et al. 2010 |
| Donation-vote correlation ≠ causation | Methodological caution in interpreting funding influence | Ansolabehere et al. 2003 |
| Fuzzy name matching for self-funded detection | SequenceMatcher handles spelling variations, middle names | Ratcliff & Obershelp 1988 |
| PageRank for legislative leadership | Cosponsorship network centrality measures peer influence | Brin & Page 1998; Tauberer 2012 |
| SVD ideology as a prior for partisan depth (linear blend) | Behavioral ideological signal regularizes sparse vote data | Poole & Rosenthal 1985; Efron & Morris 1975 |

See the [Methodology page](/about) for full details and inline citations.

---

## Prerequisites

- **Docker** and **Docker Compose** (v2)
- A free **api.data.gov** API key — sign up at https://api.data.gov/signup/
- ~16 GB RAM recommended
- ~10 GB disk for Docker images and model weights
- A `.gguf` model file for llama-server (see LLM Backend Options below)

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

## Deployment

### Docker Swarm Architecture

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
          backend (task) ──────┐                    frontend (task)
    start-first rolling update │              start-first rolling update
                                ▼
                        llama-server (task)
                     stop-first rolling update
```

`docker stack deploy -c docker-compose.yml -c docker-compose.swarm.yml
civitas` follows this sequence, all built into Swarm rather than scripted:
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
`/data` inside the backend container. The SQLite databases (`civitas.db`,
`vectors.db`) and model-version marker
live here and survive image rebuilds and stack redeploys — the volume is
`external: true` in `docker-compose.swarm.yml`, so `docker stack deploy`
never recreates or touches it directly.

### Service Layout

```
Host ports    Swarm service   Purpose
───────────────────────────────────────────────────────────────────────────────
8081          nginx           Reverse proxy + caching (the only service
                              published to the host — port-forwarded
                              externally, don't change without updating
                              that forwarding rule)
—             backend         FastAPI backend (overlay-network only)
—             frontend        Next.js frontend (overlay-network only)
—             llama-server    llama.cpp inference (overlay-network only)
```

Backend, frontend, and llama-server publish **no host port** — Swarm's
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
DNS). If llama-server is unavailable, all LLM calls fall through to a
timeout error and the pipeline records a per-member failure without
aborting the run.

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

Swarm considers a task healthy purely by its Docker `HEALTHCHECK` exit code
(`curl -sf http://localhost:8000/api/health` for the backend) — the same
"HTTP 200, don't parse the body" criterion the old deploy script used.
`database`/`ollama` in the response body are informational for the admin
dashboard and don't gate the rolling update. The `ollama` key name is
historical (kept for API/dashboard compatibility even though Ollama isn't
bundled anymore): it reports whichever backend `LLM_BACKEND` selects, so
under the default it is the llama.cpp server's health, not Ollama's.

## Development Setup

```bash
# Start deps, then run backend and frontend with live reloading
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

### Running Backend Tests

```bash
docker compose run --rm --no-deps backend python -m pytest tests/ -v

# Fast tests only (skips the tests that load the sentence-transformer model)
docker compose run --rm --no-deps backend python -m pytest tests/ -v -m "not slow"

# Only the embedding-model tests
docker compose run --rm --no-deps backend python -m pytest tests/ -v -m slow
```

CI runs the fast suite behind a 46% total-coverage floor plus a 60%
changed-lines gate (`diff-cover`), and runs the `slow` suite on pushes to
`main`. See `.github/workflows/ci.yml`.

### Running Frontend Tests

```bash
cd frontend
npm run lint     # eslint, including jsx-a11y
npm test         # vitest
npm run build    # type errors block CI
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
│   │   │   │   ├── cross_reference.py        # Per-senator lobbying/key-vote analysis
│   │   │   │   ├── action_center.py          # News clustering, LLM summarization,
│   │   │   │   │                             #   national monitors, timeline
│   │   │   │   ├── score_calculator.py       # Deterministic scoring formulas
│   │   │   │   ├── ollama_client.py          # LLM backend abstraction
│   │   │   │   ├── bluesky_poster.py         # Post new/updated action issues
│   │   │   │   ├── bluesky_spotlight.py      # Daily senator spotlight + weekly summary
│   │   │   │   ├── bluesky_engagement.py     # Repost/like matching outlet posts
│   │   │   │   └── bluesky_utils.py          # Shared link-card builder
│   │   │   ├── assemble/     # Senator scorecard builder + validator
│   │   │   ├── vector_store.py  # sqlite-vec + both embedding models
│   │   │   ├── senate_pipeline.py, house_pipeline.py  # FETCH -> TRANSFORM ->
│   │   │   │                 #   ANALYZE -> ASSEMBLE+SAVE orchestration per chamber
│   │   │   ├── stock_pipeline.py  # STOCK Act trade-disclosure ingestion (sibling
│   │   │   │                 #   phase, runs after the member pipelines)
│   │   │   └── president_pipeline.py, justice_pipeline.py, explore_pipeline.py
│   │   ├── scheduler.py      # Nightly cron entrypoint — calls the pipelines above
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
│   │   ├── politicians/      # Unified directory + per-member profile
│   │   │   └── [id]/         #   (senator, representative, president, and justice
│   │   │                     #    scorecards all render into this one page)
│   │   ├── bills/            # Bills-in-motion — grouped by stage, sortable
│   │   ├── compare/          # Side-by-side senator/representative comparison
│   │   ├── explore/          # Semantic search over government documents
│   │   ├── elections/        # State index, per-state ballot pages (federal contests
│   │   │                     #   + statewide measures), race/candidate detail
│   │   ├── leaderboard/      # Rankings across all branches (House paginated)
│   │   ├── environmental/    # Environmental policy tracking
│   │   ├── accessibility/    # Accessibility statement
│   │   ├── feedback/         # On-site feedback form -> files a GitHub issue
│   │   ├── changelog/        # Scoring-algorithm version history
│   │   └── admin/            # Pipeline control panel with granular progress
│   ├── src/components/       # React components
│   └── Dockerfile
├── check-and-deploy.sh       # Cron poller: builds images + docker stack deploy
├── docker-compose.yml
├── docker-compose.swarm.yml  # Swarm-only overlay (production stack deploy)
├── nginx/                    # Dockerfile + static config (in-stack reverse proxy)
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
| `LLAMA_SERVER_URL` | No | llama-server URL (default: `http://llama-server:8070`, the in-stack service) |
| `LLAMA_MODELS_DIR` | No | Host directory bind-mounted into llama-server at `/models` (default: `./llama-models`) |
| `LLAMA_MODEL_FILE` | No | `.gguf` filename inside `LLAMA_MODELS_DIR` (default: `lfm2.5-1.2b-instruct-q4_k_m.gguf`) |
| `OLLAMA_MODEL` | No | Model name for cache keys and Ollama (default: `LiquidAI/lfm2.5-1.2b-instruct`) |
| `DATABASE_URL` | No | SQLite path (default: `sqlite:///data/civitas.db`) |
| `PIPELINE_CRON_SCHEDULE` | No | Cron schedule for nightly pipeline (default: `0 3 * * *`) |
| `PIPELINE_CACHE_TTL_HOURS` | No | API response cache TTL (default: `72`) |
| `VOTESMART_API_KEY` | No | Vote Smart API key — enables statewide ballot measures on the state ballot pages. Unset means the sync is skipped and every state reports "not yet covered", never "no measures" |
| `GOOGLE_CIVIC_API_KEY` | No | Google Civic Information API key — enables the optional town-level local-races selector on the state ballot pages (a small hand-curated pilot list, not all US towns). Unset means the selector doesn't appear |
| `BSKY_HANDLE` | No | Bluesky handle (e.g. `civitas-research.org`) |
| `BSKY_APP_PASSWORD` | No | Bluesky app password (from Settings → App Passwords) |
| `FEEDBACK_TOKEN` | No | Fine-grained GitHub PAT (Issues: write only) for the on-site feedback form; leave unset to disable it (returns 503, never silently drops) |
| `GITHUB_FEEDBACK_REPO` | No | Repo the feedback form files issues against (default: `kamoras/civitas`) |

## Contributing

Contributions that improve data accuracy, scoring methodology, or public
usability are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the
CI gates, and what a scoring-methodology change needs to argue.

- [Code of Conduct](CODE_OF_CONDUCT.md) — Contributor Covenant 2.1, plus a
  section on political subject matter specific to a project that scores named
  politicians
- [Security policy](SECURITY.md) — please report vulnerabilities privately
  rather than in a public issue
- [Architecture diagrams](docs/diagrams/) — rendered Mermaid views of the
  pipeline, scoring, schema, and deployment

## References

Full methodology with inline citations is available on the [About page](/about).
Key references:

- Bonica, A. (2014). Mapping the Ideological Marketplace. *AJPS*, 58(2), 367-386.
- Budge, I. et al. (2001). *Mapping Policy Preferences*. Oxford UP.
- Brin, S. & Page, L. (1998). The Anatomy of a Large-Scale Hypertextual Web Search Engine. *Proc. WWW 1998*.
- Canes-Wrone, B., Brady, D. & Cogan, J. (2002). Out of Step, Out of Office. *APSR*, 96(1), 127-140.
- Carson, J. et al. (2010). The Electoral Costs of Party Loyalty. *AJPS*, 54(3), 598-616.
- Clinton, J., Jackman, S. & Rivers, D. (2004). The Statistical Analysis of Roll Call Data. *APSR*, 98(2), 355-370.
- Cover, T. & Hart, P. (1967). Nearest Neighbor Pattern Classification. *IEEE Trans. Info Theory*, 13(1), 21-27.
- Efron, B. & Morris, C. (1975). Data Analysis Using Stein's Estimator. *JASA*, 70(350), 311-319.
- Grimmer, J. & Stewart, B. (2013). Text as Data. *Political Analysis*, 21(3), 267-297.
- Laver, M., Benoit, K. & Garry, J. (2003). Extracting Policy Positions from Political Texts. *APSR*, 97(2).
- Lewis, P. et al. (2020). Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks. *NeurIPS 2020*.
- Nokken, T. & Poole, K. (2004). Congressional Party Defection in American History. *Legislative Studies Quarterly*, 29(4), 545-568.
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
