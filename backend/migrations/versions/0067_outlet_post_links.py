"""Each outlet post the repost gate scored, with the article it links

Adds `outlet_post_links`: for every news outlet post the Action Center's
repost gate scores (pipeline/analyze/bluesky_engagement.py), its best
cosine, the issue it matched, the live issues, and the article its link
resolves to. The gate's daily refit labels them by those links. Empty
until the next hourly refresh.

Additive only (one new table), so the previous image runs unchanged
against the migrated schema.

Revision ID: 0067
Revises: 0066
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa


revision = '0067'
down_revision = '0066'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'outlet_post_links',
        sa.Column('uri', sa.String(), nullable=False),
        sa.Column('outlet', sa.String(), nullable=False),
        sa.Column('posted_at', sa.DateTime(), nullable=True),
        sa.Column('link', sa.Text(), nullable=True),
        sa.Column('article', sa.Text(), nullable=True),
        sa.Column('resolve_tries', sa.Integer(), server_default='0', nullable=False),
        sa.Column('best_sim', sa.Float(), nullable=False),
        sa.Column('issue_id', sa.Integer(), nullable=False),
        sa.Column('live_issue_ids', sa.Text(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('uri'),
    )


def downgrade() -> None:
    op.drop_table('outlet_post_links')
