"""Roll calls related to an Action Center issue and a monitor update

Additive only (two nullable columns), so the previous image runs unchanged
against the migrated schema.

Revision ID: 0059
Revises: 0058
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa


revision = '0059'
down_revision = '0058'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('action_issues') as batch_op:
        batch_op.add_column(sa.Column('related_roll_call_refs', sa.Text(), nullable=True))
    with op.batch_alter_table('monitor_updates') as batch_op:
        batch_op.add_column(sa.Column('roll_call_refs', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('monitor_updates') as batch_op:
        batch_op.drop_column('roll_call_refs')
    with op.batch_alter_table('action_issues') as batch_op:
        batch_op.drop_column('related_roll_call_refs')
