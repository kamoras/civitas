"""trade owners: version the PTR parser, stop guessing OCR owners

stock_trades.parser_version records which ptr_common.PARSER_VERSION read
a Senate trade. Version 1 (every existing row) read eFD's electronic
tables with the House's owner codes only, while eFD prints the owner as a
word ("Spouse", "Joint", "Child", "Self" — 173 rows across 25 filings,
checked 2026-09-27), so every such trade was stored as the senator's
own. The stock pipeline reads those filings again (_reread_senate).

Rows read by OCR never read an owner column; their "self" was a guess and
becomes "unknown", in all three trade tables.

Expand-only: a column with a default, and an owner value the previous
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


def upgrade() -> None:
    with op.batch_alter_table("stock_trades") as batch_op:
        batch_op.add_column(sa.Column("parser_version", sa.Integer(), server_default="1", nullable=False))
    for table in ("stock_trades", "rep_stock_trades", "president_trades"):
        op.execute(f"UPDATE {table} SET owner = 'unknown' WHERE parse_confidence = 'ocr' AND owner = 'self'")


def downgrade() -> None:
    with op.batch_alter_table("stock_trades") as batch_op:
        batch_op.drop_column("parser_version")
