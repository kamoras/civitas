"""broadcast_posts: the linked page's card (image, alt text, description)

Kept so the Atom feed entry can carry the same picture and description the
Bluesky link card shows (app/broadcast.py, api/feed.py).

New nullable columns only, so the previous image runs unchanged against the
result.

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-29
"""
from alembic import op
import sqlalchemy as sa


revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("broadcast_posts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("card_image", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("card_image_alt", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("card_description", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("broadcast_posts", schema=None) as batch_op:
        batch_op.drop_column("card_description")
        batch_op.drop_column("card_image_alt")
        batch_op.drop_column("card_image")
