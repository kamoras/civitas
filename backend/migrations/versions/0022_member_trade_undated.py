"""stock_trades, rep_stock_trades: a transaction date may be unknown

A scanned PTR row whose date isn't legible is kept with its asset, type,
amount and filing date (ptr_common.ocr_extract_rows, keep_undated), as the
president's already are (0019).

Relaxes a NOT NULL only, so the previous image runs unchanged against the
migrated schema (it never writes a NULL there).

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-29
"""
from alembic import op
import sqlalchemy as sa


revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

_TABLES = ("stock_trades", "rep_stock_trades")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column("transaction_date", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DELETE FROM {table} WHERE transaction_date IS NULL")
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column("transaction_date", existing_type=sa.String(), nullable=False)
