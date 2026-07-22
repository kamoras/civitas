"""Regression test for the bush-41 -> ghwbush-41 president-id migration.

Every new UCSB-derived fetcher this pipeline uses converged on
"ghwbush-41" (avoiding ambiguity with George W. Bush's "gwbush-43");
only the now-removed hand-typed SEED_PRESIDENTS list used "bush-41".
Without _migrate_president_ids, a production database seeded under the
old id would get a duplicate row from _sync_roster instead of an update,
orphaning that row's score history.

Uses its own file-backed engine (monkeypatched onto app.database.engine)
rather than the shared db_session fixture, since _migrate_president_ids
reads the module-global `engine`, not a session passed as an argument.
"""

import os
import tempfile

from sqlalchemy import create_engine

from app import database, models


def _fresh_engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})


def test_bush_41_renamed_to_ghwbush_41(monkeypatch):
    engine = _fresh_engine()
    monkeypatch.setattr(database, "engine", engine)
    database.Base.metadata.create_all(bind=engine)

    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add(models.President(id="bush-41", name="George H.W. Bush", party="R", number=41, term_start="1989-01-20"))
    db.add(models.ScoreSnapshot(
        entity_type="president", entity_id="bush-41", date="2026-01-01",
        overall_score=50.0, score_1=10, score_2=20, score_3=30, score_4=40, score_5=0,
        algorithm_version="v1",
    ))
    db.commit()
    db.close()

    database._migrate_president_ids()

    db = Session()
    assert db.query(models.President).filter(models.President.id == "bush-41").first() is None
    renamed = db.query(models.President).filter(models.President.id == "ghwbush-41").first()
    assert renamed is not None
    assert renamed.name == "George H.W. Bush"
    snap = db.query(models.ScoreSnapshot).filter(models.ScoreSnapshot.entity_id == "ghwbush-41").first()
    assert snap is not None
    db.close()


def test_migration_is_idempotent(monkeypatch):
    engine = _fresh_engine()
    monkeypatch.setattr(database, "engine", engine)
    database.Base.metadata.create_all(bind=engine)

    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add(models.President(id="bush-41", name="George H.W. Bush", party="R", number=41, term_start="1989-01-20"))
    db.commit()
    db.close()

    database._migrate_president_ids()
    database._migrate_president_ids()  # must not raise or duplicate

    db = Session()
    matches = db.query(models.President).filter(models.President.id.in_(["bush-41", "ghwbush-41"])).all()
    assert len(matches) == 1
    assert matches[0].id == "ghwbush-41"
    db.close()


def test_noop_on_fresh_database(monkeypatch):
    engine = _fresh_engine()
    monkeypatch.setattr(database, "engine", engine)
    database.Base.metadata.create_all(bind=engine)

    database._migrate_president_ids()  # must not raise with no bush-41 row at all
