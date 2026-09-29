"""stock_trades: the asset type the Senate filer declared

The eFD table's Asset Type cell, read so the nightly industry pass can tell a
declared cryptocurrency from anything else (stock_pipeline).

Adds a nullable column only, so the previous image runs unchanged against the
migrated schema.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-29
"""
from alembic import op
import sqlalchemy as sa


revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("stock_trades", schema=None) as batch_op:
        batch_op.add_column(sa.Column("asset_type", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("stock_trades", schema=None) as batch_op:
        batch_op.drop_column("asset_type")
