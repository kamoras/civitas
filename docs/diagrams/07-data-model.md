# Data model

45 tables in one SQLite file (`/data/civitas.db`), plus a separate sqlite-vec
file (`/data/vectors.db`) holding the `vec_explore` and `vec_bills` vector
tables — split out for writer-lock isolation, not just tidiness. Shown here in clusters; infrastructure tables are listed rather
than drawn.

**Columns are a selection, not the full schema.** Each entity draws enough to
follow its relationships and the behavior these diagrams discuss, not every
column on the table — `backend/app/models.py` is the authority. An undrawn
column is one these diagrams don't need, not one that doesn't exist.

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
        bool is_current "false = left office; row deleted 180 days later"
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
        bool is_current "false = left office; row deleted 180 days later"
        float score_funding_independence
        float score_independent_voting "Constituent Alignment"
        float score_legislative_effectiveness
        float score_promise_persistence "unweighted since v6.0"
        float score_funding_diversity "unweighted since v6.5"
        float score_confidence
    }
```

## Other scored entities

```mermaid
erDiagram
    JUSTICES ||--o{ JUSTICE_VOTES : "justice_id"
    PRESIDENTS ||--o{ PRESIDENT_TRADES : "president_id"

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

    PRESIDENT_TRADES {
        int id PK
        string president_id FK
        string asset_name "securities and virtual currency alike"
        string ticker "null for crypto and bond lines"
        string transaction_type "purchase | sale_full | sale_partial | exchange"
        date transaction_date
        date disclosure_date
        float amount_low "OGE 278-T reports a range, never a single figure"
        float amount_high "== amount_low encodes the open-ended top bracket"
        string industry "embedding-classified, same classifier as donors"
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
        json source_names
        json policy_areas
        json related_bill_ids
        json related_senators
        json related_officials "a 'named in coverage' match is a publish gate"
        json related_explore_ids "2+ is a publish gate"
        json related_monitor_slugs
        text full_story "cached long-form text, cleared when the story shifts"
        datetime bsky_posted_at "null = awaiting the poster, NOT never posted"
        int bsky_posted_rank "rank at the time of that post"
        text bsky_last_post_text "what was published; near-duplicate gate + retention key"
        json bsky_posted_facts "facts as of the last post, the repost baseline"
        date primary_article_date "advances only on genuinely newer articles"
        bool is_current
    }

    NATIONAL_MONITORS {
        int id PK
        string slug UK
        string title
        string status "active | watching | closed"
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

**`bsky_posted_at` is not a "has this been published" flag**, and reading it as
one is a live bug source. It means "the poster has nothing queued for this
issue": the repost path clears it back to NULL to hand a published issue *back*
to the poster, so a row that published, was flagged for a repost, then failed to
publish the update (two grounding rejections, a publish error) or stopped being
matched sits at NULL with a real post live in the feed. The 14-day cleanup of
old issues therefore keys on `bsky_last_post_text`, which is only ever written
on a successful publish and never cleared — the honest record of "readers have a
URL for this". Near-duplicate suppression leaves it NULL on purpose: nothing was
published, so there is no permalink to protect and the row is free to age out.

## Elections and ballots

```mermaid
erDiagram
    RACES ||--o{ CANDIDATES : "race_id"
    RACES ||--o{ RACE_COVERAGE_ITEMS : "race_id"

    RACES {
        string id PK "2026-SEN-GA · 2026-HOUSE-CA-12"
        int cycle_year
        string office "S | H — FEC codes"
        string state
        int district "NULL for Senate, 0 = at-large"
        bool is_special
    }
    CANDIDATES {
        string id PK "FEC candidate_id"
        string race_id FK
        string name
        string party
        string incumbent_challenge "I | C | O"
        float contributions "NULL = not yet synced, never 0"
        float cash_on_hand "NULL = not yet synced, never 0"
        datetime last_financials_sync "watermark for the rotating refresh"
    }
    RACE_COVERAGE_ITEMS {
        int id PK
        string race_id FK
        string source_type "news | bluesky"
        string title "verbatim — never model-generated"
        string url
        string match_basis "full_name | surname_context"
    }
    BALLOT_MEASURES {
        string id PK "source's own stable id"
        string state
        string election_date "part of identity, not cycle_year"
        string election_type "primary | general | runoff | special"
        string number "mutable — measures get renumbered"
        string official_title "verbatim"
        string official_summary "verbatim"
        string fiscal_impact "verbatim"
        string yes_means "verbatim or NULL — never derived"
        string no_means "verbatim or NULL — never derived"
        string title_authority "who drafted the title"
        string status "certified | removed | withdrawn | under_appeal"
        datetime last_seen_at "drives the removal grace window"
    }
    MEASURE_COVERAGE {
        int id PK
        string state
        string election_date
        string status "covered | confirmed_none | not_yet_covered | ingest_failed"
        int measure_count
        datetime checked_at
    }
```

Three modelling decisions here are load-bearing rather than incidental:

- **`BALLOT_MEASURES` has no relationship to `RACES`.** Measures and candidate
  contests are independent things that happen to share a ballot, and the state
  ballot endpoint assembles both by `state` rather than joining them.
- **The key is `(state, election_date, number)`, not cycle year.** Ohio can run
  an "Issue 1" in a May primary and a different "Issue 1" in November; a
  cycle-year key collides them and the later sync overwrites the earlier
  measure's text.
- **`MEASURE_COVERAGE` exists so absence can explain itself.** Without it, "this
  state has no statewide measures" and "we have not ingested this state" are the
  same empty list — and on a page about somebody's ballot those are very
  different claims. See AGENTS.md principle 7.

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
| `explore_documents` | Hybrid search corpus; source of truth for the vector index, the FTS5 keyword index, and citation authority — see [09](09-explore-search.md) |
| `api_cache`, `analysis_cache`, `learned_classifications` | See [06 — Caching](06-caching.md) |
| `pipeline_runs`, `house_pipeline_runs`, `supplementary_pipeline_runs`, `stock_trades_pipeline_runs`, `election_pipeline_runs` | Per-pipeline run bookkeeping; `pipeline_runs.status` also serves as the concurrency mutex |
| `bsky_senator_spotlights` | Which senator has been spotlighted, to cycle without repeats |

## Migrations

Lightweight and hand-rolled in `backend/app/database.py` — additive column
checks at startup rather than Alembic. Suits a single-node deployment with one
writer; see `tests/test_db_migrations.py` for the cases covered.

## Source map

All models: `backend/app/models.py`. Response shapes: `backend/app/schemas.py`.
