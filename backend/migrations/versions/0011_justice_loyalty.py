"""justices: loyalty to the appointing president (the justice score, v2) and
Martin-Quinn positions

Nullable columns only, so the previous image runs unchanged against the
migrated schema.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_COLUMNS = [
    ("score_loyalty", sa.Float()), ("loyalty", sa.Float()), ("loyalty_se", sa.Float()),
    ("loyalty_votes_in", sa.Integer()), ("loyalty_votes_out", sa.Integer()),
    ("loyalty_rate_in", sa.Float()), ("loyalty_rate_out", sa.Float()),
    ("loyalty_through_term", sa.Integer()), ("ideal_points", sa.Text()),
]


def upgrade() -> None:
    with op.batch_alter_table("justices", schema=None) as batch_op:
        for name, type_ in _COLUMNS:
            batch_op.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("justices", schema=None) as batch_op:
        for name, _ in _COLUMNS:
            batch_op.drop_column(name)
