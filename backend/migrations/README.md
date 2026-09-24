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
