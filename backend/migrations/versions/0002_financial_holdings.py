"""Financial disclosures and holdings (annual-report asset lists).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('financial_disclosures',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('senator_id', sa.String(), nullable=True),
    sa.Column('representative_id', sa.String(), nullable=True),
    sa.Column('filing_id', sa.String(), nullable=False),
    sa.Column('report_year', sa.Integer(), nullable=True),
    sa.Column('filed_date', sa.String(), nullable=True),
    sa.Column('source_url', sa.String(), nullable=False),
    sa.Column('parsed', sa.Boolean(), nullable=False),
    sa.Column('ingested_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['representative_id'], ['representatives.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['senator_id'], ['senators.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('financial_disclosures', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_financial_disclosures_filing_id'), ['filing_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_financial_disclosures_representative_id'), ['representative_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_financial_disclosures_senator_id'), ['senator_id'], unique=False)

    op.create_table('financial_holdings',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('disclosure_id', sa.Integer(), nullable=False),
    sa.Column('asset_name', sa.String(), nullable=False),
    sa.Column('account', sa.String(), nullable=True),
    sa.Column('ticker', sa.String(), nullable=True),
    sa.Column('asset_type', sa.String(), nullable=False),
    sa.Column('category', sa.String(), nullable=False),
    sa.Column('owner', sa.String(), nullable=False),
    sa.Column('value_text', sa.String(), nullable=False),
    sa.Column('value_low', sa.Float(), nullable=True),
    sa.Column('value_high', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['disclosure_id'], ['financial_disclosures.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('financial_holdings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_financial_holdings_category'), ['category'], unique=False)
        batch_op.create_index(batch_op.f('ix_financial_holdings_disclosure_id'), ['disclosure_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('financial_holdings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_financial_holdings_disclosure_id'))
        batch_op.drop_index(batch_op.f('ix_financial_holdings_category'))

    op.drop_table('financial_holdings')
    with op.batch_alter_table('financial_disclosures', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_financial_disclosures_senator_id'))
        batch_op.drop_index(batch_op.f('ix_financial_disclosures_representative_id'))
        batch_op.drop_index(batch_op.f('ix_financial_disclosures_filing_id'))

    op.drop_table('financial_disclosures')
