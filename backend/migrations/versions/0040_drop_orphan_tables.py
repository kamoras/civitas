"""Drop three tables no image has mapped: alert_subscriptions, daily_themes,
member_analysis_fingerprints.

They exist only in databases old enough to have had them; no model, route
or query has named them since before the 0001 baseline (alert_subscriptions,
an email alert list: AGENTS.md §8 rules out a subscriber list), since the
LLM daily-theme system's removal (daily_themes), or since the incremental
analysis flag was retired (member_analysis_fingerprints). No running image
reads them, so dropping them needs no expand step. IF EXISTS: a database
built from the revisions never had them.

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-10
"""
from alembic import op


revision = '0040'
down_revision = '0039'
branch_labels = None
depends_on = None

ORPHAN_TABLES = ("alert_subscriptions", "daily_themes", "member_analysis_fingerprints")


def upgrade() -> None:
    for table in ORPHAN_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")


def downgrade() -> None:
    # Nothing to restore: no image maps them, and their rows were unused.
    pass
