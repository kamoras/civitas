"""congress activity: daily chamber record, events, roll calls, positions

New tables only, so the previous image runs unchanged against the
migrated schema.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-27
"""
from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "congress_days",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chamber", sa.String(6), nullable=False),
        sa.Column("date", sa.String(10), nullable=False),
        sa.Column("in_session", sa.Boolean(), nullable=False),
        sa.Column("adjournment_text", sa.Text(), nullable=False),
        sa.Column("convened_at", sa.String(16), nullable=True),
        sa.Column("adjourned_at", sa.String(16), nullable=True),
        sa.Column("next_meeting", sa.String(120), nullable=True),
        sa.Column("next_program", sa.Text(), nullable=False),
        sa.Column("bills_introduced", sa.Integer(), nullable=True),
        sa.Column("resolutions_introduced", sa.Integer(), nullable=True),
        sa.Column("introduced_text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("source_url", sa.String(500), nullable=False),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("chamber", "date", name="uq_congress_day_chamber_date"),
    )
    op.create_index("ix_congress_days_date", "congress_days", ["date"])
    op.create_table(
        "congress_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chamber", sa.String(6), nullable=False),
        sa.Column("date", sa.String(10), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("bill_id", sa.String(24), nullable=True),
        sa.Column("time", sa.String(16), nullable=True),
        sa.Column("pages", sa.String(60), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
    )
    op.create_index("ix_congress_event_day", "congress_events", ["chamber", "date"])
    op.create_index("ix_congress_events_bill_id", "congress_events", ["bill_id"])
    op.create_table(
        "roll_calls",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chamber", sa.String(6), nullable=False),
        sa.Column("congress", sa.Integer(), nullable=False),
        sa.Column("session", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("date", sa.String(10), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("result", sa.String(120), nullable=False),
        sa.Column("rejected", sa.Boolean(), nullable=True),
        sa.Column("majority_requirement", sa.String(10), nullable=False),
        sa.Column("yeas", sa.Integer(), nullable=False),
        sa.Column("nays", sa.Integer(), nullable=False),
        sa.Column("present", sa.Integer(), nullable=False),
        sa.Column("not_voting", sa.Integer(), nullable=False),
        sa.Column("bill_id", sa.String(24), nullable=True),
        sa.Column("source_url", sa.String(300), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("chamber", "congress", "session", "number", name="uq_roll_call"),
    )
    op.create_index("ix_roll_call_date", "roll_calls", ["date"])
    op.create_index("ix_roll_calls_bill_id", "roll_calls", ["bill_id"])
    op.create_table(
        "roll_call_positions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("roll_call_id", sa.Integer(), sa.ForeignKey("roll_calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.String(12), nullable=False),
        sa.Column("last_name", sa.String(80), nullable=False),
        sa.Column("first_name", sa.String(80), nullable=False),
        sa.Column("party", sa.String(2), nullable=False),
        sa.Column("state", sa.String(2), nullable=False),
        sa.Column("position", sa.String(12), nullable=False),
    )
    op.create_index("ix_roll_call_positions_roll_call_id", "roll_call_positions", ["roll_call_id"])


def downgrade() -> None:
    op.drop_table("roll_call_positions")
    op.drop_table("roll_calls")
    op.drop_table("congress_events")
    op.drop_table("congress_days")
