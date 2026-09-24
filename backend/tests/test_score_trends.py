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


def test_latest_snapshot_need_not_be_today(db_session):
    """The map must work off the most recent snapshot DATE, not literally
    today — requiring date == today made every trend vanish whenever the
    nightly pipeline hadn't run yet (early UTC hours) or had failed."""
    yesterday = (utcnow().date() - timedelta(days=1)).isoformat()
    eight_days_ago = (utcnow().date() - timedelta(days=8)).isoformat()
    db_session.add(_snapshot("senator", "S001", eight_days_ago, 60.0))
    db_session.add(_snapshot("senator", "S001", yesterday, 65.0))
    db_session.commit()

    result = compute_score_trend_map(db_session, "senator")
    assert result["S001"] == {"direction": "up", "change": 5.0, "previousScore": 60.0}


def test_young_history_falls_back_to_nearest_older_snapshot(db_session):
    """With less than a week of history, a snapshot 1-6 days older than the
    latest must be used as the prior instead of marking the member 'new'
    (the fallback the old implementation documented but never executed)."""
    today = utcnow().date().isoformat()
    two_days_ago = (utcnow().date() - timedelta(days=2)).isoformat()
    db_session.add(_snapshot("senator", "S001", two_days_ago, 60.0))
    db_session.add(_snapshot("senator", "S001", today, 66.0))
    db_session.commit()

    result = compute_score_trend_map(db_session, "senator")
    assert result["S001"] == {"direction": "up", "change": 6.0, "previousScore": 60.0}


def _versioned(entity_id, date, score, version):
    return ScoreSnapshot(entity_type="senator", entity_id=entity_id, date=date,
                         overall_score=score, algorithm_version=version)


def test_a_methodology_change_is_not_reported_as_movement(db_session):
    # A new algorithm version moves every score at once; the week-over-week
    # arrow used to report it as the member rising or falling.
    db_session.add_all([
        _versioned("S001", "2026-09-10", 60.0, "v6.12"),
        _versioned("S001", "2026-09-17", 70.0, "v6.13"),
    ])
    db_session.commit()
    assert compute_score_trend_map(db_session, "senator")["S001"]["direction"] == "reset"


def test_the_last_snapshot_on_the_same_version_is_used(db_session):
    db_session.add_all([
        _versioned("S001", "2026-09-01", 50.0, "v6.13"),
        _versioned("S001", "2026-09-05", 60.0, "v6.12"),
        _versioned("S001", "2026-09-17", 53.0, "v6.13"),
    ])
    db_session.commit()
    assert compute_score_trend_map(db_session, "senator")["S001"] == {
        "direction": "up", "change": 3.0, "previousScore": 50.0,
    }


def test_a_new_congress_resets_the_trend(db_session):
    # The current-term window restarts on January 3 of odd years.
    db_session.add_all([
        _versioned("S001", "2026-12-30", 60.0, "v6.13"),
        _versioned("S001", "2027-01-06", 50.0, "v6.13"),
    ])
    db_session.commit()
    assert compute_score_trend_map(db_session, "senator")["S001"]["direction"] == "reset"


def test_jan_1_and_2_still_belong_to_the_old_congress(db_session):
    db_session.add_all([
        _versioned("S001", "2026-12-26", 60.0, "v6.13"),
        _versioned("S001", "2027-01-02", 55.0, "v6.13"),
    ])
    db_session.commit()
    assert compute_score_trend_map(db_session, "senator")["S001"]["direction"] == "down"
