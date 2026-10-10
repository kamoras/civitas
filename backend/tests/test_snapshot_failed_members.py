"""A member the run failed to score gets no snapshot stamped with this run's
algorithm version: their stored scores are the last passing run's, which an
older algorithm may have produced."""

from datetime import datetime

from app.models import Representative, ScoreSnapshot, Senator
from app.pipeline.analyze.score_calculator import ALGORITHM_VERSION
from app.pipeline.house_pipeline import _record_rep_snapshots
from app.pipeline.senate_pipeline import _record_score_snapshots

TODAY = "2026-07-27"


def _snapshots(db, entity_type):
    return {
        s.entity_id: s.algorithm_version
        for s in db.query(ScoreSnapshot).filter(ScoreSnapshot.entity_type == entity_type)
    }


def test_senate_skips_failed_and_keeps_their_earlier_snapshot_today(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 7, 27, 12))
    for sid in ("s-ok", "s-failed"):
        db_session.add(Senator(id=sid, bioguide_id=sid, name=sid, state="CA", party="D", is_current=True))
    # An earlier run today scored the failed member.
    db_session.add(ScoreSnapshot(entity_type="senator", entity_id="s-failed", date=TODAY,
                                 overall_score=50, algorithm_version="earlier"))
    db_session.commit()

    _record_score_snapshots(db_session, {"s-failed"})

    assert _snapshots(db_session, "senator") == {"s-ok": ALGORITHM_VERSION, "s-failed": "earlier"}


def test_house_skips_failed(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 7, 27, 12))
    for rid in ("r-ok", "r-failed"):
        db_session.add(Representative(id=rid, bioguide_id=rid, name=rid, state="CA", party="D",
                                      district=1, is_current=True))
    db_session.commit()

    _record_rep_snapshots(db_session, {"r-failed"})

    assert _snapshots(db_session, "representative") == {"r-ok": ALGORITHM_VERSION}
