"""Bill ids come out in one spelling ("HR.1", "SJRES.138") in every API
response, however a record stored them; labels ("H.R. 1") are separate
fields; any spelling is read on input (app/bill_ids.py).

Before: a senator's votes carried the Senate roll call's "H.R. 7008" while a
representative's carried "HR.7008" (backlog 7, item 12)."""

import ast
import pathlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.router import api_router
from app.bill_ids import bill_id_out, canonical_bill_id, canonical_ids
from app.database import get_db
from app.models import KeyVote, RepKeyVote, Representative, RollCall, RollCallPosition, Senator

API = pathlib.Path(__file__).resolve().parents[1] / "app" / "api"
SCORES = dict(score_funding_independence=60, score_promise_persistence=50, score_constituent_alignment=55,
              score_funding_diversity=40, score_legislative_effectiveness=70)


@pytest.mark.parametrize("raw,canonical", [
    ("H.R. 7008", "HR.7008"), ("hr7008", "HR.7008"), ("HR.7008", "HR.7008"), ("S.J.Res. 138", "SJRES.138"),
    ("SJRES.138", "SJRES.138"), ("s 2398", "S.2398"), ("H. Con. Res. 4", "HCONRES.4"),
])
def test_every_spelling_reads_as_one_id(raw, canonical):
    assert canonical_bill_id(raw) == canonical


def test_what_is_not_a_bill_is_left_as_it_is():
    assert bill_id_out("PN1129") == "PN1129"
    assert bill_id_out("") == "" and bill_id_out(None) is None


def test_ids_are_settled_at_any_depth_and_labels_left_alone():
    data = {"billId": "H.R. 1", "billLabel": "H.R. 1", "votes": [{"billId": "s.j.res. 2", "label": "S.J.Res. 2"}],
            "billIds": ["H.R. 3", "PN4"], "nested": {"rollCall": {"billId": None}}}
    assert canonical_ids(data) == {
        "billId": "HR.1", "billLabel": "H.R. 1", "votes": [{"billId": "SJRES.2", "label": "S.J.Res. 2"}],
        "billIds": ["HR.3", "PN4"], "nested": {"rollCall": {"billId": None}},
    }


def test_no_api_module_builds_json_another_way():
    """Every API module takes JSONResponse from response_helpers, whose
    render settles the spelling; FastAPI's own would skip it."""
    offenders = []
    for path in API.glob("*.py"):
        if path.name == "response_helpers.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module in ("fastapi.responses", "starlette.responses"):
                offenders += [f"{path.name}: {a.name}" for a in node.names
                              if a.name in ("JSONResponse", "ORJSONResponse", "UJSONResponse")]
    assert offenders == []


@pytest.fixture
def client(db_session):
    db_session.add_all([
        Senator(id="jane-doe", name="Jane Doe", state="GA", party="D", **SCORES),
        Representative(id="john-roe", name="John Roe", state="GA", district=5, party="R", bioguide_id="R000001",
                       **SCORES),
        RollCall(id=1, chamber="senate", congress=119, session=1, number=5, date="2026-02-01",
                 question="On Passage of the Bill H.R. 1", result="Bill Passed", yeas=1, nays=0, bill_id="H.R. 1"),
        RollCallPosition(roll_call_id=1, member_id="S001", last_name="Doe", party="D", state="GA", position="Yea"),
        # Stored as the Senate's roll call names the bill.
        KeyVote(senator_id="jane-doe", bill_name="Placeholder Act", bill_id="H.R. 1", date="2026-02-01",
                vote="Yea", voted_with_party=True, party_leaning="D", roll_call="senate-119-1-5",
                vote_category="key"),
        RepKeyVote(representative_id="john-roe", bill_name="Placeholder Act", bill_id="HR.1", date="2026-01-20",
                   vote="Nay", vote_category="key"),
    ])
    db_session.commit()
    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _bill_ids(data, found=None):
    found = [] if found is None else found
    if isinstance(data, dict):
        for k, v in data.items():
            if k == "billId" and isinstance(v, str):
                found.append(v)
            elif k == "billIds" and isinstance(v, list):
                found += [x for x in v if isinstance(x, str)]
            else:
                _bill_ids(v, found)
    elif isinstance(data, list):
        for x in data:
            _bill_ids(x, found)
    return found


@pytest.mark.parametrize("path", [
    "/api/senators/jane-doe/votes?category=all", "/api/representatives/john-roe/votes?category=all",
    "/api/public/v1/senators/jane-doe/votes", "/api/public/v1/bills/H.R.%201/votes",
    "/api/congress/votes/senate/119/1/5",
])
def test_every_response_names_bills_one_way(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, resp.text
    ids = _bill_ids(resp.json())
    assert ids, "the response should name a bill"
    assert all(bill_id_out(i) == i for i in ids), ids
    assert "H.R. 1" not in ids


def test_any_spelling_finds_the_bill(client):
    for spelling in ("HR.1", "H.R. 1", "hr1"):
        body = client.get(f"/api/public/v1/bills/{spelling}/votes").json()
        assert body["billId"] == "HR.1"
    assert client.get("/api/congress/committee-record", params={"bill": "H.R. 1"}).status_code == 200
    assert client.get("/api/congress/committee-record", params={"bill": "not a bill"}).status_code == 422
