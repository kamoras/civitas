"""Public API usage counters (ApiRequestCount): what is counted, how it is
told apart by channel and outcome, and what the admin endpoint reports."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.admin import admin_api_usage
from app.api.public import CHANNEL_HEADER
from app.api.rate_limit import public_read_limit
from app.api.router import api_router
from app.database import get_db
from app.models import ApiRejectionCount, ApiRequestCount, SiteVisit, Senator
from tests.visits_helpers import _drain_queue_and_write


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _days_ago(n: int) -> str:
    return (datetime.now(UTC).date() - timedelta(days=n)).isoformat()


@pytest.fixture
def client(db_session):
    db_session.add(Senator(
        id="jon-brennan", name="Jon Brennan", state="GA", party="D",
        score_funding_independence=60, score_promise_persistence=50, score_constituent_alignment=55,
        score_funding_diversity=40, score_legislative_effectiveness=70,
    ))
    db_session.commit()
    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[get_db] = lambda: db_session
    return app, TestClient(app)


def _counts(db) -> dict[tuple[str, str, int], int]:
    _drain_queue_and_write(db)
    return {(r.endpoint, r.channel, r.status): r.count for r in db.query(ApiRequestCount).all()}


def test_each_documented_request_is_counted_by_outcome(client, db_session):
    _, http = client
    http.get("/api/public/v1/senators")
    http.get("/api/public/v1/senators")
    http.get("/api/public/v1/senators/nobody")
    http.get("/api/public/v1/senators", params={"party": "X"})
    assert _counts(db_session) == {
        ("list_senators", "http", 200): 2,
        ("get_senator", "http", 404): 1,
        ("list_senators", "http", 422): 1,
    }


def test_an_invalid_request_records_the_parameter_and_rule_never_the_value(client, db_session):
    _, http = client
    http.get("/api/public/v1/senators", params={"party": "X"})
    http.get("/api/public/v1/senators", params={"colour": "blue"})
    _counts(db_session)
    rows = {(r.endpoint, r.parameter, r.reason): r.count for r in db_session.query(ApiRejectionCount).all()}
    assert rows == {
        ("list_senators", "party", "literal_error"): 1,
        ("list_senators", "colour", "unknown_parameter"): 1,
    }
    assert not any("blue" in str(v) for r in db_session.query(ApiRejectionCount).all() for v in vars(r).values())


def test_a_party_or_state_written_out_is_read(client):
    _, http = client
    by_name = http.get("/api/public/v1/senators", params={"party": "Democrat", "state": "georgia"})
    assert by_name.status_code == 200
    assert [e["id"] for e in by_name.json()["entries"]] == ["jon-brennan"]


def test_rate_limit_refusals_are_counted_as_429(client, db_session):
    app, http = client

    def refuse():
        raise HTTPException(status_code=429, detail="slow down")

    app.dependency_overrides[public_read_limit] = refuse
    assert http.get("/api/public/v1/senators").status_code == 429
    assert _counts(db_session) == {("list_senators", "http", 429): 1}


def test_the_spec_and_preflights_are_not_api_use(client, db_session):
    _, http = client
    http.get("/api/public/v1/openapi.json")
    http.options("/api/public/v1/senators")
    assert _counts(db_session) == {}


def test_the_channel_header_marks_mcp_calls(client, db_session):
    _, http = client
    http.get("/api/public/v1/senators", headers={CHANNEL_HEADER: "mcp"})
    http.get("/api/public/v1/senators", headers={CHANNEL_HEADER: "anything else"})
    assert _counts(db_session) == {("list_senators", "mcp", 200): 1, ("list_senators", "http", 200): 1}


def test_api_use_never_touches_the_visitor_figures(client, db_session):
    _, http = client
    http.get("/api/public/v1/senators")
    _counts(db_session)
    assert db_session.query(SiteVisit).count() == 0


def test_admin_usage_is_zero_filled_and_split_by_channel_and_outcome(db_session):
    db_session.add_all([
        ApiRequestCount(date=_today(), endpoint="list_senators", channel="http", status=200, count=5),
        ApiRequestCount(date=_today(), endpoint="list_senators", channel="mcp", status=200, count=2),
        ApiRequestCount(date=_today(), endpoint="get_senator", channel="http", status=404, count=1),
        ApiRequestCount(date=_today(), endpoint="get_senator", channel="http", status=429, count=3),
        ApiRequestCount(date=_today(), endpoint="get_senator", channel="http", status=500, count=2),
        ApiRejectionCount(date=_today(), endpoint="search_documents", channel="http", parameter="doc_type",
                          reason="literal_error", count=6),
        ApiRejectionCount(date=_days_ago(1), endpoint="search_documents", channel="mcp", parameter="doc_type",
                          reason="literal_error", count=1),
        ApiRejectionCount(date=_today(), endpoint="search_documents", channel="http", parameter="q",
                          reason="string_too_short", count=2),
        ApiRequestCount(date=_today(), endpoint="tools/list", channel="mcp", status=200, count=4),
        ApiRequestCount(date=_days_ago(2), endpoint="search_documents", channel="http", status=200, count=1),
        ApiRequestCount(date=_days_ago(9), endpoint="search_documents", channel="http", status=200, count=50),
    ])
    db_session.commit()

    usage = admin_api_usage(days=3, db=db_session)

    assert [d["date"] for d in usage["days"]] == [_days_ago(2), _days_ago(1), _today()]
    assert usage["days"][1] == {"date": _days_ago(1), "http": 0, "mcp": 0, "rateLimited": 0,
                                "rejected": 0, "serverErrors": 0, "mcpConnections": 0}
    today = usage["days"][2]
    assert (today["http"], today["mcp"], today["rateLimited"], today["rejected"], today["serverErrors"],
            today["mcpConnections"]) == (11, 2, 3, 1, 2, 4)
    assert usage["totals"] == {"http": 12, "mcp": 2, "rateLimited": 3, "rejected": 1, "serverErrors": 2,
                               "mcpConnections": 4}
    assert [e["endpoint"] for e in usage["byEndpoint"]] == ["list_senators", "get_senator", "search_documents"]
    assert usage["byEndpoint"][1] == {"endpoint": "get_senator", "http": 6, "mcp": 0, "rateLimited": 3,
                                      "rejected": 1, "serverErrors": 2}
    # Why invalid requests were refused, summed over the window across channels, most frequent first.
    assert usage["rejections"] == [
        {"endpoint": "search_documents", "parameter": "doc_type", "reason": "literal_error", "count": 7},
        {"endpoint": "search_documents", "parameter": "q", "reason": "string_too_short", "count": 2},
    ]
