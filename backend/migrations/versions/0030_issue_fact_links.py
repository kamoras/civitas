"""action_issues: the article each fact was quoted from, and the summary's
outlet and article

Three nullable columns, so the previous image runs unchanged against the
migrated schema. Existing issues get their facts' article links where the
outlet a fact names published exactly one of the issue's sources; the
summary's outlet was never stored, so it stays empty for them.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-01
"""
import json

from alembic import op
import sqlalchemy as sa


revision = '0030'
down_revision = '0029'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('action_issues', sa.Column('fact_source_urls', sa.Text(), nullable=True))
    op.add_column('action_issues', sa.Column('summary_source', sa.String(), nullable=True))
    op.add_column('action_issues', sa.Column('summary_source_url', sa.String(), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, fact_sources, source_names, source_urls FROM action_issues"
    )).fetchall()
    for issue_id, fact_sources, source_names, source_urls in rows:
        try:
            facts = json.loads(fact_sources or "[]")
            names = json.loads(source_names or "[]")
            urls = json.loads(source_urls or "[]")
        except (TypeError, ValueError):
            continue
        if not facts or len(names) != len(urls):
            continue
        by_name: dict[str, list[str]] = {}
        for name, url in zip(names, urls):
            by_name.setdefault(name, []).append(url)
        links = [(by_name.get(name) or [""])[0] if len(by_name.get(name) or []) == 1 else "" for name in facts]
        if any(links):
            conn.execute(
                sa.text("UPDATE action_issues SET fact_source_urls = :links WHERE id = :id"),
                {"links": json.dumps(links), "id": issue_id},
            )


def downgrade() -> None:
    op.drop_column('action_issues', 'summary_source_url')
    op.drop_column('action_issues', 'summary_source')
    op.drop_column('action_issues', 'fact_source_urls')
