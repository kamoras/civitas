"""ballot_measures.source_position, summary_authority, framing_authority:
the measure's place in the state's own document (the page sorted the
printed number as a string), and the drafters of the summary and the
yes/no framing where the state names one other than the title's

Nullable columns, so the previous image runs unchanged against the
migrated schema.

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa


revision = '0038'
down_revision = '0037'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('ballot_measures', sa.Column('summary_authority', sa.String(length=200), nullable=True))
    op.add_column('ballot_measures', sa.Column('framing_authority', sa.String(length=200), nullable=True))
    op.add_column('ballot_measures', sa.Column('source_position', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('ballot_measures', 'source_position')
    op.drop_column('ballot_measures', 'framing_authority')
    op.drop_column('ballot_measures', 'summary_authority')
