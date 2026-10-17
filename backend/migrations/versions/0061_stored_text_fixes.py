"""Rows written before two text fixes, rewritten to match them.

- The donor-vote connection's description used an em dash ("($455,257) —
  a substantial concentration"); policy_alignment.py writes a colon since
  #1059, but a member whose run fails keeps the rows of the last run that
  passed, so the old wording stayed on their profile.
- Comment links stored as http://www.regulations.gov/... (the Federal
  Register hands out some that way; fr_rulemaking.py has upgraded new ones
  to https since), which a refresh keeps when the Register sends none.

Data only, no schema change: the previous image reads the rewritten rows
the same way.

Revision ID: 0061
Revises: 0060
Create Date: 2026-10-17
"""
from alembic import op


revision = '0061'
down_revision = '0060'
branch_labels = None
depends_on = None

OLD_DASH = ") — a substantial concentration"
NEW_COLON = "): a substantial concentration"


def upgrade() -> None:
    for table in ("lobbying_matches", "rep_lobbying_matches"):
        op.execute(
            f"UPDATE {table} SET description = REPLACE(description, '{OLD_DASH}', '{NEW_COLON}') "
            f"WHERE description LIKE '%{OLD_DASH}%'"
        )
    op.execute(
        "UPDATE explore_documents SET comment_url = 'https://' || SUBSTR(comment_url, 8) "
        "WHERE comment_url LIKE 'http://%'"
    )


def downgrade() -> None:
    # The old wording and scheme were the defects; nothing to restore.
    pass
