"""candidates: where a campaign's contributions came from (the FEC totals
row's own categories) and the candidate's loans, for the ballot page's
money view

Nullable columns only, so the previous image runs unchanged against the
migrated schema (it never reads them).

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-14
"""
from alembic import op
import sqlalchemy as sa


revision = '0045'
down_revision = '0044'
branch_labels = None
depends_on = None

_FLOATS = (
    'individual_unitemized_contributions', 'other_committee_contributions',
    'party_committee_contributions', 'candidate_contribution', 'candidate_loans',
)


def upgrade() -> None:
    for name in _FLOATS:
        op.add_column('candidates', sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('candidates') as batch:
        for name in _FLOATS:
            batch.drop_column(name)
