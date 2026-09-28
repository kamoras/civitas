"""measure coverage: last successful check, and the shrink streak

Three nullable/defaulted columns on measure_coverage, so the previous
image runs unchanged against the migrated schema (expand only):

- last_success_at: when the status shown was last established by a read
  that worked. A failed read no longer refreshes the page's "checked"
  date, so a state serving yesterday's measures after tonight's failure
  says so.
- pending_shrink / shrink_streak: a direct read returning fewer measures
  than are on file is held back (MEASURE_SHRINK_FLOOR) until the same
  shorter list has come back on MEASURE_SHRINK_CONFIRM_RUNS consecutive
  runs; these remember which list and how many runs.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("measure_coverage") as batch:
        batch.add_column(sa.Column("last_success_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("pending_shrink", sa.Text(), nullable=True))
        batch.add_column(sa.Column("shrink_streak", sa.Integer(), nullable=False, server_default="0"))
    # Every existing row's status was written by a read that worked or by
    # a failure; the old checked_at is the best available record of the
    # former, and a failed row gets none.
    op.execute(
        "UPDATE measure_coverage SET last_success_at = checked_at WHERE status != 'ingest_failed'"
    )


def downgrade() -> None:
    with op.batch_alter_table("measure_coverage") as batch:
        batch.drop_column("shrink_streak")
        batch.drop_column("pending_shrink")
        batch.drop_column("last_success_at")
