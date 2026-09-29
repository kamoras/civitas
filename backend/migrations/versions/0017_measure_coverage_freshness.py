"""measure coverage: last successful check, and the shrink streak

Five nullable/defaulted columns on measure_coverage, so the previous
image runs unchanged against the migrated schema (expand only):

- last_success_at: when the status shown was last established by a read
  that worked. A failed read no longer refreshes the page's "checked"
  date, so a state serving yesterday's measures after tonight's failure
  says so.
- pending_shrink / shrink_streak: a direct read returning fewer measures
  than are on file is held back (MEASURE_SHRINK_FLOOR) until the same
  shorter list has come back on MEASURE_SHRINK_CONFIRM_RUNS consecutive
  runs; these remember which list and how many runs.
- operator_note: set when an operator accepted a state's absence for an
  election (POST /api/admin/ballot-measures/{state}/{date}/accept-absence);
  while set, a reader reporting the document as not published leaves that
  accepted answer standing instead of alarming every night.
- operator_actions: every accept-absence action (JSON list: when, note,
  force, rows marked). An audit trail that a reader's later answer does
  not clear, unlike operator_note.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("measure_coverage") as batch:
        batch.add_column(sa.Column("last_success_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("pending_shrink", sa.Text(), nullable=True))
        batch.add_column(sa.Column("shrink_streak", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("operator_note", sa.Text(), nullable=True))
        batch.add_column(sa.Column("operator_actions", sa.Text(), nullable=True))
    # last_success_at means "an answer was established" — covered or
    # confirmed none, the only statuses a successful read writes. For
    # those rows the old checked_at is the best record of when; a
    # not_yet_covered or ingest_failed row established nothing and gets
    # none.
    op.execute(
        "UPDATE measure_coverage SET last_success_at = checked_at "
        "WHERE status IN ('covered', 'confirmed_none')"
    )


def downgrade() -> None:
    with op.batch_alter_table("measure_coverage") as batch:
        batch.drop_column("operator_actions")
        batch.drop_column("operator_note")
        batch.drop_column("shrink_streak")
        batch.drop_column("pending_shrink")
        batch.drop_column("last_success_at")
