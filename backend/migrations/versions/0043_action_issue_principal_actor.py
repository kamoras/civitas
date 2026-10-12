"""action_issues.principal_actor: who the story is principally about
(pipeline/analyze/story_actor.py), for the daily actor cap and the admin's
weekly actor concentration

A nullable column, so the previous image runs unchanged against the migrated
schema; the refresh labels existing rows on its next run.

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-12
"""
from alembic import op
import sqlalchemy as sa


revision = '0043'
down_revision = '0042'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('action_issues', sa.Column('principal_actor', sa.String(length=60), nullable=True))


def downgrade() -> None:
    op.drop_column('action_issues', 'principal_actor')
