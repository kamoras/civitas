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

The rollback case also means the previous image meets a database stamped
with a revision it has never seen. `_run_migrations` leaves such a database
alone (it logs and starts) rather than letting Alembic fail with "Can't
locate revision" and crash-loop the rollback — but only when that revision
is a *later number* than the image's own head, which is why revisions are
numbered sequentially (`0002`, `0003`, ...). Any other unknown revision
still fails loudly. That guard only protects a rollback *to an image that
has it*: it shipped in a release of its own, deployed before the first
revision that relied on it (`0005`). `0002`–`0004` predate it — a rollback
across one of those is not covered.

The same release carried the tolerance for a trade owner of `unknown`,
which `0006` writes, on both sides: the schema's (`schemas.DisclosureOwner`)
— an image without it fails validation on such a row — and the frontend's
owner type and "OWNER NOT STATED" label, since Swarm rolls services back
one at a time and a rolled-back frontend can be served by the new backend.
Both had to be running before `0006` could be.

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

### Pending contract

`0019` dropped the columns no image had mapped since v6.13 (outside
spending, the opposing-party unity figure, GDP-adjusted growth) and released
the ones below: unmapped, made nullable so this image's inserts can leave
them out, kept in the database because the image before still selects them.
`tests/test_alembic_migrations.py`'s `UNMAPPED_PENDING_DROP` lists them, and
checks each is still present and nullable.

Write the drops as the next free revision once an image from `0019` or later
is the running one, and remove each from `UNMAPPED_PENDING_DROP`:

| Change | Why it waits |
|---|---|
| Drop `justices.score_consistency`, `score_independence`, `score_bipartisan_agreement`, `score_judicial_restraint` | Unscored since justice v2 / v6.13; images before `0019` still map them. |
| Drop `candidates.last_coverage_search` | The Bluesky candidate search it paced was removed; images before `0019` still map it. |
| Rename `score_independent_voting` → `score_constituent_alignment` on `senators` and `representatives` | Every image so far maps `score_constituent_alignment` onto the old column name, so an in-place rename breaks the running one. It needs two releases: add the new column and write both, then drop the old one. |
