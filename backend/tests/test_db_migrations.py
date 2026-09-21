"""Tests for the hand-rolled column migrations in database._migrate_columns.

create_all() only ever builds the *current* ORM schema, so the standard
db_session fixture can never exercise the ADD/DROP COLUMN path — which is
exactly the path that took production down in the #220 president incident
(a DROP COLUMN that was missing, crash-looping startup). These tests build
a table at an *old* schema with raw SQL, run the migration against it, and
assert the columns move and the legacy rows survive.

_migrate_columns references the module-global `engine`, so each test
monkeypatches it to a StaticPool in-memory engine (one shared connection,
so the raw CREATE, the migration, and the assertions all see one DB).
"""

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

import app.database as database
import app.models  # noqa: F401  (registers the tables on Base.metadata)


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


def test_add_column_populates_default_and_preserves_row(patched_engine):
    eng = patched_engine
    # Legacy sponsored_bills table predating the `stage` column.
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE sponsored_bills (id INTEGER PRIMARY KEY, senator_id TEXT)"
        ))
        conn.execute(text(
            "INSERT INTO sponsored_bills (id, senator_id) VALUES (1, 'S1')"
        ))

    database._migrate_columns()

    cols = {c["name"] for c in inspect(eng).get_columns("sponsored_bills")}
    assert "stage" in cols
    with eng.begin() as conn:
        row = conn.execute(
            text("SELECT id, senator_id, stage FROM sponsored_bills WHERE id = 1")
        ).fetchone()
    assert row.senator_id == "S1"      # legacy row survived
    assert row.stage == ""             # DEFAULT '' applied to the existing row


def test_drops_legacy_president_columns_and_keeps_data(patched_engine):
    eng = patched_engine
    # The exact shape behind the #220 crash-loop: presidents still carrying
    # the retired score_independence / score_follow_through columns.
    # score_competence joined the retired list in #218 (Competence removed
    # as a dimension), so it's now asserted dropped too; avg_approval
    # stands in as the "unrelated data survives" column instead.
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE presidents ("
            " id TEXT PRIMARY KEY, score_independence REAL,"
            " score_follow_through REAL, score_competence REAL,"
            " avg_approval REAL)"
        ))
        conn.execute(text(
            "INSERT INTO presidents"
            " (id, score_independence, score_follow_through, score_competence, avg_approval)"
            " VALUES ('p1', 10, 20, 60, 47)"
        ))

    database._migrate_columns()

    cols = {c["name"] for c in inspect(eng).get_columns("presidents")}
    assert "score_independence" not in cols
    assert "score_follow_through" not in cols
    assert "score_competence" not in cols
    with eng.begin() as conn:
        row = conn.execute(
            text("SELECT id, avg_approval FROM presidents WHERE id = 'p1'")
        ).fetchone()
    assert row.avg_approval == 47  # non-dropped data untouched


def test_absent_tables_are_skipped_not_errored(patched_engine):
    # No tables at all — every addition/drop targets a missing table, so
    # the migration must be a clean no-op rather than raising.
    database._migrate_columns()  # should not raise


def test_migration_is_idempotent(patched_engine):
    eng = patched_engine
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE sponsored_bills (id INTEGER PRIMARY KEY, senator_id TEXT)"
        ))
    database._migrate_columns()
    # Running it again against the now-current schema must not re-add or fail.
    database._migrate_columns()
    cols = {c["name"] for c in inspect(eng).get_columns("sponsored_bills")}
    assert "stage" in cols


def _legacy_action_issues(eng, rows: str) -> None:
    """Create action_issues at the pre-#310 schema (no bsky_posted_facts)
    and insert `rows`."""
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE action_issues ("
            " id INTEGER PRIMARY KEY, facts TEXT, bsky_posted_at DATETIME,"
            " bsky_last_post_text TEXT)"
        ))
        conn.execute(text(rows))


def test_bsky_posted_facts_backfilled_for_already_posted_issues(patched_engine):
    # #310 added bsky_posted_facts and left it NULL on existing rows, where
    # the repost gate falls back to the live `facts` column — the ratcheting
    # baseline #310 exists to remove. The column is only ever written by the
    # Bluesky poster, which only sees issues with bsky_posted_at NULL, which
    # for a posted issue only the stuck gate can clear: without a backfill
    # the rows the fix targets can never reach it.
    eng = patched_engine
    _legacy_action_issues(eng, (
        "INSERT INTO action_issues (id, facts, bsky_posted_at) VALUES"
        " (1, '[\"Senate passed S.1\"]', '2026-07-26 12:00:00'),"
        " (2, '[\"House voted\"]', NULL)"
    ))

    database._migrate_columns()

    with eng.begin() as conn:
        rows = {
            r.id: r.bsky_posted_facts
            for r in conn.execute(text(
                "SELECT id, bsky_posted_facts FROM action_issues"
            )).fetchall()
        }
    # Posted row gets its baseline pinned to what readers were last told.
    assert rows[1] == '["Senate passed S.1"]'
    # Never-posted row stays NULL — the poster sets it on its first post,
    # and pinning a baseline for a post that never happened would suppress
    # that first post's own content as "already known".
    assert rows[2] is None


