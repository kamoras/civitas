"""Expected source failures as daily counters

`source_failure_counts`: one row per (UTC day, run kind, step, host, kind)
counting the steps that failed because a source refused Civitas or could
not be read (error_utils.ExpectedSourceFailure), for the admin's "Expected
source failures" chart.

Adds a table only. The previous image never reads it.

Revision ID: 0073
Revises: 0072 (0072_member_photo_hashes)
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa


revision = '0073'
down_revision = '0072'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'source_failure_counts',
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('run_kind', sa.String(length=64), nullable=False),
        sa.Column('step', sa.String(length=64), nullable=False),
        sa.Column('host', sa.String(length=128), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('date', 'run_kind', 'step', 'host', 'kind'),
    )


def downgrade() -> None:
    op.drop_table('source_failure_counts')
