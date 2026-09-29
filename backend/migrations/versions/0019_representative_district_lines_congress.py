"""representative district_lines_congress: the lines a stored score used

The Congress whose district lines a representative's stored scores were
computed on (fetch/district_pvi.lines_congress). The score breakdown
recomputes Constituent Alignment on the same lines, so the two agree
across a change of Congress.

Expand only: one nullable column. An image without it never reads it.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-29
"""
import sqlalchemy as sa
from alembic import op


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("representatives", sa.Column("district_lines_congress", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("representatives") as batch:
        batch.drop_column("district_lines_congress")
