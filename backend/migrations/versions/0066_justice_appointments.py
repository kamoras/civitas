"""Every Supreme Court appointment, for a president's page

Adds `justice_appointments`: each appointment in the Federal Judicial
Center's directory (name, seat, appointing president's id, nomination
date, years served), written by the justice pipeline. Empty until the
pipeline's next run.

Additive only (one new table), so the previous image runs unchanged
against the migrated schema.

Revision ID: 0066
Revises: 0065
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa


revision = '0066'
down_revision = '0065'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'justice_appointments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('fjc_nid', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('last_key', sa.String(), nullable=False),
        sa.Column('first_initial', sa.String(), nullable=False),
        sa.Column('seat', sa.String(), nullable=False),
        sa.Column('president_id', sa.String(), nullable=True),
        sa.Column('nominated', sa.String(), nullable=False),
        sa.Column('served_from', sa.String(), nullable=False),
        sa.Column('served_until', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_justice_appointments_president_id'), 'justice_appointments', ['president_id'],
                    unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_justice_appointments_president_id'), table_name='justice_appointments')
    op.drop_table('justice_appointments')
