"""A race's projection and when it settled

`race_results.projected` and `race_results.settled_at`
(app/live_results/sync.py, app/live_results/certified.py).

Adds nullable columns only. The previous image never reads them.

Revision ID: 0077
Revises: 0076 (0076_race_certifications)
Create Date: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa


revision = '0077'
down_revision = '0076'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('race_results') as batch:
        batch.add_column(sa.Column('projected', sa.Boolean(), nullable=True))
        batch.add_column(sa.Column('settled_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('race_results') as batch:
        batch.drop_column('settled_at')
        batch.drop_column('projected')
