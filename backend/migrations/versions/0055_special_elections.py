"""Special elections and the election calendar: the FEC's election dates
(with a special election's count), the chambers' vacant seats, and the
FEC's reporting deadlines

Additive only (three new tables), so the previous image runs unchanged
against the migrated schema.

Revision ID: 0055
Revises: 0054
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa


revision = '0055'
down_revision = '0054'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'federal_election_dates',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('election_date', sa.String(length=10), nullable=False),
        sa.Column('election_year', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(length=2), nullable=False),
        sa.Column('office', sa.String(length=1), nullable=False),
        sa.Column('district', sa.Integer(), nullable=True),
        sa.Column('type_code', sa.String(length=4), nullable=False),
        sa.Column('type_label', sa.String(), nullable=True),
        sa.Column('is_special', sa.Boolean(), nullable=False),
        sa.Column('note', sa.String(), nullable=True),
        sa.Column('listed_on', sa.String(length=10), nullable=False),
        sa.Column('result_source_name', sa.String(), nullable=True),
        sa.Column('result_source_url', sa.String(), nullable=True),
        sa.Column('tallies', sa.Text(), nullable=True),
        sa.Column('votes_counted', sa.Integer(), nullable=True),
        sa.Column('reporting_units', sa.Integer(), nullable=True),
        sa.Column('total_units', sa.Integer(), nullable=True),
        sa.Column('unit_label', sa.String(length=20), nullable=True),
        sa.Column('units_complete', sa.Boolean(), nullable=True),
        sa.Column('official', sa.Boolean(), nullable=False),
        sa.Column('source_updated_at', sa.DateTime(), nullable=True),
        sa.Column('source_version', sa.String(length=40), nullable=True),
        sa.Column('last_change_at', sa.DateTime(), nullable=True),
        sa.Column('read_status', sa.String(length=20), nullable=True),
        sa.Column('read_checked_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_federal_election_dates_election_date', 'federal_election_dates', ['election_date'])
    op.create_index('ix_federal_election_dates_election_year', 'federal_election_dates', ['election_year'])
    op.create_table(
        'seat_vacancies',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('office', sa.String(length=1), nullable=False),
        sa.Column('state', sa.String(length=2), nullable=False),
        sa.Column('district', sa.Integer(), nullable=True),
        sa.Column('senate_class', sa.Integer(), nullable=True),
        sa.Column('predecessor', sa.String(), nullable=True),
        sa.Column('predecessor_party', sa.String(length=1), nullable=True),
        sa.Column('vacated_on', sa.String(length=10), nullable=True),
        sa.Column('footnote', sa.String(), nullable=True),
        sa.Column('source_url', sa.String(), nullable=False),
        sa.Column('read_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'fec_reporting_deadlines',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('due_date', sa.String(length=10), nullable=False),
        sa.Column('report_type', sa.String(length=8), nullable=False),
        sa.Column('label', sa.String(), nullable=True),
        sa.Column('report_year', sa.Integer(), nullable=True),
        sa.Column('rows', sa.Integer(), nullable=False),
        sa.Column('read_on', sa.String(length=10), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_fec_reporting_deadlines_due_date', 'fec_reporting_deadlines', ['due_date'])


def downgrade() -> None:
    op.drop_index('ix_fec_reporting_deadlines_due_date', table_name='fec_reporting_deadlines')
    op.drop_table('fec_reporting_deadlines')
    op.drop_table('seat_vacancies')
    op.drop_index('ix_federal_election_dates_election_year', table_name='federal_election_dates')
    op.drop_index('ix_federal_election_dates_election_date', table_name='federal_election_dates')
    op.drop_table('federal_election_dates')
