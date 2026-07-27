# Data model

42 tables in one SQLite file (`/data/civitas.db`), plus a separate sqlite-vec
file (`/data/vectors.db`) holding the `vec_explore` and `vec_bills` vector
tables — split out for writer-lock isolation, not just tidiness. Shown here in clusters; infrastructure tables are listed rather
than drawn.

## Members of Congress

Senators and representatives have **parallel, mirrored table sets**. The
`Representative` side duplicates the `Senator` side rather than sharing a
polymorphic parent — separate tables, identical `score_*` column names, so one
weight map covers both entity types.

```mermaid
erDiagram
    SENATORS ||--o{ DONORS : "senator_id"
    SENATORS ||--o{ INDUSTRY_DONATIONS : "senator_id"
    SENATORS ||--o{ KEY_VOTES : "senator_id"
    SENATORS ||--o{ LOBBYING_MATCHES : "senator_id"
    SENATORS ||--o{ CAMPAIGN_PROMISES : "senator_id"
    SENATORS ||--o{ SPONSORED_BILLS : "senator_id"
    SENATORS ||--o{ STOCK_TRADES : "senator_id"

    REPRESENTATIVES ||--o{ REP_DONORS : "representative_id"
    REPRESENTATIVES ||--o{ REP_INDUSTRY_DONATIONS : "representative_id"
    REPRESENTATIVES ||--o{ REP_KEY_VOTES : "representative_id"
    REPRESENTATIVES ||--o{ REP_LOBBYING_MATCHES : "representative_id"
    REPRESENTATIVES ||--o{ REP_CAMPAIGN_PROMISES : "representative_id"
    REPRESENTATIVES ||--o{ REP_SPONSORED_BILLS : "representative_id"
    REPRESENTATIVES ||--o{ REP_STOCK_TRADES : "representative_id"

    SENATORS {
        int id PK
        string bioguide_id UK
        string name
        string state
        string party
        int years_in_office
        bool is_current
        float score_funding_independence
        float score_independent_voting "Constituent Alignment"
        float score_legislative_effectiveness
        float score_promise_persistence "unweighted since v6.0"
        float score_funding_diversity "unweighted since v6.5"
        float score_confidence
        float total_raised
        float total_from_pacs
        float small_donor_percentage
        float leadership_score
        float ideology_score
        float attracted_bipartisanship_score
    }

    REPRESENTATIVES {
        int id PK
        string bioguide_id UK
        string name
        string state
        string district
        string party
        float score_funding_independence
        float score_independent_voting
        float score_legislative_effectiveness
    }
```

## Other scored entities

```mermaid
erDiagram
    RACES ||--o{ CANDIDATES : "race_id"
    RACES ||--o{ RACE_COVERAGE_ITEMS : "race_id"
    JUSTICES ||--o{ JUSTICE_VOTES : "justice_id"

    PRESIDENTS {
        int id PK
        string name
        string party
        int number
        date term_start
        date term_end
        float score_public_mandate "21.67%"
        float score_effectiveness "21.67%"
        float score_agency_alignment "21.67%, N/A pre-Clinton"
        float score_historical_legacy "35%, C-SPAN 2021"
        float avg_approval
        float gdp_growth_avg
        int rulemaking_count
    }

    JUSTICES {
        int id PK
        string name
        string appointing_president
        float score_consistency "35%"
        float score_independence "30%"
        float score_judicial_restraint "20%"
        float score_bipartisan_agreement "15%"
        int cases_decided
        json agreement_matrix
    }

    RACES {
        int id PK
        int cycle_year
        string office
        string state
        string district
        bool is_special
    }
```

## Action Center

