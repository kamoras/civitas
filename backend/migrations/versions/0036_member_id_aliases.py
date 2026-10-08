"""member_id_aliases: every renamed member id and the id it became, so
/politicians/<old_id> and the API keep answering URLs already posted and
indexed (app/member_ids.py)

A new table only, so the previous image runs unchanged against the
migrated schema (it never reads it).

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa


revision = '0036'
down_revision = '0035'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'member_id_aliases',
        sa.Column('old_id', sa.String(), nullable=False),
        sa.Column('new_id', sa.String(), nullable=False),
        sa.Column('bioguide_id', sa.String(), nullable=True),
        sa.Column('renamed_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('old_id'),
    )
    op.create_index(op.f('ix_member_id_aliases_new_id'), 'member_id_aliases', ['new_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_member_id_aliases_new_id'), table_name='member_id_aliases')
    op.drop_table('member_id_aliases')
