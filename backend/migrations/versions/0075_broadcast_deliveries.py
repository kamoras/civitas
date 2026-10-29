"""Each post's delivery to Threads and Instagram

`broadcast_deliveries`: one row per (post, channel) past Bluesky, with its
status, tries, the platform's post id and the last failure
(app/broadcast.py, app/social/).

Adds a table only. The previous image never reads it.

Revision ID: 0075
Revises: 0074 (0074_source_read_stats)
Create Date: 2026-10-29
"""
from alembic import op
import sqlalchemy as sa


revision = '0075'
down_revision = '0074'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'broadcast_deliveries',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('channel', sa.String(length=12), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_attempt_at', sa.DateTime(), nullable=True),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.Column('remote_id', sa.String(length=64), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['post_id'], ['broadcast_posts.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('post_id', 'channel', name='uq_broadcast_delivery_post_channel'),
    )
    with op.batch_alter_table('broadcast_deliveries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_broadcast_deliveries_post_id'), ['post_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('broadcast_deliveries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_broadcast_deliveries_post_id'))
    op.drop_table('broadcast_deliveries')
