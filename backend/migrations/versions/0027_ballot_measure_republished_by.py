"""ballot_measures.republished_by: the county office whose copy of the
state's document a measure was read from

One nullable column, so the previous image runs unchanged against the
migrated schema.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa


revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('ballot_measures', sa.Column('republished_by', sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column('ballot_measures', 'republished_by')
