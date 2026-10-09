"""presidents.approval_own_party / approval_other_party /
approval_independents / term_polarization: a president's average approval
in each party group and the House party distance over the term, which
Public Mandate compares within an era (president v9)

Four nullable columns, so the previous image runs unchanged against the
migrated schema.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa


revision = '0032'
down_revision = '0031'
branch_labels = None
depends_on = None

_COLUMNS = ('approval_own_party', 'approval_other_party', 'approval_independents', 'term_polarization')


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column('presidents', sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column('presidents', name)
