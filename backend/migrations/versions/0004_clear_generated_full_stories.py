"""clear model-written full stories

Every stored action_issues.full_story was prose a 1.2B model wrote from
the issue's facts. Checked only after the fact, it published a
relationship the sources never stated (issue 748), gave a House member
the wrong office (750) and was filler on a third (751). Full stories
are now verified claims grouped by outlet (claims.build_story), built
while the cluster's articles are in hand, and the old ones cannot be
rebuilt that way, since no article text is stored. So they are removed
rather than left up; the issue page shows none for those rows.

Data only, no schema change. Not reversible: the text is gone.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-27
"""
from alembic import op


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE action_issues SET full_story = NULL WHERE full_story IS NOT NULL")


def downgrade() -> None:
    pass
