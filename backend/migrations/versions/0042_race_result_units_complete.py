"""race_results.units_complete: whether a count's reporting figure counts
units the source marks fully reported (Clarity, TotalVote, Tally) or units
that have reported any results (Enhanced Voting) -- the page words the two
differently (live_results/signals.reporting_line).

Nullable, so the previous image runs unchanged against the migrated schema;
NULL is a row written before the column, worded as before.

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-12
"""
from alembic import op
import sqlalchemy as sa


revision = '0042'
down_revision = '0041'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('race_results', sa.Column('units_complete', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('race_results', 'units_complete')
