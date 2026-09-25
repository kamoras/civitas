"""Alembic environment for the main database (app.database.Base).

init_db runs migrations programmatically and hands its own connection in
through config.attributes["connection"], so the upgrade happens inside
init_db's cross-process lock. The CLI (`alembic -c alembic.ini ...`) falls
back to app.database.engine. The visits database is not managed here; see
migrations/README.md.
"""

from alembic import context

from app import models  # noqa: F401 — registers every table on Base.metadata
from app.database import Base

target_metadata = Base.metadata


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite has no ALTER COLUMN / DROP CONSTRAINT; batch mode rebuilds
        # the table instead — the operation #592 had to hand-roll.
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("offline (--sql) migrations are not supported; run against a database")

connection = context.config.attributes.get("connection")
if connection is not None:
    _run(connection)
else:
    from app.database import engine

    with engine.begin() as conn:
        _run(conn)
