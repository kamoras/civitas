"""Action issues: retired_reason, why a developing draft left the Action
Center (unconfirmed, or covered by a reported issue)

Additive only (one nullable column), so the previous image runs unchanged
against the migrated schema. Rows retired before it are given a reason by
the hourly refresh (early_signal.reword_retired_drafts).

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa


revision = '0051'
down_revision = '0050'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('action_issues') as batch_op:
        batch_op.add_column(sa.Column('retired_reason', sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('action_issues') as batch_op:
        batch_op.drop_column('retired_reason')
