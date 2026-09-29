"""president_trades: a transaction date may be unknown

A scanned 278-T row whose date isn't legible is kept with its asset, type,
amount and filing date (ptr_common.ocr_extract_rows, keep_undated).

Relaxes a NOT NULL only, so the previous image runs unchanged against the
migrated schema (it never writes a NULL there).

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-29
"""
from alembic import op
import sqlalchemy as sa


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("president_trades", schema=None) as batch_op:
        batch_op.alter_column("transaction_date", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM president_trades WHERE transaction_date IS NULL")
    with op.batch_alter_table("president_trades", schema=None) as batch_op:
        batch_op.alter_column("transaction_date", existing_type=sa.String(), nullable=False)