def test_bsky_posted_facts_backfill_does_not_rerun_on_restart(patched_engine):
    # The backfill runs on every startup (so a process that dies between the
    # ALTER and the UPDATE repairs itself), which is only safe because a
    # seeded row stops matching. A baseline the poster has since advanced
    # must never be re-pinned — that would rebuild the exact ratchet this
    # removes, once per deploy.
    eng = patched_engine
    _legacy_action_issues(eng, (
        "INSERT INTO action_issues (id, facts, bsky_posted_at) VALUES"
        " (1, '[\"original\"]', '2026-07-26 12:00:00')"
    ))

    database._migrate_columns()

    # The poster advances the baseline, then `facts` moves on as it does on
    # every hourly refresh. A second startup must not overwrite the former.
    with eng.begin() as conn:
        conn.execute(text(
            "UPDATE action_issues SET bsky_posted_facts = '[\"posted\"]',"
            " facts = '[\"newer facts\"]' WHERE id = 1"
        ))

    database._migrate_columns()

    with eng.begin() as conn:
        value = conn.execute(text(
            "SELECT bsky_posted_facts FROM action_issues WHERE id = 1"
        )).scalar()
    assert value == '["posted"]'


def test_bsky_posted_facts_backfill_repairs_a_half_applied_migration(patched_engine):
    # pysqlite commits around DDL, so the ALTER can land while the process
    # dies before the UPDATE. Gating the backfill on "did this call add the
    # column?" would leave that database on the pre-#310 behavior forever,
    # silently. Simulate it: column present, baseline never seeded.
    eng = patched_engine
    _legacy_action_issues(eng, (
        "INSERT INTO action_issues (id, facts, bsky_posted_at) VALUES"
        " (1, '[\"carried over\"]', '2026-07-26 12:00:00')"
    ))
    with eng.begin() as conn:
        conn.execute(text("ALTER TABLE action_issues ADD COLUMN bsky_posted_facts TEXT"))

    database._migrate_columns()  # adds nothing; must still backfill

    with eng.begin() as conn:
        value = conn.execute(text(
            "SELECT bsky_posted_facts FROM action_issues WHERE id = 1"
        )).scalar()
    assert value == '["carried over"]'


class TestStateOfficeTableRebuild:
    """statewide_nominees and state_leg_nominees gained shape AFTER they
    first shipped, and create_all never alters an existing table.

    The schema below is production's real one, read off the deployed
    database: created when the table first shipped, so it has no
    `district` column and a unique index that predates `display_name`.
    Left alone, the first ballot-page request raises "no such column:
    statewide_nominees.district".
    """

    _PRODUCTION_SHAPE = """
        CREATE TABLE statewide_nominees (
            id INTEGER NOT NULL PRIMARY KEY,
            state VARCHAR(2) NOT NULL,
            cycle_year INTEGER NOT NULL,
            office VARCHAR(32) NOT NULL,
            party VARCHAR(1) NOT NULL,
            display_name VARCHAR(200) NOT NULL,
            source_name VARCHAR(200),
            updated_at DATETIME,
            UNIQUE (state, cycle_year, office, party)
        )
    """

    def test_a_table_missing_a_column_is_dropped(self, patched_engine):
        with patched_engine.begin() as conn:
            conn.execute(text(self._PRODUCTION_SHAPE))
            conn.execute(text(
                "INSERT INTO statewide_nominees "
                "(state, cycle_year, office, party, display_name) "
                "VALUES ('RI', 2026, 'governor', 'D', 'Someone')"
            ))
        database._migrate_state_office_tables()
        assert not inspect(patched_engine).has_table("statewide_nominees")

    def test_create_all_then_rebuilds_it_with_the_column(self, patched_engine):
        with patched_engine.begin() as conn:
            conn.execute(text(self._PRODUCTION_SHAPE))
        database._migrate_state_office_tables()
        database.Base.metadata.create_all(bind=patched_engine)
        columns = {c["name"] for c in inspect(patched_engine).get_columns("statewide_nominees")}
        assert "district" in columns

    def test_a_current_table_is_left_alone(self, patched_engine):
        """The rebuild must not fire on every startup — it would discard
        the day's sync each time the process restarts."""
        database.Base.metadata.create_all(bind=patched_engine)
        with patched_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO statewide_nominees "
                "(state, cycle_year, office, party, display_name, source_name, updated_at) "
                "VALUES ('RI', 2026, 'governor', 'D', 'Someone', 'src', '2026-09-21 00:00:00')"
            ))
        database._migrate_state_office_tables()
        with patched_engine.begin() as conn:
            kept = conn.execute(text("SELECT COUNT(*) FROM statewide_nominees")).scalar()
        assert kept == 1

    def test_a_stale_unique_index_alone_triggers_the_rebuild(self, patched_engine):
        """Even with every column present, a unique key predating
        display_name rejects the second same-party nominee a top-two
        state legitimately produces — and SQLite cannot drop a
        table-level UNIQUE without rebuilding."""
        with patched_engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE state_leg_nominees (
                    id INTEGER NOT NULL PRIMARY KEY,
                    state VARCHAR(2) NOT NULL,
                    cycle_year INTEGER NOT NULL,
                    chamber VARCHAR(8) NOT NULL,
                    district VARCHAR(8) NOT NULL,
                    seat VARCHAR(4),
                    party VARCHAR(1) NOT NULL,
                    display_name VARCHAR(200) NOT NULL,
                    source_name VARCHAR(200),
                    updated_at DATETIME,
                    UNIQUE (state, cycle_year, chamber, district, party)
                )
            """))
        database._migrate_state_office_tables()
        assert not inspect(patched_engine).has_table("state_leg_nominees")

    def test_an_absent_table_is_not_an_error(self, patched_engine):
        """A fresh install has neither table; create_all builds both."""
        database._migrate_state_office_tables()
