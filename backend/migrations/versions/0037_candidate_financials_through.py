"""candidates.financials_through: the last day a candidate's FEC totals
cover, shown as what the figures are as of (the sync date is only when
they were last checked)

Nullable column, so the previous image runs unchanged against the
migrated schema.

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa


revision = '0037'
down_revision = '0036'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('candidates', sa.Column('financials_through', sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column('candidates', 'financials_through')
