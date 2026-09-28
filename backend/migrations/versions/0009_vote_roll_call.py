"""key_votes / rep_key_votes: which roll call each vote is

Nullable columns only, so the previous image runs unchanged against the
migrated schema.

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
    for table in ("key_votes", "rep_key_votes"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column("roll_call", sa.String(24), nullable=True))


def downgrade() -> None:
    for table in ("key_votes", "rep_key_votes"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("roll_call")
