"""contract: drop long-unread columns; release the unmapped justice columns

Two kinds of cleanup, each safe for the image this one replaces (and for a
rollback to it):

- Dropped: columns no model has mapped since v6.13 (outside spending, the
  opposing-party unity figure, GDP-adjusted growth). They exist only in
  databases created before Alembic, so each is dropped where present.
- Made nullable: the four justice sub-scores no longer scored (v6.13 and
  justice v2) and the candidate Bluesky-search watermark. This image stops
  mapping them, so its inserts leave them out, which a NOT NULL column
  would refuse. The previous image still maps them and keeps working.
  The release after this one drops them (migrations/README.md).

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-29
"""
import sqlalchemy as sa
from alembic import op


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

_DROP = [
    ("senators", "outside_spending_for"),
    ("representatives", "outside_spending_for"),
    ("key_votes", "opposing_party_unity_pct"),
    ("rep_key_votes", "opposing_party_unity_pct"),
    ("presidents", "gdp_growth_adjusted"),
]

_RELEASE = ("score_consistency", "score_independence", "score_bipartisan_agreement", "score_judicial_restraint")


def _columns(table: str) -> dict[str, dict]:
    return {c["name"]: c for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table, column in _DROP:
        if column in _columns(table):
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_column(column)
    present = _columns("justices")
    held = [c for c in _RELEASE if c in present and not present[c]["nullable"]]
    if held:
        with op.batch_alter_table("justices", schema=None) as batch_op:
            for column in held:
                batch_op.alter_column(column, existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    # The dropped columns held nothing any image reads; they are not
    # recreated. The justice columns go back to NOT NULL only if every row
    # has a value.
    pass
