"""POST /action/pulse — one stance per issue per visitor per day, without
keeping the visitor's IP address."""

from types import SimpleNamespace
from unittest.mock import patch

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


async def _vote(db_session, ip: str, issue_id: int):
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
    import sqlite3

    issue_id = _issue(db_session)
    await _vote(db_session, "203.0.113.7", issue_id)
    conn = sqlite3.connect(throttle_store)
    held = [row[0] for row in conn.execute("SELECT key FROM claims")]
    conn.close()
    assert held and all("203.0.113.7" not in key for key in held)


async def test_a_vote_on_a_missing_issue_does_not_use_up_the_day(db_session):
    issue_id = _issue(db_session)
    with pytest.raises(HTTPException) as exc:
        await _vote(db_session, "203.0.113.7", issue_id + 1)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as again:
        await _vote(db_session, "203.0.113.7", issue_id + 1)
    assert again.value.status_code == 404  # not 429


async def test_a_vote_more_than_a_day_later_is_allowed(db_session, monkeypatch):
    from datetime import datetime, timezone

    from app.api import throttle

    issue_id = _issue(db_session)
    await _vote(db_session, "203.0.113.7", issue_id)

    class _Tomorrow(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2099, 1, 2, tzinfo=timezone.utc)

    monkeypatch.setattr(throttle, "datetime", _Tomorrow)
    again = await _vote(db_session, "203.0.113.7", issue_id)
    assert again["concernedCount"] == 2


async def test_a_failure_after_the_vote_commits_keeps_the_claim(db_session):
    # The vote is counted; releasing the claim on a later error would let
    # the visitor's retry count a second one.
    issue_id = _issue(db_session)
    real_query = db_session.query
    calls = []

    def query_then_lose_the_connection(*args, **kwargs):
        calls.append(args)
        if len(calls) > 1:  # the totals read, after the vote committed
            raise RuntimeError("connection lost")
        return real_query(*args, **kwargs)

    with patch.object(db_session, "query", query_then_lose_the_connection), pytest.raises(RuntimeError):
        await _vote(db_session, "203.0.113.9", issue_id)
    with pytest.raises(HTTPException) as again:
        await _vote(db_session, "203.0.113.9", issue_id)
    assert again.value.status_code == 429
    db_session.expire_all()
    assert db_session.get(ActionIssue, issue_id).concerned_count == 1


async def test_a_vote_whose_dedup_cannot_be_checked_is_refused(db_session, tmp_path):
    # Failing open here would count unlimited votes from one client for
    # as long as the store is down.
    from app.api import throttle

    issue_id = _issue(db_session)
    previous = throttle._path
    throttle.use_path(str(tmp_path / "no-such-dir" / "t.db"))
    try:
        with pytest.raises(HTTPException) as exc:
            await _vote(db_session, "203.0.113.20", issue_id)
    finally:
        throttle.use_path(previous)
    assert exc.value.status_code == 503
    db_session.expire_all()
    assert db_session.get(ActionIssue, issue_id).concerned_count in (0, None)


async def test_concurrent_votes_are_all_counted(db_session, monkeypatch):
    # Several API workers: a count read and written back plus one would let
    # simultaneous votes both write the same total.
    import asyncio

    issue_id = _issue(db_session)
    await asyncio.gather(*(_vote(db_session, f"203.0.113.{n}", issue_id) for n in range(20, 26)))
    db_session.expire_all()
    assert db_session.get(ActionIssue, issue_id).concerned_count == 6


async def test_a_request_cancelled_while_the_vote_commits_keeps_the_claim(db_session):
    # Cancelling the awaiting request doesn't stop the thread committing the
    # vote; releasing the claim then would let a retry count a second one.
    import asyncio
    import time

    issue_id = _issue(db_session)
    real_commit = db_session.commit

    def slow_commit():
        time.sleep(0.2)
        real_commit()

    with patch.object(db_session, "commit", slow_commit):
        task = asyncio.create_task(_vote(db_session, "203.0.113.30", issue_id))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.3)  # the thread finishes the commit
    with pytest.raises(HTTPException) as again:
        await _vote(db_session, "203.0.113.30", issue_id)
    assert again.value.status_code == 429
    db_session.expire_all()
    assert db_session.get(ActionIssue, issue_id).concerned_count == 1
