"""Pipeline runs: error_detail, the exception and bounded traceback behind
a failed run or step (error_utils.record_exception)

Additive only (a nullable column per run table), so the previous image runs
unchanged against the migrated schema.

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa


revision = '0049'
down_revision = '0048'
branch_labels = None
depends_on = None

TABLES = (
    'pipeline_runs',
    'house_pipeline_runs',
    'supplementary_pipeline_runs',
    'stock_trades_pipeline_runs',
    'election_pipeline_runs',
)


def upgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column('error_detail', sa.Text(), nullable=True))


def downgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column('error_detail')
