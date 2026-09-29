"""nominee party_label: a certified list's party as printed

A certified November list can name a party the shared party codes do not
(Vermont's Freedom and Unity, Peace and Justice; South Carolina's
Workers). Those rows were dropped. They are now stored under the neutral
code "O" (state_candidates_common.OTHER_PARTY) with the party exactly as
the state printed it in this column.

Expand only: three nullable columns. An image without them never reads
them, and an "O" row renders as its code there.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-28
"""
import sqlalchemy as sa
from alembic import op


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_TABLES = ("statewide_nominees", "state_leg_nominees", "judicial_nominees")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("party_label", sa.String(length=80), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column("party_label")
