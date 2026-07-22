"""Tests for president_pipeline.py's score-history snapshotting."""

from app.models import President, ScoreSnapshot
from app.pipeline.analyze.president_scorer import PRESIDENT_ALGORITHM_VERSION
from app.pipeline.president_pipeline import _record_president_snapshots


def _make_president(**overrides) -> President:
    defaults = dict(
        id="test-prez", name="Test President", party="D", number=99,
        term_start="2021-01-20", term_end=None, is_current=True,
        score_public_mandate=60.0, score_effectiveness=55.0,
        score_competence=50.0, score_agency_alignment=65.0,
    )
    defaults.update(overrides)
    return President(**defaults)


class TestRecordPresidentSnapshots:
    def test_writes_one_snapshot_per_president(self, db_session):
        db_session.add(_make_president())
        db_session.add(_make_president(id="test-prez-2", number=98, score_public_mandate=40.0))
        db_session.commit()

        _record_president_snapshots(db_session)

        snapshots = db_session.query(ScoreSnapshot).filter(
            ScoreSnapshot.entity_type == "president",
        ).all()
        assert len(snapshots) == 2
        assert {s.entity_id for s in snapshots} == {"test-prez", "test-prez-2"}

    def test_maps_dimensions_to_score_slots_correctly(self, db_session):
        db_session.add(_make_president(
            score_public_mandate=60.0, score_effectiveness=55.0,
            score_competence=50.0, score_agency_alignment=65.0,
        ))
        db_session.commit()

        _record_president_snapshots(db_session)

        snap = db_session.query(ScoreSnapshot).filter(
            ScoreSnapshot.entity_type == "president", ScoreSnapshot.entity_id == "test-prez",
        ).first()
        assert snap.score_1 == 60.0  # publicMandate
        assert snap.score_2 == 55.0  # effectiveness
        assert snap.score_3 == 50.0  # competence
        assert snap.score_4 == 65.0  # agencyAlignment
        # Pin the exact version, not just non-null: trend charts key formula-
        # change markers off this string, so a wrong stamp (e.g. the senator
        # ALGORITHM_VERSION copy-pasted in) must fail here.
        assert snap.algorithm_version == PRESIDENT_ALGORITHM_VERSION

    def test_rerunning_same_day_upserts_not_duplicates(self, db_session):
        p = _make_president()
        db_session.add(p)
        db_session.commit()

        _record_president_snapshots(db_session)
        p.score_public_mandate = 80.0
        db_session.commit()
        _record_president_snapshots(db_session)

        snapshots = db_session.query(ScoreSnapshot).filter(
            ScoreSnapshot.entity_type == "president", ScoreSnapshot.entity_id == "test-prez",
        ).all()
        assert len(snapshots) == 1
        assert snapshots[0].score_1 == 80.0

    def test_historical_president_with_unchanging_score_still_gets_snapshotted(self, db_session):
        # Not in DYNAMIC_PRESIDENTS/ECONOMICS_ONLY_PRESIDENTS — score never
        # moves, but the trend line still needs a continuous daily row,
        # same as senators/reps.
        db_session.add(_make_president(id="lincoln-16", number=16, is_current=False))
        db_session.commit()

        _record_president_snapshots(db_session)

        snap = db_session.query(ScoreSnapshot).filter(
            ScoreSnapshot.entity_type == "president", ScoreSnapshot.entity_id == "lincoln-16",
        ).first()
        assert snap is not None
