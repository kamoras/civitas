"""The Federal Register's table-of-contents subject on each document, which
tells a Paperwork Reduction Act notice from other notices
(app/comment_periods.py). Expand only: a new nullable column.

Revision ID: 0060
Revises: 0059
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa


revision = '0060'
down_revision = '0059'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('explore_documents') as batch:
        batch.add_column(sa.Column('fr_toc_subject', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('explore_documents') as batch:
        batch.drop_column('fr_toc_subject')
