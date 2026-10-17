"""Roll calls: the amendment a vote was on and its purpose, verbatim

Additive only (four columns, nullable or defaulted), so the previous image runs unchanged
against the migrated schema.

Revision ID: 0058
Revises: 0057
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa


revision = '0058'
down_revision = '0057'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('roll_calls') as batch:
        batch.add_column(sa.Column('amendment', sa.String(length=24), nullable=True))
        batch.add_column(sa.Column('amendment_purpose', sa.Text(), nullable=True))
        batch.add_column(sa.Column('amendment_checked_at', sa.DateTime(), nullable=True))
        batch.add_column(sa.Column('amendment_misses', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('roll_calls') as batch:
        batch.drop_column('amendment_misses')
        batch.drop_column('amendment_checked_at')
        batch.drop_column('amendment_purpose')
        batch.drop_column('amendment')
