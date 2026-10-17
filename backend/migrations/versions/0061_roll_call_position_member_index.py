"""Roll-call positions: index the member id

/congress's "your members" panel and the public API's per-member votes
read one member's positions at a time; without an index each read scanned
every stored position (California's 54 members took 4.5 s, 1.6 s with it).

Additive only (one index), so the previous image runs unchanged against
the migrated schema.

Revision ID: 0061
Revises: 0060
Create Date: 2026-10-17
"""
from alembic import op


revision = '0061'
down_revision = '0060'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(op.f('ix_roll_call_positions_member_id'), 'roll_call_positions', ['member_id'],
                    unique=False, if_not_exists=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_roll_call_positions_member_id'), table_name='roll_call_positions')
