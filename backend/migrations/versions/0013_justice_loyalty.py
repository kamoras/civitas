"""justices: loyalty to the appointing president (the justice score, v2) and
Martin-Quinn positions

Nullable columns only, so the previous image runs unchanged against the
migrated schema.

Merged as a second "0012" beside 0012_action_issue_duplicate_of (both
branches took the next number), and deployed under that number, so a
database stamped 0012 may hold either change. Each column is added only if
missing, action_issues.duplicate_of_id included, so every database ends at
the same schema whichever 0012 it ran.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

_COLUMNS = [
    ("score_loyalty", sa.Float()), ("loyalty", sa.Float()), ("loyalty_se", sa.Float()),
    ("loyalty_votes_in", sa.Integer()), ("loyalty_votes_out", sa.Integer()),
    ("loyalty_rate_in", sa.Float()), ("loyalty_rate_out", sa.Float()),
    ("loyalty_through_term", sa.Integer()), ("ideal_points", sa.Text()),
]


def _add_missing(table: str, columns: list[tuple[str, sa.types.TypeEngine]]) -> None:
    present = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
    missing = [(name, type_) for name, type_ in columns if name not in present]
    if not missing:
        return
    with op.batch_alter_table(table, schema=None) as batch_op:
        for name, type_ in missing:
            batch_op.add_column(sa.Column(name, type_, nullable=True))


def upgrade() -> None:
    _add_missing("justices", _COLUMNS)
    # The other 0012's column, for a database that ran this revision as 0012.
    _add_missing("action_issues", [("duplicate_of_id", sa.Integer())])


def downgrade() -> None:
    with op.batch_alter_table("justices", schema=None) as batch_op:
        for name, _ in _COLUMNS:
            batch_op.drop_column(name)
