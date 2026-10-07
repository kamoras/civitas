"""presidents.gdp_growth_per_person / gdp_growth_peer_median /
gdp_growth_relative: a term's real GDP growth per person, the peer
economies' median over the same years, and the difference with the peers'
catch-up growth set aside, which Effectiveness scores (president v8)

Three nullable columns, so the previous image runs unchanged against the
migrated schema.

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa


revision = '0031'
down_revision = '0030'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('presidents', sa.Column('gdp_growth_per_person', sa.Float(), nullable=True))
    op.add_column('presidents', sa.Column('gdp_growth_peer_median', sa.Float(), nullable=True))
    op.add_column('presidents', sa.Column('gdp_growth_relative', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('presidents', 'gdp_growth_relative')
    op.drop_column('presidents', 'gdp_growth_peer_median')
    op.drop_column('presidents', 'gdp_growth_per_person')
