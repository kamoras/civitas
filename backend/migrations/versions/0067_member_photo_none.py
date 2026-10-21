"""A member with no official photo

`member_photos.source_url` and `depiction_url` become nullable: a row with
no source records that neither Congress.gov nor bioguide has a photo of the
member, so the API sends no photo path; one with no depiction records a
member whose record credits none (bioguide's portrait is served).

Loosens two NOT NULL constraints only. The previous image never writes a
null and reads a null source as "no stored choice", so it runs unchanged
against the migrated schema.

Revision ID: 0067
Revises: 0066
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa


revision = '0067'
down_revision = '0066'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('member_photos', schema=None) as batch_op:
        batch_op.alter_column('source_url', existing_type=sa.String(length=300), nullable=True)
        batch_op.alter_column('depiction_url', existing_type=sa.String(length=300), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM member_photos WHERE source_url IS NULL OR depiction_url IS NULL")
    with op.batch_alter_table('member_photos', schema=None) as batch_op:
        batch_op.alter_column('depiction_url', existing_type=sa.String(length=300), nullable=False)
        batch_op.alter_column('source_url', existing_type=sa.String(length=300), nullable=False)
