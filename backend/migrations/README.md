# Main-database migrations (Alembic)

`init_db` applies these on every start, inside its cross-process lock. It runs
`upgrade head` against the main database (`app.database.Base`).

## Changing the schema

1. Change the model in `app/models.py`.
2. Generate a revision against a database at head, from `backend/`:

       DATABASE_URL=sqlite:////tmp/at-head.db alembic -c alembic.ini upgrade head
       DATABASE_URL=sqlite:////tmp/at-head.db alembic -c alembic.ini revision --autogenerate -m "what changed"

3. Read the generated file and fix it by hand:
   - **Dropping a column:** drop it here in the same change. A removed model
     column left in a deployed table as NOT NULL blocks every insert
     (#220, #611).
   - **Adding a NOT NULL column:** give it a `server_default`, or existing rows
     cannot take it.
   - **Changing a unique key or making a column nullable:** SQLite can't do
     this in place. `render_as_batch` makes Alembic rebuild the table (#592).
4. Run `pytest tests/test_alembic_migrations.py`. It builds a database from the
   revisions and diffs it against the models, and fails if they disagree. That
   is the check that stops a model change reaching production without a
   migration.

`0001_baseline` is a frozen snapshot of the schema at the switch-over
(2026-09). Never edit it.

## Databases from before Alembic

A database with tables but no `alembic_version` predates Alembic. The first
start after this change runs `_bridge_pre_alembic_schema` once:

- The hand-written migrations that used to run on every start.
- An in-place add of any nullable baseline column still missing.

Then the baseline creates only the tables that are absent, and later revisions
apply as usual. The bridge is frozen. Nothing new goes in it.

## Not managed here

- **Visits database** (`VisitsBase`, `visits.db`): kept physically separate for
  privacy (see `_derive_visits_database_url`), with its own `visits_migrations`
  ledger.
- **FTS5 keyword index** (`pipeline/lexical_index.py`) and **sqlite-vec
  tables** (`pipeline/vector_store.py`): SQLite virtual tables, which rebuild
  themselves on a layout change.
- **Partial unique indexes** in `_ensure_indexes`, such as the one-running-row
  locks: idempotent `CREATE ... IF NOT EXISTS`, applied after the upgrade.

## Expand, then contract

Production deploys with Swarm's `start-first` update and `FailureAction:
rollback`. Two consequences follow:

- The previous image keeps serving while the new one migrates the shared
  database.
- If the new image fails its health check, Swarm runs the previous image
  against the *migrated* schema.

So every release must leave a schema the image before it can still read:

- **Expand** (any release): add tables, add nullable or defaulted columns, add
  indexes. Stop *using* a column in the model, but leave it in the database.
  A NOT NULL column the old image reads stays in the model with a default, so
  inserts still work.
- **Contract** (a later release, once no running image references the column):
  drop it or rename it in a revision.

Found against production on PR #615. v6.13 first dropped and renamed columns
that `main`'s models still read, and `main`'s code then failed with `no such
column: presidents.gdp_growth_adjusted` on the migrated copy.

### Pending contract (the release after v6.13)

Write these as the next free revision (`0003` or later — `0002` added the
financial-holdings tables) once v6.13 is the running image:

| Change | Why it waits |
|---|---|
| Drop `justices.score_bipartisan_agreement`, `justices.score_judicial_restraint` | Unscored since v6.13; still NOT NULL and read by the v6.12 image. Drop them from the model in the same change. |
| Drop `senators.outside_spending_for`, `representatives.outside_spending_for` | No longer in the model (outside spending left Funding Independence); v6.12 reads them. |
| Drop `key_votes.opposing_party_unity_pct`, `rep_key_votes.opposing_party_unity_pct` | No longer in the model; v6.12 reads them. |
| Drop `presidents.gdp_growth_adjusted` | No longer in the model; v6.12 reads it. |
| Rename `score_independent_voting` → `score_constituent_alignment` on `senators` and `representatives` | The model maps `score_constituent_alignment` onto the old column name until then. Rename in place, which keeps stored scores. |