```mermaid
erDiagram
    NATIONAL_MONITORS ||--o{ MONITOR_UPDATES : "monitor_id"

    ACTION_ISSUES {
        int id PK "stable permalink target"
        date date
        int rank
        string title
        text summary
        json facts
        json actions
        json source_urls
        json related_bill_ids
        json related_monitor_slugs
        datetime bsky_posted_at "null = never posted"
        date primary_article_date "advances only on genuinely newer articles"
        bool is_current
    }

    NATIONAL_MONITORS {
        int id PK
        string slug UK
        string title
        string status "active | watching"
        json policy_areas
        date last_article_date
    }

    TIMELINE_ENTRIES {
        int id PK
        date date
        string title
    }
```

`ACTION_ISSUES` has no foreign keys to bills or members: `related_bill_ids`,
`related_senators` and `related_monitor_slugs` are JSON arrays of soft
references, populated by semantic search during the ENRICH step. They are
matches, not integrity constraints — the pipeline should not fail because a
linked bill was renumbered.

`WEEK_SUMMARIES`, `MONTH_SUMMARIES` and `YEAR_SUMMARIES` roll up
`TIMELINE_ENTRIES` at period boundaries with no FK, keyed by period.

## Score history

```mermaid
erDiagram
    SCORE_SNAPSHOTS {
        int id PK
        string entity_type "senator | representative | president | candidate"
        int entity_id "no FK - polymorphic"
        date date
        float overall_score
        float score_1 "meaning depends on entity_type"
        float score_2 "meaning depends on entity_type"
        float score_3 "meaning depends on entity_type"
        float score_4 "meaning depends on entity_type"
        float score_5 "meaning depends on entity_type"
        string algorithm_version "e.g. v6.12"
    }
```

One row per entity per run, carrying the algorithm version that produced it —
that last column is what stops the trend charts from comparing scores across
formula changes as though they were the same measurement.

**The numbered columns are a shared slot layout, not fixed dimensions.** Four
different writers use this table and each maps the slots differently, so reading
`score_3` without first checking `entity_type` will give you the wrong number:

| `entity_type` | `overall_score` | `score_1` | `score_2` | `score_3` | `score_4` | `score_5` |
|---|---|---|---|---|---|---|
| `senator`, `representative` | weighted overall | funding independence | promise persistence | constituent alignment | funding diversity | legislative effectiveness |
| `president` | weighted overall | public mandate | effectiveness | *retired* — always 0.0 | agency alignment | historical legacy |
| `candidate` | cash on hand | contributions | disbursements | — | — | — |

Three things worth knowing about that table:

- **`score_3` is a retired slot for presidents.** It held Competence until that
  dimension was removed in 2026-07, and has been 0.0 since. The slot was retired
  in place rather than reindexing every other dimension's historical rows.
- **The columns are `NOT NULL`,** so a president dimension that is genuinely
  inapplicable is stored as `0.0` here. That is not a score of zero — the
  authoritative "does this apply" answer always lives on the `presidents` row's
  own nullable column, never on the snapshot.
- **`candidate` rows aren't scores at all.** The election pipeline reuses the
  table as a financial time series, so `overall_score` is dollars of cash on
  hand. Rows are only written when a value actually changed.

## Not drawn

| Table | Purpose |
|---|---|
| `explore_documents` | Semantic search corpus — see [09](09-explore-search.md) |
| `api_cache`, `analysis_cache`, `learned_classifications` | See [06 — Caching](06-caching.md) |
| `pipeline_runs`, `house_pipeline_runs`, `supplementary_pipeline_runs`, `stock_trades_pipeline_runs`, `election_pipeline_runs` | Per-pipeline run bookkeeping; `pipeline_runs.status` also serves as the concurrency mutex |
| `bsky_senator_spotlights` | Which senator has been spotlighted, to cycle without repeats |

## Migrations

Lightweight and hand-rolled in `backend/app/database.py` — additive column
checks at startup rather than Alembic. Suits a single-node deployment with one
writer; see `tests/test_db_migrations.py` for the cases covered.

## Source map

All models: `backend/app/models.py`. Response shapes: `backend/app/schemas.py`.
