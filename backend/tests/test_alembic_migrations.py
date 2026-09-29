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


# Columns this image no longer maps but the database keeps, because the
# image before it still reads them (migrations/README.md, "expand, then
# contract"). The next release's revision drops each and removes it here.
UNMAPPED_PENDING_DROP = {
    ("justices", "score_consistency"),
    ("justices", "score_independence"),
    ("justices", "score_bipartisan_agreement"),
    ("justices", "score_judicial_restraint"),
    ("candidates", "last_coverage_search"),
}


def _diff(eng):
    with eng.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        return [
            d for d in compare_metadata(ctx, Base.metadata)
            if not (d[0] == "remove_column" and (d[2], d[3].name) in UNMAPPED_PENDING_DROP)
        ]


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


def test_0006_stops_guessing_ocr_owners_and_versions_senate_trades(patched_engine):
    database._run_migrations("0005")
    with patched_engine.begin() as conn:
        # No senators row needed: SQLite runs here without foreign-key enforcement.
        for n, (confidence, owner) in enumerate([("ocr", "self"), ("text", "self"), ("ocr", "spouse")]):
            conn.execute(text(
                "INSERT INTO stock_trades (senator_id, asset_name, owner, transaction_type, transaction_date, "
                "disclosure_date, days_to_disclose, amount_low, amount_high, industry, source_url, filing_id, "
                "parse_confidence) "
                f"VALUES ('S1', 'A', '{owner}', 'purchase', '2026-01-01', '2026-01-02', 1, 0, 0, 'X', '', "
                f"'f{n}', '{confidence}')"
            ))

    database._run_migrations("0006")

    with patched_engine.connect() as conn:
        rows = conn.execute(text("SELECT parse_confidence, owner, parser_version FROM stock_trades ORDER BY id")).all()
    # OCR rows now read exactly as version 2 reads them, so they are current.
    assert [tuple(r) for r in rows] == [("ocr", "unknown", 2), ("text", "self", 1), ("ocr", "spouse", 2)]


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


def test_a_database_that_ran_the_justice_change_as_0012_converges(patched_engine):
    # Two branches each merged a "0012" (action_issues.duplicate_of_id and
    # the justice loyalty columns); prod ran the justice one under that
    # number. Stamped 0012 but without the other's column, the upgrade must
    # still end at the models' schema.
    eng = patched_engine
    database._run_migrations("0011")
    with eng.begin() as conn:
        for name, kind in (("score_loyalty", "FLOAT"), ("loyalty", "FLOAT"), ("loyalty_se", "FLOAT"),
                           ("loyalty_votes_in", "INTEGER"), ("loyalty_votes_out", "INTEGER"),
                           ("loyalty_rate_in", "FLOAT"), ("loyalty_rate_out", "FLOAT"),
                           ("loyalty_through_term", "INTEGER"), ("ideal_points", "TEXT")):
            conn.execute(text(f"ALTER TABLE justices ADD COLUMN {name} {kind}"))
        conn.execute(text("UPDATE alembic_version SET version_num = '0012'"))
    database._run_migrations()
    assert _revision(eng) == _head()
    assert "duplicate_of_id" in {c["name"] for c in inspect(eng).get_columns("action_issues")}
    assert "score_loyalty" in {c["name"] for c in inspect(eng).get_columns("justices")}


def test_a_database_that_ran_the_sworn_date_change_as_0016_converges(patched_engine):
    # Two branches each merged a "0016" (financial_disclosures.president_id
    # and representatives.sworn_date). Stamped 0016 having run only the
    # sworn-date one, the upgrade must still end at the models' schema.
    eng = patched_engine
    database._run_migrations("0015")
    with eng.begin() as conn:
        conn.execute(text("ALTER TABLE representatives ADD COLUMN sworn_date VARCHAR(10)"))
        conn.execute(text("UPDATE alembic_version SET version_num = '0016'"))
    database._run_migrations()
    assert _revision(eng) == _head()
    assert "president_id" in {c["name"] for c in inspect(eng).get_columns("financial_disclosures")}
    assert "sworn_date" in {c["name"] for c in inspect(eng).get_columns("representatives")}
    assert _diff(eng) == []


def test_every_pending_drop_is_still_there_and_nullable(patched_engine):
    # An entry the database no longer has is stale; one still NOT NULL would
    # refuse this image's inserts, which leave it out.
    database._run_migrations()
    insp = inspect(patched_engine)
    for table, column in UNMAPPED_PENDING_DROP:
        cols = {c["name"]: c for c in insp.get_columns(table)}
        assert column in cols, f"{table}.{column} is gone: remove it from UNMAPPED_PENDING_DROP"
        assert cols[column]["nullable"], f"{table}.{column} is NOT NULL but no longer mapped"


def test_a_new_justice_inserts_once_the_unscored_columns_are_released(patched_engine):
    from sqlalchemy.orm import Session

    from app.models import Justice

    database._run_migrations()
    with Session(patched_engine) as s:
        s.add(Justice(id="new", name="New Justice", last_name="Justice",
                      appointing_president="X", appointing_party="D"))
        s.commit()
        assert s.query(Justice).count() == 1


def test_0018_drops_the_long_unread_columns_where_a_bridged_database_has_them(patched_engine):
    eng = patched_engine
    database._run_migrations("0017")
    with eng.begin() as conn:
        conn.execute(text("ALTER TABLE senators ADD COLUMN outside_spending_for FLOAT"))
        conn.execute(text("ALTER TABLE presidents ADD COLUMN gdp_growth_adjusted FLOAT"))
    database._run_migrations()
    assert "outside_spending_for" not in {c["name"] for c in inspect(eng).get_columns("senators")}
    assert "gdp_growth_adjusted" not in {c["name"] for c in inspect(eng).get_columns("presidents")}
    assert _diff(eng) == []
