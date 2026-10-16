"""feed_articles: the national-feed articles the Action Center's relevance
gate judged, with their publisher-filed labels (the gate's kNN reference)

Additive only (a new table), so the previous image runs unchanged against
the migrated schema.

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa


revision = '0052'
down_revision = '0051'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'feed_articles',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('url', sa.String(), nullable=False),
        sa.Column('source_name', sa.String(), nullable=False),
        sa.Column('scope', sa.String(length=16), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('names_official', sa.Boolean(), nullable=False),
        sa.Column('label', sa.Integer(), nullable=True),
        sa.Column('vote', sa.Float(), nullable=True),
        sa.Column('kept', sa.Boolean(), nullable=False),
        sa.Column('embedding', sa.LargeBinary(), nullable=False),
        sa.Column('model', sa.String(), nullable=False),
        sa.Column('published', sa.DateTime(), nullable=True),
        sa.Column('seen_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('feed_articles') as batch_op:
        batch_op.create_index(batch_op.f('ix_feed_articles_url'), ['url'], unique=True)
        batch_op.create_index(batch_op.f('ix_feed_articles_seen_at'), ['seen_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('feed_articles') as batch_op:
        batch_op.drop_index(batch_op.f('ix_feed_articles_seen_at'))
        batch_op.drop_index(batch_op.f('ix_feed_articles_url'))
    op.drop_table('feed_articles')
