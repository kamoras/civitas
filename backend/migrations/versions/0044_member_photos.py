"""member_photos: the official photo each member's /photo/bioguide/<id>
serves, chosen nightly by face size (pipeline/fetch/member_photos.py)

A new table, so the previous image runs unchanged against the migrated
schema (it never reads it).

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-13
"""
from alembic import op
import sqlalchemy as sa


revision = '0044'
down_revision = '0043'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'member_photos',
        sa.Column('bioguide_id', sa.String(length=7), nullable=False),
        sa.Column('source_url', sa.String(length=300), nullable=False),
        sa.Column('depiction_url', sa.String(length=300), nullable=False),
        sa.Column('bioguide_face_share', sa.Float(), nullable=True),
        sa.Column('depiction_face_share', sa.Float(), nullable=True),
        sa.Column('checked_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('bioguide_id'),
    )


def downgrade() -> None:
    op.drop_table('member_photos')
