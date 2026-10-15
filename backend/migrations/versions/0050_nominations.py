"""Nominations: Congress.gov's presidential nominations, one row per citation

Additive only (a new table), so the previous image runs unchanged against
the migrated schema.

Revision ID: 0050
Revises: 0049
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa


revision = '0050'
down_revision = '0049'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('nominations',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('congress', sa.Integer(), nullable=False),
    sa.Column('citation', sa.String(length=16), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('part_number', sa.String(length=4), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('received_date', sa.String(length=10), nullable=False),
    sa.Column('organization', sa.String(length=300), nullable=False),
    sa.Column('position_title', sa.Text(), nullable=False),
    sa.Column('is_military', sa.Boolean(), nullable=False),
    sa.Column('is_list', sa.Boolean(), nullable=False),
    sa.Column('nominee_count', sa.Integer(), nullable=False),
    sa.Column('nominees', sa.Text(), nullable=False),
    sa.Column('actions', sa.Text(), nullable=False),
    sa.Column('latest_action_date', sa.String(length=10), nullable=False),
    sa.Column('latest_action_text', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('status_date', sa.String(length=10), nullable=True),
    sa.Column('committee_code', sa.String(length=8), nullable=True),
    sa.Column('committee_name', sa.String(length=200), nullable=False),
    sa.Column('hearings', sa.Text(), nullable=False),
    sa.Column('fjc_judge_id', sa.String(length=12), nullable=True),
    sa.Column('fjc_node_id', sa.String(length=12), nullable=True),
    sa.Column('update_date', sa.String(length=25), nullable=False),
    sa.Column('fetched_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('congress', 'citation', name='uq_nomination')
    )
    with op.batch_alter_table('nominations', schema=None) as batch_op:
        batch_op.create_index('ix_nomination_status', ['congress', 'status'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('nominations', schema=None) as batch_op:
        batch_op.drop_index('ix_nomination_status')

    op.drop_table('nominations')
