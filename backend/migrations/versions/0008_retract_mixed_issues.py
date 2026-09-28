"""retract two issues built from unrelated stories

Issues 753 (i612cf1a1) and 759 (i1679cbc7) were assembled by the
single-linkage clustering fixed in PR #659: 759 joined floods in Bangkok,
a Hawaii hurricane, a nor'easter and an HIV emergency in Fiji; 753 took
its headline from an unrelated article and cited unrelated sources. No
article text is stored, so they cannot be rebuilt, and are removed rather
than left up. Their pages answer 410 with the reason from
app/data/retractions.json, and their Bluesky posts are deleted by the
retraction job. The day's timeline entry that carried 753's mixed-up
headline is removed too; the next refresh records the day's top issue
again.

Data only, no schema change. Not reversible: the rows are gone.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-27
"""
from alembic import op


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM action_issues WHERE id IN (753, 759)")
    op.execute(
        "DELETE FROM timeline_entries WHERE date = '2026-09-27' "
        "AND title = 'Young people in China are fangirling over a professor who gets their anxiety'"
    )


def downgrade() -> None:
    pass
