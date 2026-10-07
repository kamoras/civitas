"""presidents.unemployment_start / unemployment_change / inflation_start /
inflation_average / economy_years: the unemployment and inflation figures
Effectiveness judges a term on since president v10

Five nullable columns, so the previous image runs unchanged against the
migrated schema.

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa


revision = '0033'
down_revision = '0032'
branch_labels = None
depends_on = None

_FLOATS = ('unemployment_start', 'unemployment_change', 'inflation_start', 'inflation_average')


def upgrade() -> None:
    for name in _FLOATS:
        op.add_column('presidents', sa.Column(name, sa.Float(), nullable=True))
    op.add_column('presidents', sa.Column('economy_years', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('presidents', 'economy_years')
    for name in reversed(_FLOATS):
        op.drop_column('presidents', name)
