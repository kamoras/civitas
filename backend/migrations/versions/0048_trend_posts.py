"""Trend posts: the trend observations and audit tables, and the
broadcast_posts columns for a post's own image and its Bluesky engagement

Additive only (new tables, nullable columns), so the previous image runs
unchanged against the migrated schema.

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa


revision = '0048'
down_revision = '0047'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('broadcast_posts') as batch_op:
        batch_op.add_column(sa.Column('image_url', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('alt_text', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('embed_kind', sa.String(length=8), nullable=True))
        batch_op.add_column(sa.Column('bsky_uri', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('bsky_likes', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('bsky_reposts', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('bsky_replies', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('engagement_checked_at', sa.DateTime(), nullable=True))
    op.create_table(
        'trend_observations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('observed_at', sa.DateTime(), nullable=False),
        sa.Column('topic', sa.String(length=64), nullable=False),
        sa.Column('display_name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('category', sa.String(length=32), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('post_count', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_trend_observations_observed_at', 'trend_observations', ['observed_at'])
    op.create_index('ix_trend_observation_topic_seen', 'trend_observations', ['topic', 'observed_at'])
    op.create_table(
        'trend_candidates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('topic', sa.String(length=64), nullable=False),
        sa.Column('display_name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('category', sa.String(length=32), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('relevance', sa.Float(), nullable=False),
        sa.Column('tragedy', sa.Float(), nullable=False),
        sa.Column('political_violence', sa.Float(), nullable=False),
        sa.Column('loaded', sa.Float(), nullable=False),
        sa.Column('neutrality', sa.Text(), nullable=False),
        sa.Column('match_type', sa.String(length=10), nullable=True),
        sa.Column('match_strength', sa.Float(), nullable=True),
        sa.Column('record_kind', sa.String(length=10), nullable=True),
        sa.Column('record_key', sa.String(length=80), nullable=True),
        sa.Column('record_date', sa.String(length=10), nullable=True),
        sa.Column('record_url', sa.Text(), nullable=True),
        sa.Column('named_parties', sa.Text(), nullable=False),
        sa.Column('sponsor_parties', sa.Text(), nullable=False),
        sa.Column('selection_score', sa.Float(), nullable=True),
        sa.Column('decision', sa.String(length=10), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('broadcast_post_id', sa.Integer(), nullable=True),
        sa.Column('decided_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('topic'),
    )
    op.create_index('ix_trend_candidates_record_key', 'trend_candidates', ['record_key'])


def downgrade() -> None:
    op.drop_index('ix_trend_candidates_record_key', table_name='trend_candidates')
    op.drop_table('trend_candidates')
    op.drop_index('ix_trend_observation_topic_seen', table_name='trend_observations')
    op.drop_index('ix_trend_observations_observed_at', table_name='trend_observations')
    op.drop_table('trend_observations')
    with op.batch_alter_table('broadcast_posts') as batch_op:
        for col in ('engagement_checked_at', 'bsky_replies', 'bsky_reposts', 'bsky_likes',
                    'bsky_uri', 'embed_kind', 'alt_text', 'image_url'):
            batch_op.drop_column(col)
