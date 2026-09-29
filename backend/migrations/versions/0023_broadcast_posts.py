"""broadcast_posts: every post Civitas publishes, as the Atom feed's entries

A post used to exist only as a Bluesky side effect. It is now stored first
(app/broadcast.py) and served at /feed.xml; Bluesky delivers from the row.

A new table only, so the previous image runs unchanged against the result.

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-29
"""
from alembic import op
import sqlalchemy as sa


revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broadcast_posts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("subject", sa.String(length=80), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=2), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("bsky_status", sa.String(length=10), server_default="off", nullable=False),
        sa.Column("bsky_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("bsky_last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("bsky_sent_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # Ids are the feed entries' ids and must never be reused.
        sqlite_autoincrement=True,
    )
    with op.batch_alter_table("broadcast_posts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_broadcast_posts_kind"), ["kind"], unique=False)
        batch_op.create_index(batch_op.f("ix_broadcast_posts_published_at"), ["published_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_broadcast_posts_subject"), ["subject"], unique=False)
        batch_op.create_index(batch_op.f("ix_broadcast_posts_source_url"), ["source_url"], unique=False)
        batch_op.create_index(batch_op.f("ix_broadcast_posts_state"), ["state"], unique=False)


def downgrade() -> None:
    op.drop_table("broadcast_posts")
