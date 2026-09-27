"""POST /action/pulse — one stance per issue per visitor per day, without
keeping the visitor's IP address."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.action import PulseVoteRequest, record_pulse_vote
from app.models import ActionIssue


@pytest.fixture(autouse=True)
def _store(throttle_store):
    yield throttle_store


def _request(ip: str):
    return SimpleNamespace(client=SimpleNamespace(host=ip), headers={})


def _issue(db_session) -> int:
    issue = ActionIssue(date="2026-09-26", rank=1, title="An issue")
    db_session.add(issue)
    db_session.commit()
    return issue.id


async def _vote(db_session, ip: str, issue_id: int, salt: bytes = b"s" * 32):
    with patch("app.api.visits._daily_salt", AsyncMock(return_value=salt)):
        return await record_pulse_vote(
            _request(ip), PulseVoteRequest(issue_id=issue_id, stance="concerned"),
            None, db_session,
        )


async def test_second_vote_same_day_is_refused(db_session):
    issue_id = _issue(db_session)
    first = await _vote(db_session, "203.0.113.7", issue_id)
    assert first["concernedCount"] == 1
    with pytest.raises(HTTPException) as exc:
        await _vote(db_session, "203.0.113.7", issue_id)
    assert exc.value.status_code == 429


async def test_other_visitors_are_unaffected(db_session):
    issue_id = _issue(db_session)
    await _vote(db_session, "203.0.113.7", issue_id)
    second = await _vote(db_session, "198.51.100.4", issue_id)
    assert second["concernedCount"] == 2


async def test_the_ip_itself_is_never_held(db_session, throttle_store):
    from sqlalchemy import text

    issue_id = _issue(db_session)
    await _vote(db_session, "203.0.113.7", issue_id)
    with throttle_store.connect() as conn:
        held = [row[0] for row in conn.execute(text("SELECT key FROM throttle_claims"))]
    assert held and all("203.0.113.7" not in key for key in held)


async def test_a_vote_on_a_missing_issue_does_not_use_up_the_day(db_session):
    issue_id = _issue(db_session)
    with pytest.raises(HTTPException) as exc:
        await _vote(db_session, "203.0.113.7", issue_id + 1)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as again:
        await _vote(db_session, "203.0.113.7", issue_id + 1)
    assert again.value.status_code == 404  # not 429


async def test_a_new_day_salt_allows_a_new_vote(db_session):
    issue_id = _issue(db_session)
    await _vote(db_session, "203.0.113.7", issue_id, salt=b"a" * 32)
    again = await _vote(db_session, "203.0.113.7", issue_id, salt=b"b" * 32)
    assert again["concernedCount"] == 2
