"""sponsored bill commemorative flag

Volden & Wiseman's commemorative significance tier, detected from the
title by analyze/commemorative.py and weighted 1x in Legislative
Effectiveness. NOT NULL with a server default, so the previous image —
which doesn't know the column — keeps inserting rows against the migrated
schema (expand, then contract).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-27
"""
from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_TABLES = ("sponsored_bills", "rep_sponsored_bills")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("commemorative", sa.Boolean(), server_default=sa.text("0"), nullable=False)
            )


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("commemorative")
