"""senators/representatives.small_donor_percentage nullable: NULL is a
campaign whose filings report no unitemized money (it itemizes every gift),
so its small-donor share can't be read from them; 0 claimed none.

Only relaxes NOT NULL, so the previous image, which always writes a number,
runs unchanged against the migrated schema.

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa


revision = '0035'
down_revision = '0034'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ('senators', 'representatives'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column('small_donor_percentage', existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    for table in ('senators', 'representatives'):
        op.execute(f"UPDATE {table} SET small_donor_percentage = 0 WHERE small_donor_percentage IS NULL")
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column('small_donor_percentage', existing_type=sa.Float(), nullable=False)
