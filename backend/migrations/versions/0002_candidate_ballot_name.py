"""candidate ballot name

The name a state prints for a candidate on its ballot ("Roy Cooper"),
beside the FEC's (`name`, "COOPER, ROY"). Nullable, so the previous image
keeps working against the migrated schema (expand, then contract).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26
"""
from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("candidates", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ballot_name", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("candidates", schema=None) as batch_op:
        batch_op.drop_column("ballot_name")
