"""race_results / election_result_events / live_result_reads: live
general-election counts; action_issues.count_as_of / count_official

New tables and two nullable columns only, so the previous image runs
unchanged against the migrated schema.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa


revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('election_result_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('race_id', sa.String(), nullable=False),
    sa.Column('election_date', sa.String(length=10), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('detail', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('bsky_posted_at', sa.DateTime(), nullable=True),
    sa.Column('bsky_posted', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['race_id'], ['races.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('election_result_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_election_result_events_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_election_result_events_election_date'), ['election_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_election_result_events_race_id'), ['race_id'], unique=False)

    op.create_table('live_result_reads',
    sa.Column('state', sa.String(length=2), nullable=False),
    sa.Column('election_date', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('checked_at', sa.DateTime(), nullable=False),
    sa.Column('last_ok_at', sa.DateTime(), nullable=True),
    sa.Column('reason_kind', sa.String(length=16), nullable=True),
    sa.PrimaryKeyConstraint('state', 'election_date')
    )
    op.create_table('race_results',
    sa.Column('race_id', sa.String(), nullable=False),
    sa.Column('election_date', sa.String(length=10), nullable=False),
    sa.Column('source_name', sa.String(), nullable=False),
    sa.Column('source_url', sa.String(), nullable=True),
    sa.Column('tallies', sa.Text(), nullable=False),
    sa.Column('votes_counted', sa.Integer(), nullable=False),
    sa.Column('reporting_units', sa.Integer(), nullable=True),
    sa.Column('total_units', sa.Integer(), nullable=True),
    sa.Column('unit_label', sa.String(length=20), nullable=False),
    sa.Column('official', sa.Boolean(), nullable=False),
    sa.Column('held_by_party', sa.String(length=8), nullable=True),
    sa.Column('developing_issue_id', sa.Integer(), nullable=True),
    sa.Column('source_updated_at', sa.DateTime(), nullable=True),
    sa.Column('source_version', sa.String(length=40), nullable=True),
    sa.Column('first_reported_at', sa.DateTime(), nullable=False),
    sa.Column('last_change_at', sa.DateTime(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['race_id'], ['races.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('race_id')
    )
    with op.batch_alter_table('race_results', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_race_results_election_date'), ['election_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_race_results_last_change_at'), ['last_change_at'], unique=False)

    with op.batch_alter_table('action_issues', schema=None) as batch_op:
        batch_op.add_column(sa.Column('count_as_of', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('count_official', sa.Boolean(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('action_issues', schema=None) as batch_op:
        batch_op.drop_column('count_official')
        batch_op.drop_column('count_as_of')

    with op.batch_alter_table('race_results', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_race_results_last_change_at'))
        batch_op.drop_index(batch_op.f('ix_race_results_election_date'))

    op.drop_table('race_results')
    op.drop_table('live_result_reads')
    with op.batch_alter_table('election_result_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_election_result_events_race_id'))
        batch_op.drop_index(batch_op.f('ix_election_result_events_election_date'))
        batch_op.drop_index(batch_op.f('ix_election_result_events_created_at'))

    op.drop_table('election_result_events')
