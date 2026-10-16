"""The Senate's tie-breaker on a roll call: who broke the tie and how

Additive only (two nullable columns), so the previous image runs unchanged
against the migrated schema.

Revision ID: 0056
Revises: 0055
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa


revision = '0056'
down_revision = '0055'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('roll_calls') as batch_op:
        batch_op.add_column(sa.Column('tie_breaker_by', sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column('tie_breaker_vote', sa.String(length=12), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('roll_calls') as batch_op:
        batch_op.drop_column('tie_breaker_vote')
        batch_op.drop_column('tie_breaker_by')
