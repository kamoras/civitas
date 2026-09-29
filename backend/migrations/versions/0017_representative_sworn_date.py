"""representative sworn_date: when a member took the seat this Congress

Legislative Effectiveness prorates its bar for a member sworn in
mid-Congress (a special election) by the share of the Congress served
(v6.23). The date comes from the House Clerk's member list.

Expand only: one nullable column. An image without it never reads it.

This merged as a second "0016" beside financial_disclosures.president_id,
so a database may be stamped 0016 having run either one. Both halves are
added only where missing, and every such database converges here.

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


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "sworn_date" not in _columns("representatives"):
        with op.batch_alter_table("representatives", schema=None) as batch_op:
            batch_op.add_column(sa.Column("sworn_date", sa.String(length=10), nullable=True))
    # The other 0016's change, for a database that ran this revision as 0016.
    if "president_id" not in _columns("financial_disclosures"):
        with op.batch_alter_table("financial_disclosures", schema=None) as batch_op:
            batch_op.add_column(sa.Column("president_id", sa.String(), nullable=True))
            batch_op.create_foreign_key(
                "fk_financial_disclosures_president_id", "presidents", ["president_id"], ["id"], ondelete="CASCADE",
            )
            batch_op.create_index(batch_op.f("ix_financial_disclosures_president_id"), ["president_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("representatives", schema=None) as batch_op:
        batch_op.drop_column("sworn_date")
