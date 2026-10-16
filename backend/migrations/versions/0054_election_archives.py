"""Election archives: each held general election's frozen record (its
pre-election map, federal ballot, counts and statewide measures), kept
after the site moves on to the next cycle (app/election_archive.py)

Additive only (one new table), so the previous image runs unchanged
against the migrated schema.

Revision ID: 0054
Revises: 0051
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa


revision = '0054'
down_revision = '0051'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'election_archives',
        sa.Column('cycle', sa.Integer(), autoincrement=False, nullable=False),
        sa.Column('election_day', sa.String(length=10), nullable=False),
        sa.Column('map_json', sa.Text(), nullable=True),
        sa.Column('map_as_of', sa.DateTime(), nullable=True),
        sa.Column('record_json', sa.Text(), nullable=True),
        sa.Column('record_as_of', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('cycle'),
    )


def downgrade() -> None:
    op.drop_table('election_archives')
