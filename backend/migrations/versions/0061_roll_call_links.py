"""Roll calls related to an Action Center issue and a monitor update, and
the data-supplied labels for recalibrating that link

Additive only (two nullable columns and a new table), so the previous image
runs unchanged against the migrated schema.

Revision ID: 0061
Revises: 0060
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa


revision = '0061'
down_revision = '0060'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'roll_call_link_labels',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('issue_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('story', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=40), nullable=False),
        sa.Column('related', sa.Text(), nullable=False),
        sa.Column('candidates', sa.Text(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_roll_call_link_labels_issue_id', 'roll_call_link_labels', ['issue_id'], unique=True)
    with op.batch_alter_table('action_issues') as batch_op:
        batch_op.add_column(sa.Column('related_roll_call_refs', sa.Text(), nullable=True))
    with op.batch_alter_table('monitor_updates') as batch_op:
        batch_op.add_column(sa.Column('roll_call_refs', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_index('ix_roll_call_link_labels_issue_id', table_name='roll_call_link_labels')
    op.drop_table('roll_call_link_labels')
    with op.batch_alter_table('monitor_updates') as batch_op:
        batch_op.drop_column('roll_call_refs')
    with op.batch_alter_table('action_issues') as batch_op:
        batch_op.drop_column('related_roll_call_refs')
