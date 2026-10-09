"""justices.appointer_effect, appointer_effect_se: each justice's own
estimate of the appointing president's effect and its standard error,
shown and not scored (justice v3). The v2 score and shrunk estimate
(score_loyalty, loyalty, loyalty_se) stay for the image before.

Nullable columns, so the previous image runs unchanged against the
migrated schema.

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-09
"""
from alembic import op
import sqlalchemy as sa


revision = '0038'
down_revision = '0037'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('justices', sa.Column('appointer_effect', sa.Float(), nullable=True))
    op.add_column('justices', sa.Column('appointer_effect_se', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('justices', 'appointer_effect_se')
    op.drop_column('justices', 'appointer_effect')
