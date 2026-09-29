"""president_trades: which report a row comes from (periodic 278-T or annual 278e)

A column with a server default only, so the previous image runs unchanged
against the migrated schema.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("president_trades", schema=None) as batch_op:
        batch_op.add_column(sa.Column("report_kind", sa.String(8), nullable=False, server_default="periodic"))


def downgrade() -> None:
    with op.batch_alter_table("president_trades", schema=None) as batch_op:
        batch_op.drop_column("report_kind")
