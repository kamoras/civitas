"""presidents.approval_start: the term's opening approval level, which the
approval trend is judged against

One nullable column, so the previous image runs unchanged against the
migrated schema.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa


revision = '0028'
down_revision = '0027'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('presidents', sa.Column('approval_start', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('presidents', 'approval_start')
