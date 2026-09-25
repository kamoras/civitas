"""Alembic owns the main database's schema from the 0001 baseline on.

The check that matters is the first one: the schema the revision history
builds must equal the models. That is the check the hand-written
migrations could not have — a fresh database was built from the models,
so a model change without a matching migration (#220 and #611: a removed
column left NOT NULL; #592: a table create_all could not alter) was
invisible until it broke production. Now it fails here, in CI, when
someone changes a model without writing a revision.
"""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

import app.database as database
import app.models  # noqa: F401  (registers the tables on Base.metadata)
from app.database import Base


@pytest.fixture()
def patched_engine(monkeypatch):
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(database, "engine", eng)
    yield eng
    eng.dispose()


def _diff(eng):
    with eng.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        return compare_metadata(ctx, Base.metadata)


def _revision(eng):
    with eng.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _head():
    return ScriptDirectory.from_config(database._alembic_config()).get_current_head()


def test_the_revision_history_builds_exactly_the_models(patched_engine):
    database._run_migrations()
    diff = _diff(patched_engine)
    assert diff == [], (
        "The models and backend/migrations/ disagree. Write a revision for the "
        f"model change (backend/migrations/README.md): {diff}"
    )


def test_one_head():
    assert len(ScriptDirectory.from_config(database._alembic_config()).get_heads()) == 1


def test_upgrade_is_idempotent(patched_engine):
    database._run_migrations()
    database._run_migrations()
    assert _revision(patched_engine) == _head()


def test_a_pre_alembic_database_is_bridged_then_stamped(patched_engine):
    eng = patched_engine
    # A deployed database from before Alembic: built by create_all long ago,
    # so it lacks a newer column, still carries a removed NOT NULL one (the
    # #611 shape), and is missing a table added since.
    # Built at the baseline's shape (what a deployed pre-Alembic database
    # has), not from today's models, which later revisions have moved on.
    baseline = database._baseline_metadata()
    baseline.remove(baseline.tables["alembic_version"])
    baseline.create_all(bind=eng)
    with eng.begin() as conn:
        conn.execute(text("ALTER TABLE senators DROP COLUMN caucus_party"))
        conn.execute(text("ALTER TABLE senators DROP COLUMN committees"))
        conn.execute(text("ALTER TABLE representatives ADD COLUMN voting_summary TEXT NOT NULL DEFAULT ''"))
        conn.execute(text("DROP TABLE ballot_measures"))

    database._bridge_pre_alembic_schema()
    database._run_migrations()

    senators = {c["name"] for c in inspect(eng).get_columns("senators")}
    reps = {c["name"] for c in inspect(eng).get_columns("representatives")}
    assert {"caucus_party", "committees"} <= senators
    assert "voting_summary" not in reps
    # ...and the revisions after the baseline applied on top of the bridge.
    assert "score_constituent_alignment" in senators and "score_independent_voting" not in senators
    assert inspect(eng).has_table("ballot_measures")
    assert _revision(eng) == _head()


def test_the_drift_check_is_not_vacuous(patched_engine):
    # Guard the guard: a missing column must show up as a difference.
    database._run_migrations()
    with patched_engine.begin() as conn:
        conn.execute(text("ALTER TABLE senators DROP COLUMN caucus_party"))
    assert any(d[0] == "add_column" and d[3].name == "caucus_party" for d in _diff(patched_engine))
