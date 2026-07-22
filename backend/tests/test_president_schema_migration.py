"""Regression test for _migrate_presidents_schema_rebuild (#218 review B1).

Production's presidents table predates this PR's model change: score_
public_mandate/score_effectiveness/score_agency_alignment were NOT NULL
DEFAULT 0.0, and score_competence/summary/key_achievements/key_failures
existed as NOT NULL columns with no server-side default. SQLite has no
ALTER COLUMN to relax a NOT NULL constraint, so an existing database on
that old schema would otherwise raise IntegrityError the moment the
pipeline writes a legitimate None to one of the newly-nullable columns,
or _sync_roster inserts a new president row without the four removed
ones. Reproduces the old schema directly via raw SQL (Base.metadata.
create_all always builds the CURRENT model, so it can't produce the old
shape) and confirms the rebuild + create_all sequence used by init_db
leaves a database that accepts exactly the writes the new pipeline makes.

Uses its own file-backed engine (monkeypatched onto app.database.engine),
same pattern as test_president_id_migration.py.
"""

import os
import tempfile

from sqlalchemy import create_engine, text

from app import database, models


def _fresh_engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})


_OLD_SCHEMA_SQL = """
CREATE TABLE presidents (
    id VARCHAR NOT NULL PRIMARY KEY,
    name VARCHAR NOT NULL,
    party VARCHAR NOT NULL,
    number INTEGER NOT NULL,
    term_start VARCHAR NOT NULL,
    term_end VARCHAR,
    is_current BOOLEAN,
    score_public_mandate FLOAT NOT NULL,
    score_effectiveness FLOAT NOT NULL,
    score_competence FLOAT NOT NULL,
    score_agency_alignment FLOAT NOT NULL,
    avg_approval FLOAT,
    gdp_growth_avg FLOAT,
    jobs_created_millions FLOAT,
    eo_count INTEGER,
    eo_court_success_pct FLOAT,
    cabinet_turnover_pct FLOAT,
    gdp_growth_adjusted FLOAT,
    rulemaking_count INTEGER,
    rulemaking_finalized_pct FLOAT,
    summary TEXT NOT NULL,
    key_achievements TEXT NOT NULL,
    key_failures TEXT NOT NULL,
    created_at DATETIME,
    updated_at DATETIME
)
"""


def test_legacy_schema_is_dropped_and_rebuilt_nullable(monkeypatch):
    engine = _fresh_engine()
    monkeypatch.setattr(database, "engine", engine)

    with engine.begin() as conn:
        conn.execute(text(_OLD_SCHEMA_SQL))
        conn.execute(text(
            "INSERT INTO presidents (id, name, party, number, term_start, "
            "score_public_mandate, score_effectiveness, score_competence, "
            "score_agency_alignment, summary, key_achievements, key_failures) "
            "VALUES ('obama-44', 'Barack Obama', 'D', 44, '2009-01-20', "
            "60.0, 55.0, 50.0, 65.0, '', '[]', '[]')"
        ))

    database._migrate_presidents_schema_rebuild()
    database.Base.metadata.create_all(bind=engine)

    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        # Table was rebuilt fresh — the old row is gone, next pipeline run
        # repopulates it (nothing here was worth preserving through the
        # migration; every score is recomputed nightly anyway).
        assert db.query(models.President).count() == 0

        # The write pattern the new pipeline actually makes must not raise:
        # a president row with the three formerly-NOT-NULL score columns
        # left None, and none of the four removed columns supplied at all.
        db.add(models.President(
            id="obama-44", name="Barack Obama", party="D", number=44,
            term_start="2009-01-20", score_public_mandate=None,
            score_effectiveness=None, score_agency_alignment=None,
        ))
        db.commit()
        p = db.query(models.President).filter(models.President.id == "obama-44").first()
        assert p is not None
        assert p.score_public_mandate is None
    finally:
        db.close()


def test_idempotent_on_already_rebuilt_schema(monkeypatch):
    engine = _fresh_engine()
    monkeypatch.setattr(database, "engine", engine)
    database.Base.metadata.create_all(bind=engine)

    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add(models.President(
        id="obama-44", name="Barack Obama", party="D", number=44,
        term_start="2009-01-20",
    ))
    db.commit()
    db.close()

    database._migrate_presidents_schema_rebuild()  # must not touch an already-current schema
    database._migrate_presidents_schema_rebuild()  # and must not raise on a second call

    db = Session()
    try:
        assert db.query(models.President).count() == 1
    finally:
        db.close()


def test_noop_on_fresh_database(monkeypatch):
    engine = _fresh_engine()
    monkeypatch.setattr(database, "engine", engine)

    database._migrate_presidents_schema_rebuild()  # must not raise with no presidents table at all
