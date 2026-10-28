"""Which hosts each pipeline step read

`source_read_stats`: per (run kind, run id, step, host), how many of the
step's requests the host answered and how many failed, and whether the
step failed on the host
(app/source_reads.py, written by ProgressTracker), for the source map
(app/source_health.py).

Adds a table only. The previous image never reads it.

Revision ID: 0074
Revises: 0073 (0073_source_failure_counts)
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa


revision = '0074'
down_revision = '0073'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'source_read_stats',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('run_kind', sa.String(length=64), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('step_key', sa.String(length=64), nullable=False),
        sa.Column('host', sa.String(length=128), nullable=False),
        sa.Column('answered', sa.Integer(), nullable=False),
        sa.Column('failed', sa.Integer(), nullable=False),
        sa.Column('failed_on_host', sa.Boolean(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_kind', 'run_id', 'step_key', 'host', name='uq_source_read_stat_run_step_host'),
    )
    with op.batch_alter_table('source_read_stats', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_source_read_stats_run_kind'), ['run_kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_source_read_stats_host'), ['host'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('source_read_stats', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_source_read_stats_host'))
        batch_op.drop_index(batch_op.f('ix_source_read_stats_run_kind'))
    op.drop_table('source_read_stats')
