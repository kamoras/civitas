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


def test_revisions_form_one_numbered_chain():
    """_run_migrations tells a rollback from a stray revision by number, so
    the numbers must be one sequence, each revising the one before. Two
    branches that each took the next number fail here once both are merged
    (Alembic itself only warns about a duplicate id, keeping one file)."""
    import re
    from pathlib import Path

    script_dir = ScriptDirectory.from_config(database._alembic_config())
    files = sorted(Path(script_dir.versions).glob("[0-9]*.py"))
    ids = [re.search(r"^revision = ['\"](\w+)['\"]", f.read_text(), re.M).group(1) for f in files]
    assert ids == [f"{n:04d}" for n in range(1, len(files) + 1)]
    for script in script_dir.walk_revisions():
        expected = None if script.revision == "0001" else f"{int(script.revision) - 1:04d}"
        assert script.down_revision == expected, script.revision


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
    # Additive only: a column the previous image still reads is never
    # dropped or renamed in the release that stops using it (Swarm's
    # start-first update and automatic rollback run that image against this
    # schema). See migrations/README.md, "Expand, then contract".
    assert "score_independent_voting" in senators
    assert inspect(eng).has_table("ballot_measures")
    assert _revision(eng) == _head()


def test_the_drift_check_is_not_vacuous(patched_engine):
    # Guard the guard: a missing column must show up as a difference.
    database._run_migrations()
    with patched_engine.begin() as conn:
        conn.execute(text("ALTER TABLE senators DROP COLUMN caucus_party"))
    assert any(d[0] == "add_column" and d[3].name == "caucus_party" for d in _diff(patched_engine))


def test_a_database_migrated_by_a_newer_image_is_left_alone(patched_engine):
    """Swarm's automatic rollback starts the previous image against a
    database the failed new image already migrated. Alembic can't locate
    that newer revision; the older image must start anyway (the schema only
    ever expands between releases), not crash-loop the rollback."""
    database._run_migrations()
    newer = f"{int(_head()) + 1:04d}"
    with patched_engine.begin() as conn:
        conn.execute(text(f"UPDATE alembic_version SET version_num = '{newer}'"))

    database._run_migrations()  # must not raise

    assert _revision(patched_engine) == newer


def test_an_unknown_revision_that_is_not_a_later_one_still_fails_loudly(patched_engine):
    """Another branch's revision, a renamed file: not a rollback. Starting
    anyway would run the app against a schema nobody checked."""
    from alembic.util.exc import CommandError

    database._run_migrations()
    with patched_engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = 'a1b2c3d4e5f6'"))

    with pytest.raises(CommandError):
        database._run_migrations()


def test_an_owner_this_image_does_not_know_reads_as_unknown():
    """0006 writes owner 'unknown'; an image reading a row some other
    image's parser wrote must not fail the member's whole response on it."""
    from app.schemas import StockTradeSchema

    fields = dict(
        asset_name="A", transaction_type="purchase", transaction_date="2026-01-01",
        disclosure_date="2026-01-02", days_to_disclose=1, amount_low=1, amount_high=2,
        industry="X", source_url="",
    )
    assert StockTradeSchema(owner="unknown", **fields).owner == "unknown"
    assert StockTradeSchema(owner="trust", **fields).owner == "unknown"
    assert StockTradeSchema(owner="spouse", **fields).owner == "spouse"
