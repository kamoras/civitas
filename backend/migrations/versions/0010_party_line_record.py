"""senators / representatives: the party-line record over the whole Congress

Nullable column only, so the previous image runs unchanged against the
migrated schema.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("senators", "representatives"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column("party_line_record", sa.Text(), nullable=True))


def downgrade() -> None:
    for table in ("senators", "representatives"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("party_line_record")
