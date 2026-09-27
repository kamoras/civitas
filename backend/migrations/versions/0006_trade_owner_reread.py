"""trade owners: version the PTR parser, stop guessing OCR owners

Each trade table gets parser_version: which ptr_common.PARSER_VERSION read
the row. Version 1 (every existing row) read eFD's electronic tables with
the House's owner codes only, while eFD prints the owner as a word
("Spouse", "Joint", "Child", "Self" — 173 rows across 25 filings, checked
2026-09-27), so every such Senate trade was stored as the senator's own;
it also read a table with no owner column as the filer's. The stock
pipeline reads version-1 filings again (_reread_trades).

Rows read by OCR never read an owner column; their "self" was a guess and
becomes "unknown", which is exactly what version 2 reads from those scans,
so they are marked version 2 and not fetched again.

Expand-only: columns with a default, and an owner value the previous
image's schema already reads (DisclosureOwner).

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-27
"""
from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_TABLES = ("stock_trades", "rep_stock_trades", "president_trades")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("parser_version", sa.Integer(), server_default="1", nullable=False))
        op.execute(f"UPDATE {table} SET owner = 'unknown' WHERE parse_confidence = 'ocr' AND owner = 'self'")
        op.execute(f"UPDATE {table} SET parser_version = 2 WHERE parse_confidence = 'ocr'")


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("parser_version")
