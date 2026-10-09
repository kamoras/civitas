"""A member whose prepare or score step raises keeps a stale scorecard; the
run's failure count was the only trace of it until alert_member_failures
(2026-10-09: one senator failed every night from v6.31 on, unnoticed).
One alert per chamber per run, so an outage is one alert, not a hundred."""

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


def _run(db_session, failures, chamber="senate", **kw):
    with patch("app.ops_alerts.SessionLocal", return_value=db_session):
        alert_member_failures(chamber, failures, **kw)
        return recent_alerts()


def test_a_member_whose_validation_raises_alerts_once_naming_them(db_session):
    [alert] = _run(db_session, [("jane-doe", _validation_error())])
    assert alert["open"] and alert["condition"] == "member-failed-senate"
    assert "1 member failed" in alert["subject"]
    assert "TypeError" in alert["body"] and "×1: jane-doe" in alert["body"]


def test_the_same_set_the_next_night_does_not_alert_again(db_session):
    _run(db_session, [("jane-doe", _validation_error())])
    assert len(_run(db_session, [("jane-doe", _validation_error())])) == 1


def test_a_changed_set_alerts_again_and_replaces_the_old_alert(db_session):
    _run(db_session, [("jane-doe", _validation_error())])
    alerts = _run(db_session, [("jane-doe", _validation_error()), ("john-roe", ValueError("bad"))])
    assert [a["open"] for a in alerts] == [True, False]
    assert "2 members failed" in alerts[0]["subject"] and "john-roe" in alerts[0]["body"]


def test_an_outage_of_a_hundred_is_one_grouped_alert(db_session):
    failures = [(f"senator-{i:03d}", RuntimeError("FEC 503")) for i in range(100)]
    [alert] = _run(db_session, failures)
    assert "100 members failed" in alert["subject"]
    assert "RuntimeError: FEC 503 ×100: senator-000" in alert["body"]
    assert "and 90 more" in alert["body"] and "senator-099" not in alert["body"]


def test_a_clean_run_closes_the_alert(db_session):
    _run(db_session, [("jane-doe", _validation_error())])
    [alert] = _run(db_session, [])
    assert not alert["open"] and alert["resolvedAt"]


def test_a_single_member_run_with_no_failures_leaves_the_alert_open(db_session):
    _run(db_session, [("jane-doe", _validation_error())])
    [alert] = _run(db_session, [], close_when_clean=False)
    assert alert["open"]


def test_the_chambers_alert_separately(db_session):
    _run(db_session, [("jane-doe", _validation_error())])
    alerts = _run(db_session, [("john-roe", ValueError("bad"))], chamber="house")
    assert sorted(a["condition"] for a in alerts if a["open"]) == ["member-failed-house", "member-failed-senate"]


def test_alerting_never_raises_into_the_run():
    with patch("app.pipeline.run_checks.send_ops_alert", side_effect=RuntimeError("down")):
        alert_member_failures("house", [("x", ValueError("y"))])
    with patch("app.pipeline.run_checks.resolve_ops_alert", side_effect=RuntimeError("down")):
        alert_member_failures("house", [])
