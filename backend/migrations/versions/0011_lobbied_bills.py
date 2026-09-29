"""lobbying matches: bills named in LDA filings, spend by client, lookup status

Three nullable columns on each match table, so the previous image runs
unchanged against the migrated schema (it never reads them).

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-27
"""
from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_TABLES = ("lobbying_matches", "rep_lobbying_matches")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("lobbied_bills", sa.Text(), nullable=True))
        op.add_column(table, sa.Column("lobbying_checked", sa.Boolean(), nullable=True))
        op.add_column(table, sa.Column("lobbying_clients", sa.Text(), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column("lobbying_clients")
            batch.drop_column("lobbying_checked")
            batch.drop_column("lobbied_bills")
