"""Rename score_independent_voting to score_constituent_alignment.

The dimension has been Constituent Alignment since v4.2; the column kept
the old name "for compatibility", which every reader then had to be
told. Renamed in place (SQLite >= 3.25 RENAME COLUMN via batch mode), so
stored scores are kept.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-25
"""
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

_TABLES = ("senators", "representatives")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column("score_independent_voting", new_column_name="score_constituent_alignment")


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column("score_constituent_alignment", new_column_name="score_independent_voting")
