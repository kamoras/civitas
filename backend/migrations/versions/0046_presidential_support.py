"""policy_statements, statement_roll_calls, presidential_support: the
president's stated positions (OMB Statements of Administration Policy), the
roll calls they apply to, and each member's rate of voting with them
(analyze/presidential_support.py)

New tables, so the previous image runs unchanged against the migrated
schema (it never reads them).

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-14
"""
from alembic import op
import sqlalchemy as sa


revision = '0046'
down_revision = '0045'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'policy_statements',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('url', sa.String(length=300), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('readable', sa.Boolean(), nullable=False),
        sa.Column('issued', sa.String(length=10), nullable=False),
        sa.Column('chamber', sa.String(length=6), nullable=False),
        sa.Column('measures', sa.Text(), nullable=False),
        sa.Column('amendments', sa.Text(), nullable=False),
        sa.Column('position', sa.String(length=8), nullable=False),
        sa.Column('position_sentence', sa.Text(), nullable=False),
        sa.Column('parser_version', sa.Integer(), nullable=False),
        sa.Column('classifier_version', sa.Integer(), nullable=False),
        sa.Column('fetched_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('url'),
    )
    op.create_table(
        'statement_roll_calls',
        sa.Column('roll_call_id', sa.Integer(), nullable=False),
        sa.Column('statement_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['roll_call_id'], ['roll_calls.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['statement_id'], ['policy_statements.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('roll_call_id'),
    )
    op.create_index(op.f('ix_statement_roll_calls_statement_id'), 'statement_roll_calls', ['statement_id'])
    op.create_table(
        'presidential_support',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('chamber', sa.String(length=6), nullable=False),
        sa.Column('member_id', sa.String(), nullable=False),
        sa.Column('congress', sa.Integer(), nullable=False),
        sa.Column('party', sa.String(length=1), nullable=False),
        sa.Column('seat_alignment', sa.Float(), nullable=True),
        sa.Column('seat_lean', sa.Integer(), nullable=True),
        sa.Column('with_president', sa.Integer(), nullable=False),
        sa.Column('votes', sa.Integer(), nullable=False),
        sa.Column('expected', sa.Float(), nullable=True),
        sa.Column('computed_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('chamber', 'member_id', name='uq_presidential_support_member'),
    )


def downgrade() -> None:
    op.drop_table('presidential_support')
    op.drop_index(op.f('ix_statement_roll_calls_statement_id'), table_name='statement_roll_calls')
    op.drop_table('statement_roll_calls')
    op.drop_table('policy_statements')
