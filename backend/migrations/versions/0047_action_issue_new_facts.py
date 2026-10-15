"""action_issues.new_facts: the facts new since previous_facts, stored when
the facts are written so the API never loads the embedding model to diff
them (app/fact_diff.py)

A nullable column, so the previous image runs unchanged against the
migrated schema (it never reads it).

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa


revision = '0047'
down_revision = '0046'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('action_issues') as batch_op:
        batch_op.add_column(sa.Column('new_facts', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('action_issues') as batch_op:
        batch_op.drop_column('new_facts')
