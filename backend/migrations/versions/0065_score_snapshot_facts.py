"""Score snapshots: the four measures' figures on the day

Adds `score_snapshots.facts`, JSON of the measures a member page compares
with peers (small gifts, PAC share, breaks with party, bills past
committee) and the counts behind them, filled when a snapshot is inserted
(models._snapshot_facts), so history can show a measure over time instead
of a composite score's line. NULL for earlier snapshots and presidents.

Additive only (one nullable column), so the previous image runs unchanged
against the migrated schema.

Revision ID: 0065
Revises: 0064
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa


revision = '0065'
down_revision = '0064'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('score_snapshots') as batch_op:
        batch_op.add_column(sa.Column('facts', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('score_snapshots') as batch_op:
        batch_op.drop_column('facts')
