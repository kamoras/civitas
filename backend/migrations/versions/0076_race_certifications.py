"""Each race's certified count

`race_certifications`: a race's count as its state certified it, or the
member sworn in for the seat (app/live_results/certified.py).

Adds a table only. The previous image never reads it.

Revision ID: 0076
Revises: 0075 (0075_broadcast_deliveries)
Create Date: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa


revision = '0076'
down_revision = '0075'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'race_certifications',
        sa.Column('race_id', sa.String(), nullable=False),
        sa.Column('cycle', sa.Integer(), nullable=False),
        sa.Column('election_date', sa.String(length=10), nullable=False),
        sa.Column('state', sa.String(length=2), nullable=False),
        sa.Column('office', sa.String(length=1), nullable=False),
        sa.Column('district', sa.Integer(), nullable=True),
        sa.Column('basis', sa.String(length=10), nullable=False),
        sa.Column('certified_on', sa.String(length=10), nullable=True),
        sa.Column('certified_on_stated', sa.Boolean(), nullable=False),
        sa.Column('source_name', sa.String(), nullable=False),
        sa.Column('source_url', sa.String(), nullable=True),
        sa.Column('tallies', sa.Text(), nullable=False),
        sa.Column('votes_counted', sa.Integer(), nullable=True),
        sa.Column('read_at', sa.DateTime(), nullable=False),
        sa.Column('changed_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('race_id'),
    )
    with op.batch_alter_table('race_certifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_race_certifications_cycle'), ['cycle'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('race_certifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_race_certifications_cycle'))
    op.drop_table('race_certifications')
