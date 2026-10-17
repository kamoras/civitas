"""Action Center issues: their Explore documents by permanent key

An issue stored its related documents and comment actions by Explore row
id, and rows get replaced (a provisional Federal Register number re-issued,
a re-read Record day, a data reset handing rebuilt documents old row ids),
so an archived issue's links broke or pointed elsewhere. Adds
`related_explore_keys` beside `related_explore_ids` and backfills it, and
each comment action's "docKey" beside its "docId", from the rows that hold
them now (services/explore_keys.public_key, restated here so the migration
never changes under it).

Additive only (one defaulted column), so the previous image runs unchanged
against the migrated schema.

Revision ID: 0059
Revises: 0058
Create Date: 2026-10-17
"""
import json
import re

from alembic import op
import sqlalchemy as sa


revision = '0059'
down_revision = '0058'
branch_labels = None
depends_on = None

_SPEECH_FORMAT_RE = re.compile(r"^crec-v\d+-")


def _key(external_id, row_id):
    if not external_id:
        return str(row_id)
    if external_id.startswith("fr-reg-"):
        return "fr-" + external_id[len("fr-reg-"):]
    return _SPEECH_FORMAT_RE.sub("crec-", external_id)


def _ids(raw):
    try:
        value = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, list) else None


def upgrade() -> None:
    with op.batch_alter_table('action_issues') as batch:
        batch.add_column(sa.Column('related_explore_keys', sa.Text(), server_default='[]', nullable=False))

    conn = op.get_bind()
    keys = {row_id: _key(ext, row_id) for row_id, ext in conn.execute(
        sa.text("SELECT id, external_id FROM explore_documents"))}
    for issue_id, related, actions in conn.execute(sa.text(
            "SELECT id, related_explore_ids, actions FROM action_issues")).fetchall():
        updates = {}
        ids = _ids(related)
        if ids:
            updates["related_explore_keys"] = json.dumps([keys[i] for i in ids if i in keys])
        acts = _ids(actions)
        if acts:
            changed = False
            for a in acts:
                if isinstance(a, dict) and a.get("type") == "comment_period" and a.get("docId") in keys \
                        and "docKey" not in a:
                    a["docKey"] = keys[a["docId"]]
                    changed = True
            if changed:
                updates["actions"] = json.dumps(acts)
        if updates:
            sets = ", ".join(f"{col} = :{col}" for col in updates)
            conn.execute(sa.text(f"UPDATE action_issues SET {sets} WHERE id = :id"), {**updates, "id": issue_id})


def downgrade() -> None:
    with op.batch_alter_table('action_issues') as batch:
        batch.drop_column('related_explore_keys')
