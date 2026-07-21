"""Tests for compute_score_trend_map — shared trend computation extracted
from senator_service.py's _compute_trend_map and representative_service.py's
_compute_rep_trend_map (previously copy-pasted, down to the same lookback
window and change threshold)."""

from datetime import timedelta
from app.time_utils import utcnow

from app.models import ScoreSnapshot
from app.services.score_trends import compute_score_trend_map


def _snapshot(entity_type, entity_id, date, overall_score):
    return ScoreSnapshot(
        entity_type=entity_type, entity_id=entity_id, date=date, overall_score=overall_score,
    )


def test_no_snapshot_today_returns_empty_map(db_session):
    assert compute_score_trend_map(db_session, "senator") == {}


def test_first_ever_snapshot_is_marked_new(db_session):
    today = utcnow().date().isoformat()
    db_session.add(_snapshot("senator", "S001", today, 72.0))
    db_session.commit()

    result = compute_score_trend_map(db_session, "senator")
    assert result["S001"] == {"direction": "new", "change": 0.0, "previousScore": None}


def test_score_increase_above_threshold_is_up(db_session):
    today = utcnow().date().isoformat()
    week_ago = (utcnow().date() - timedelta(days=7)).isoformat()
    db_session.add(_snapshot("senator", "S001", week_ago, 60.0))
    db_session.add(_snapshot("senator", "S001", today, 65.0))
    db_session.commit()

    result = compute_score_trend_map(db_session, "senator")
    assert result["S001"] == {"direction": "up", "change": 5.0, "previousScore": 60.0}


def test_score_decrease_above_threshold_is_down(db_session):
    today = utcnow().date().isoformat()
    week_ago = (utcnow().date() - timedelta(days=7)).isoformat()
    db_session.add(_snapshot("representative", "R001", week_ago, 60.0))
    db_session.add(_snapshot("representative", "R001", today, 55.0))
    db_session.commit()

    result = compute_score_trend_map(db_session, "representative")
    assert result["R001"] == {"direction": "down", "change": -5.0, "previousScore": 60.0}


def test_small_change_within_threshold_is_stable(db_session):
    today = utcnow().date().isoformat()
    week_ago = (utcnow().date() - timedelta(days=7)).isoformat()
    db_session.add(_snapshot("senator", "S001", week_ago, 60.0))
    db_session.add(_snapshot("senator", "S001", today, 60.2))
    db_session.commit()

    result = compute_score_trend_map(db_session, "senator")
    assert result["S001"]["direction"] == "stable"


def test_entity_types_are_isolated(db_session):
    today = utcnow().date().isoformat()
    db_session.add(_snapshot("senator", "S001", today, 72.0))
    db_session.add(_snapshot("representative", "R001", today, 72.0))
    db_session.commit()

    senate_result = compute_score_trend_map(db_session, "senator")
    house_result = compute_score_trend_map(db_session, "representative")
    assert set(senate_result.keys()) == {"S001"}
    assert set(house_result.keys()) == {"R001"}
