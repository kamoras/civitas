"""Members' stored score breakdown and held-score marker: the breakdown the
run computed with the scores it stored, and, when the latest run could not
score a member, which run's scores they keep and why

Additive only (four nullable columns), so the previous image runs unchanged
against the migrated schema.

Revision ID: 0057
Revises: 0056
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa


revision = '0057'
down_revision = '0056'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ('senators', 'representatives'):
        op.add_column(table, sa.Column('score_breakdown', sa.Text(), nullable=True))
        op.add_column(table, sa.Column('score_held', sa.Text(), nullable=True))


def downgrade() -> None:
    for table in ('senators', 'representatives'):
        op.drop_column(table, 'score_held')
        op.drop_column(table, 'score_breakdown')
