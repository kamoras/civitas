"""action_issues: which row a near-identical duplicate repeats

Nullable column only, so the previous image runs unchanged against the
migrated schema. Filled by the hourly Action Center refresh
(action_center.mark_recent_duplicates); until its first run after this
release the homepage feed shows the rows unmarked.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("action_issues", schema=None) as batch_op:
        batch_op.add_column(sa.Column("duplicate_of_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("action_issues", schema=None) as batch_op:
        batch_op.drop_column("duplicate_of_id")
