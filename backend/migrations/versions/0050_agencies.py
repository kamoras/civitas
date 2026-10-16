"""Agencies: the Federal Register's agency list, USAspending's toptier
budgets, and explore_documents.agency_slug

Additive only (new tables, a nullable column), so the previous image runs
unchanged against the migrated schema.

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
    op.create_table(
        'agencies',
        sa.Column('slug', sa.String(), nullable=False),
        sa.Column('fr_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('short_name', sa.String(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('fr_url', sa.String(), nullable=True),
        sa.Column('website', sa.String(), nullable=True),
        sa.Column('parent_slug', sa.String(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('slug'),
        sa.UniqueConstraint('fr_id'),
    )
    op.create_index('ix_agencies_parent_slug', 'agencies', ['parent_slug'])
    op.create_table(
        'agency_budgets',
        sa.Column('toptier_code', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('abbreviation', sa.String(), nullable=True),
        sa.Column('usaspending_slug', sa.String(), nullable=True),
        sa.Column('agency_slug', sa.String(), nullable=True),
        sa.Column('match_score', sa.Float(), nullable=True),
        sa.Column('fiscal_year', sa.Integer(), nullable=True),
        sa.Column('fiscal_quarter', sa.Integer(), nullable=True),
        sa.Column('budget_authority', sa.Float(), nullable=True),
        sa.Column('obligated', sa.Float(), nullable=True),
        sa.Column('outlays', sa.Float(), nullable=True),
        sa.Column('by_year', sa.Text(), nullable=True),
        sa.Column('by_year_synced_at', sa.DateTime(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), nullable=False),
        sa.Column('retired_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('toptier_code'),
    )
    op.create_index('ix_agency_budgets_agency_slug', 'agency_budgets', ['agency_slug'])
    with op.batch_alter_table('explore_documents') as batch_op:
        batch_op.add_column(sa.Column('agency_slug', sa.String(), nullable=True))
    op.create_index('ix_explore_documents_agency_slug', 'explore_documents', ['agency_slug'])


def downgrade() -> None:
    op.drop_index('ix_explore_documents_agency_slug', table_name='explore_documents')
    with op.batch_alter_table('explore_documents') as batch_op:
        batch_op.drop_column('agency_slug')
    op.drop_index('ix_agency_budgets_agency_slug', table_name='agency_budgets')
    op.drop_table('agency_budgets')
    op.drop_index('ix_agencies_parent_slug', table_name='agencies')
    op.drop_table('agencies')
