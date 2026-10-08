"""candidates.ballot_party: the party the state's list prints for a
confirmed candidate, shown over the FEC filing's code; candidates.
member_bioguide: the member of Congress the FEC id belongs to

Nullable columns, so the previous image runs unchanged against the
migrated schema.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa


revision = '0034'
down_revision = '0033'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('candidates', sa.Column('ballot_party', sa.String(), nullable=True))
    op.add_column('candidates', sa.Column('member_bioguide', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('candidates', 'member_bioguide')
    op.drop_column('candidates', 'ballot_party')
