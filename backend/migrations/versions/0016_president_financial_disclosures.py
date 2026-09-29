"""financial_disclosures: the sitting president's annual report too

A nullable column only, so the previous image runs unchanged against the
migrated schema.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-29
"""
from alembic import op
import sqlalchemy as sa


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("financial_disclosures", schema=None) as batch_op:
        batch_op.add_column(sa.Column("president_id", sa.String(), nullable=True))
        batch_op.create_foreign_key(
            "fk_financial_disclosures_president_id", "presidents", ["president_id"], ["id"], ondelete="CASCADE",
        )
        batch_op.create_index(batch_op.f("ix_financial_disclosures_president_id"), ["president_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("financial_disclosures", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_financial_disclosures_president_id"))
        batch_op.drop_constraint("fk_financial_disclosures_president_id", type_="foreignkey")
        batch_op.drop_column("president_id")
