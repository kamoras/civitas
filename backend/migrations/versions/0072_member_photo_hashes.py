"""Member photos: each measured image's sha256

`member_photos.bioguide_sha256` and `depiction_sha256` record which image
each face share was measured on, so the photo check's daily refit
(scripts/calibrate_member_photos.py) scores itself only on hand labels whose
images are still the ones judged. NULL until the member's next measurement.

Adds nullable columns only. The previous image never reads them and its
writes leave them as they are.

Revision ID: 0072
Revises: 0071 (0071_funding_election_year)
Create Date: 2026-10-23
"""
from alembic import op
import sqlalchemy as sa


revision = '0072'
down_revision = '0071'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('member_photos', schema=None) as batch_op:
        batch_op.add_column(sa.Column('bioguide_sha256', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('depiction_sha256', sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('member_photos', schema=None) as batch_op:
        batch_op.drop_column('depiction_sha256')
        batch_op.drop_column('bioguide_sha256')
