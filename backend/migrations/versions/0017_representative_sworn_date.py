"""representative sworn_date: when a member took the seat this Congress

Legislative Effectiveness prorates its bar for a member sworn in
mid-Congress (a special election) by the share of the Congress served
(v6.23). The date comes from the House Clerk's member list.

Expand only: one nullable column. An image without it never reads it.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-29
"""
import sqlalchemy as sa
from alembic import op


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("representatives", sa.Column("sworn_date", sa.String(length=10), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("representatives") as batch:
        batch.drop_column("sworn_date")
