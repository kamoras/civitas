"""A member the latest run couldn't score keeps an earlier run's score, and
every page that shows it says so (score_keeping.held): the profile, the
leaderboard and compare all read it from the member's representationScore.
2026-10-16: one senator showed a v6.34 score ranked among v6.35 scores with
no mark at all."""

from datetime import datetime

from app.models import Representative, ScoreSnapshot, Senator
from app.pipeline.fetch.fec import FecUnavailable
from app.pipeline.run_checks import record_member_holds
from app.pipeline.senate_pipeline import upsert_senator
from app.score_keeping import held, load_breakdown, unread_source
from app.services.representative_service import get_representative_by_id, upsert_representative
from app.services.senator_service import get_leaderboard, get_senator_by_id


def _senator(db_session, member_id="pat-doe"):
    db_session.add(Senator(id=member_id, bioguide_id="Z1", name="Pat Doe", state="OH", party="D",
                           is_current=True))
    db_session.add(ScoreSnapshot(entity_type="senator", entity_id=member_id, date="2026-10-14",
                                 overall_score=57.8, algorithm_version="v6.34"))
    db_session.commit()


def test_a_failed_member_is_marked_with_the_run_it_keeps_and_the_source(db_session, freeze_utcnow):
    _senator(db_session)
    freeze_utcnow(datetime(2026, 10, 16, 6))
    record_member_holds(db_session, Senator, "senator",
                        [("pat-doe", FecUnavailable("no FEC data read for Pat Doe"))])
    expected = {"failedOn": "2026-10-16", "source": "FEC filings", "scoredOn": "2026-10-14",
                "algorithmVersion": "v6.34"}
    assert held(db_session.get(Senator, "pat-doe")) == expected
    # Every surface reads it from the same field.
    assert get_senator_by_id(db_session, "pat-doe").representation_score.held.model_dump(
        by_alias=True) == expected
    [row] = get_leaderboard(db_session)
    assert row.representation_score.held.source == "FEC filings"


def test_a_member_never_stored_has_nothing_held(db_session):
    record_member_holds(db_session, Senator, "senator", [("nobody", RuntimeError("x"))])
    assert db_session.query(Senator).count() == 0


def test_the_next_run_that_scores_the_member_clears_it(db_session):
    _senator(db_session)
    record_member_holds(db_session, Senator, "senator", [("pat-doe", RuntimeError("boom"))])
    assert held(db_session.get(Senator, "pat-doe"))["source"] is None
    kept = {"constituentAlignment": {"score": 60, "components": []}}
    upsert_senator(db_session, {"id": "pat-doe", "name": "Pat Doe", "state": "OH", "party": "D",
                                "representationScore": {"constituentAlignment": 60, "breakdown": kept}})
    db_session.commit()
    assert held(db_session.get(Senator, "pat-doe")) is None
    # The run's breakdown is stored with its scores.
    assert load_breakdown(db_session.get(Senator, "pat-doe"))[0] == kept


def test_a_representative_is_marked_and_cleared_the_same_way(db_session):
    db_session.add(Representative(id="lee-roe", bioguide_id="Z2", name="Lee Roe", state="OH", district=3,
                                  party="R", is_current=True))
    db_session.commit()
    record_member_holds(db_session, Representative, "representative", [("lee-roe", RuntimeError("x"))])
    assert get_representative_by_id(db_session, "lee-roe").representation_score.held.failed_on
    upsert_representative(db_session, {"id": "lee-roe", "representationScore": {}})
    assert held(db_session.get(Representative, "lee-roe")) is None


def test_the_source_is_found_through_a_wrapping_exception():
    try:
        try:
            raise FecUnavailable("down")
        except FecUnavailable as inner:
            raise RuntimeError("member failed") from inner
    except RuntimeError as outer:
        assert unread_source(outer) == "FEC filings"
    assert unread_source(ValueError("x")) is None
