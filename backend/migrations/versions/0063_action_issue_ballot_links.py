"""Action Center issues: the states and races they name

Adds `action_issues.ballot_links`, the states and races an issue's own
words name (a nominee by name, a seat phrase), matched when the hourly refresh writes the issue and served with
the state's voter deadlines and official links. NULL until the refresh
links a row.

Additive only (one nullable column), so the previous image runs unchanged
against the migrated schema.

Revision ID: 0063
Revises: 0062
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa


revision = '0063'
down_revision = '0062'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('action_issues') as batch_op:
        batch_op.add_column(sa.Column('ballot_links', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('action_issues') as batch_op:
        batch_op.drop_column('ballot_links')
