"""stock_trades: the asset type the Senate filer declared

The eFD table's Asset Type cell, read so the nightly industry pass can tell a
declared cryptocurrency from anything else (stock_pipeline).

Adds a nullable column only, so the previous image runs unchanged against the
migrated schema.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa


revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("stock_trades", schema=None) as batch_op:
        batch_op.add_column(sa.Column("asset_type", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("stock_trades", schema=None) as batch_op:
        batch_op.drop_column("asset_type")
