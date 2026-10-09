"""A member whose prepare or score step raises keeps a stale scorecard; the
run's failure count was the only trace of it until alert_member_failures
(2026-10-09: one senator failed every night from v6.31 on, unnoticed)."""

from unittest.mock import patch

import pytest

from app.ops_alerts import recent_alerts
from app.pipeline.assemble.validator import validate_senator
from app.pipeline.run_checks import alert_member_failures


def _validation_error() -> Exception:
    """What a member's run step raises when validation fails."""
    with pytest.raises(TypeError) as info:
        validate_senator({
            "id": "jane-doe", "name": "Jane Doe", "state": "NY", "party": "D",
            "funding": {"totalRaised": "not a number"},
        })
    return info.value


def _run(db_session, failures, stored=()):
    with patch("app.ops_alerts.SessionLocal", return_value=db_session):
        alert_member_failures("senate", failures, stored)
        return recent_alerts()


def test_a_member_whose_validation_raises_alerts_once_naming_them(db_session):
    [alert] = _run(db_session, [("jane-doe", _validation_error())])
    assert "jane-doe" in alert["subject"]
    assert "jane-doe" in alert["body"] and "TypeError" in alert["body"]
    assert alert["open"]


def test_the_same_failure_the_next_night_does_not_alert_again(db_session):
    _run(db_session, [("jane-doe", _validation_error())])
    alerts = _run(db_session, [("jane-doe", _validation_error())])
    assert len(alerts) == 1


def test_a_different_failure_for_the_member_alerts_again(db_session):
    _run(db_session, [("jane-doe", _validation_error())])
    alerts = _run(db_session, [("jane-doe", RuntimeError("database is locked"))])
    assert [a["open"] for a in alerts] == [True, False]  # the newer supersedes
    assert "RuntimeError: database is locked" in alerts[0]["body"]


def test_each_failed_member_is_named_in_their_own_alert(db_session):
    alerts = _run(db_session, [("jane-doe", _validation_error()), ("john-roe", ValueError("bad"))])
    assert sorted(a["subject"].rsplit(" ", 1)[1] for a in alerts) == ["jane-doe", "john-roe"]


def test_a_member_stored_again_closes_their_alert_and_a_recurrence_alerts(db_session):
    _run(db_session, [("jane-doe", _validation_error())])
    [alert] = _run(db_session, [], stored=["jane-doe"])
    assert not alert["open"] and alert["resolvedAt"]
    alerts = _run(db_session, [("jane-doe", _validation_error())])
    assert len(alerts) == 2 and alerts[0]["open"]


def test_alerting_never_raises_into_the_run(db_session):
    with patch("app.pipeline.run_checks.send_ops_alert", side_effect=RuntimeError("down")):
        alert_member_failures("house", [("x", ValueError("y"))], [])
