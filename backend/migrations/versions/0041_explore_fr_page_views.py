"""explore_documents.fr_page_views: the Federal Register's own count of
readers of a document (its API's page_views), refreshed nightly with the
other fields the Register changes after publication. Ranks the Action
Center's open comment periods by attention instead of deadline.

Nullable, so the previous image runs unchanged against the migrated schema.

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa


revision = '0041'
down_revision = '0040'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('explore_documents', sa.Column('fr_page_views', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('explore_documents', 'fr_page_views')
