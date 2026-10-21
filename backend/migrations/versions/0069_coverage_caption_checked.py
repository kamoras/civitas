"""Race coverage: when a summary was verified caption-free

`race_coverage_items.caption_checked_at` records when an item's summary was
last known not to open with a photo caption (fetch/coverage_captions.py).
NULL, every row stored before this, is "not yet verified".

Adds a nullable column only. The previous image never reads it and its
inserts leave it NULL, which the nightly re-check reads as unverified.

Revision ID: 0069
Revises: 0068 (0068_outlet_post_links, PR #1188)
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa


revision = '0069'
down_revision = '0068'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('race_coverage_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('caption_checked_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('race_coverage_items', schema=None) as batch_op:
        batch_op.drop_column('caption_checked_at')
