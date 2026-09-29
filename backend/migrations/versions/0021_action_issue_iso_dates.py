"""action_issues: every date in ISO form

A developing issue drafted from a Senate roll call stored the vote's own
date string ("September 28, 2026,  09:42 PM") as its date, which the
Action Center sorts as text to find "the latest day": that string sorted
after every ISO date, and the page showed that one issue alone
(2026-09-29). early_signal now stores the refresh's date. Rows already
stored are rewritten to the ISO date they name; one that names none is
left for the refresh to retire.

Data only, so the previous image runs unchanged against the result.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-29
"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def _iso(text: str | None) -> str | None:
    words = " ".join((text or "").split())
    parts = words.split(",")
    if len(parts) >= 2:
        try:
            return datetime.strptime(f"{parts[0].strip()}, {parts[1].strip()}", "%B %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


def upgrade() -> None:
    bind = op.get_bind()
    for column in ("date", "primary_article_date"):
        rows = bind.execute(sa.text(
            f"SELECT id, {column} FROM action_issues "
            f"WHERE {column} IS NOT NULL AND {column} NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'"
        )).fetchall()
        for issue_id, value in rows:
            if iso := _iso(value):
                bind.execute(sa.text(f"UPDATE action_issues SET {column} = :v WHERE id = :id"), {"v": iso, "id": issue_id})


def downgrade() -> None:
    pass  # the rewritten dates are the correct ones
