"""One followed thing's Atom feed: a member, a bill, an agency, a monitor
(app/entity_feeds.py, served by app/api/feed.py)."""

import json
from datetime import datetime
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import feed as feed_api
from app.database import get_db
from app.models import (
    Agency,
    ApiCache,
    ExploreDocument,
    MemberIdAlias,
    MonitorUpdate,
    NationalMonitor,
    Representative,
    RepSponsoredBill,
    RepStockTrade,
    RollCall,
    RollCallPosition,
    ScoreSnapshot,
)

A = "{http://www.w3.org/2005/Atom}"
TAG = "tag:civitas-research.org,2026:"


@pytest.fixture
def client(db_session):
    app = FastAPI()
    app.include_router(feed_api.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _entries(resp) -> list[dict]:
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/atom+xml; charset=utf-8"
    assert resp.headers["cache-control"].startswith("public, max-age=")
    root = ET.fromstring(resp.content)
    return [{
        "id": e.findtext(f"{A}id"), "title": e.findtext(f"{A}title"), "published": e.findtext(f"{A}published"),
        "page": e.find(f"{A}link[@rel='alternate']").get("href"),
        "source": next((link.get("href") for link in e.findall(f"{A}link[@rel='related']")), None),
        "term": e.find(f"{A}category").get("term"), "content": e.findtext(f"{A}content"),
    } for e in root.findall(f"{A}entry")]


def _member(db):
    db.add(Representative(id="jane-doe", bioguide_id="D000001", name="Jane Doe", state="OH", district=3, party="D"))
    rc = RollCall(chamber="house", congress=119, session=1, number=12, date="2026-03-01", question="On Passage",
                  title="To do a thing", result="Passed", yeas=220, nays=210, bill_id="HR.1",
                  source_url="https://clerk.house.gov/evs/2026/roll012.xml")
    db.add(rc)
    db.flush()
    db.add(RollCallPosition(roll_call_id=rc.id, member_id="D000001", last_name="Doe", position="Aye"))
    db.add(RepSponsoredBill(representative_id="jane-doe", bill_id="HR.2", title="A bill to do another thing",
                            latest_action="Referred to the Committee on Ways and Means.",
                            latest_action_date="2026-03-05", congress=119))
    for day, score, version in [("2026-03-01", 60.4, "v1"), ("2026-03-02", 61.0, "v1"), ("2026-03-02", 63.6, "v1"),
                                ("2026-03-03", 63.8, "v1"), ("2026-03-04", 58.0, "v2")]:
        db.add(ScoreSnapshot(entity_type="representative", entity_id="jane-doe", date=day, overall_score=score,
                             algorithm_version=version))
    for asset in ("Acme Corp", "Widget Inc"):
        db.add(RepStockTrade(representative_id="jane-doe", asset_name=asset, ticker=None, transaction_type="purchase",
                             transaction_date="2026-02-20", disclosure_date="2026-03-06", amount_low=1001,
                             amount_high=15000, source_url="https://disclosures-clerk.house.gov/x.pdf",
                             filing_id="20030001"))
    db.commit()


def test_a_members_feed_holds_votes_bills_score_changes_and_trades(client, db_session):
    _member(db_session)
    entries = _entries(client.get("/api/feed/members/jane-doe.xml"))
    by_term = {}
    for e in entries:
        by_term.setdefault(e["term"], []).append(e)
    vote = by_term["vote"][0]
    # Keyed on the roll call and the bioguide id, never the slug.
    assert vote["id"] == f"{TAG}vote/house-119-1-12/D000001"
    assert vote["title"] == "Jane Doe voted Yea on House roll call 12: On Passage"
    assert vote["page"] == "https://civitas-research.org/congress/votes/house/119/1/12"
    assert vote["source"] == "https://clerk.house.gov/evs/2026/roll012.xml"
    assert "Result: Passed, 220 to 210." in vote["content"]
    bill = by_term["bill-action"][0]
    assert bill["title"] == "H.R. 2, sponsored by Jane Doe: Referred to the Committee on Ways and Means."
    assert bill["page"] == "https://civitas-research.org/congress/bills/HR.2"
    # Rounded as shown: 63.6 -> 63.8 is no change; the method change is said.
    assert [e["title"] for e in by_term["score"]] == [
        "Jane Doe's representation score went from 64 to 58",
        "Jane Doe's representation score went from 60 to 64",
    ]
    assert "changed from v1 to v2" in by_term["score"][0]["content"]
    trades = by_term["trades"]
    assert len(trades) == 1 and trades[0]["title"] == "Jane Doe disclosed 2 stock trades on 2026-03-06"
    assert "$1,001 to $15,000" in trades[0]["content"]
    assert [e["published"] for e in entries] == sorted((e["published"] for e in entries), reverse=True)


def test_a_renamed_members_old_id_serves_the_feed_under_the_current_address(client, db_session):
    _member(db_session)
    db_session.add(MemberIdAlias(old_id="janet-doe", new_id="jane-doe", bioguide_id="D000001"))
    db_session.commit()
    root = ET.fromstring(client.get("/api/feed/members/janet-doe.xml").content)
    assert root.find(f"{A}link[@rel='self']").get("href") == "https://civitas-research.org/feed/members/jane-doe.xml"
    assert root.find(f"{A}link[@rel='alternate']").get("href") == "https://civitas-research.org/politicians/jane-doe"


def test_a_bills_feed_reads_the_newest_stored_actions_and_its_roll_calls(client, db_session):
    _member(db_session)
    old = [{"actionDate": "2026-03-01", "text": "Introduced in House", "actionCode": "Intro-H"}]
    new = old + [{"actionDate": "2026-03-04", "text": "Passed House.", "actionCode": "8000"}]
    db_session.add_all([
        ApiCache(tier="congress", cache_key="bill-actions-119-hr-1", data_json=json.dumps(old),
                 cached_at=datetime(2026, 3, 2)),
        ApiCache(tier="congress", cache_key="bill-record-actions-119-hr-1", data_json=json.dumps({"value": new}),
                 cached_at=datetime(2026, 3, 5)),
    ])
    db_session.commit()
    entries = _entries(client.get("/api/feed/bills/119/HR.1.xml"))
    assert [e["title"] for e in entries if e["term"] == "bill-action"] == [
        "H.R. 1: Passed House.", "H.R. 1: Introduced in House"]
    assert all(e["source"] == "https://www.congress.gov/bill/119th-congress/house-bill/1"
               for e in entries if e["term"] == "bill-action")
    vote = next(e for e in entries if e["term"] == "vote")
    assert vote["id"] == f"{TAG}vote/house-119-1-12"
    assert vote["title"] == "H.R. 1: House roll call 12, On Passage"


def test_a_member_feed_and_a_bill_feed_give_one_action_one_id(client, db_session):
    _member(db_session)
    text = "Referred to the Committee on Ways and Means."
    db_session.add(ApiCache(tier="congress", cache_key="bill-actions-119-hr-2",
                            data_json=json.dumps([{"actionDate": "2026-03-05", "text": text}]),
                            cached_at=datetime(2026, 3, 6)))
    db_session.commit()
    member = {e["id"] for e in _entries(client.get("/api/feed/members/jane-doe.xml")) if e["term"] == "bill-action"}
    bill = {e["id"] for e in _entries(client.get("/api/feed/bills/119/HR.2.xml"))}
    assert member == bill and len(bill) == 1


def test_an_agency_feed_holds_rules_openings_and_last_days_with_its_sub_agencies(client, db_session):
    now = datetime(2026, 3, 1)
    db_session.add_all([
        Agency(slug="dept", fr_id=1, name="Example Department", synced_at=now),
        Agency(slug="sub", fr_id=2, name="Example Service", parent_slug="dept", synced_at=now),
        ExploreDocument(doc_type="Proposed Rule", source="Federal Register", title="Widget safety", date="2026-02-01",
                        summary="The Service proposes a rule.", chamber="Regulatory", agency_name="Example Service",
                        agency_slug="sub", url="https://www.federalregister.gov/d/2026-00001",
                        comment_url="https://www.regulations.gov/x", comments_close_on="2026-03-02",
                        external_id="fr-reg-2026-00001"),
        ExploreDocument(doc_type="Notice", source="Federal Register", title="Meeting", date="2026-02-10",
                        chamber="Regulatory", agency_slug="dept", external_id="fr-reg-2026-00002"),
    ])
    db_session.commit()
    with patch("app.entity_feeds.comment_period_today", return_value="2026-03-03"):
        entries = _entries(client.get("/api/feed/agencies/dept.xml"))
    assert [(e["title"], e["published"][:10]) for e in entries] == [
        ("Last day to comment: Widget safety", "2026-03-02"),
        ("Proposed rule: Widget safety", "2026-02-01"),
    ]  # a notice with no comment period is not in it
    assert entries[1]["id"] == f"{TAG}fr/fr-reg-2026-00001"
    assert entries[1]["page"] == "https://civitas-research.org/explore/fr-2026-00001"
    assert entries[1]["source"] == "https://www.federalregister.gov/d/2026-00001"
    assert entries[0]["source"] == "https://www.regulations.gov/x"


def test_a_monitor_feed_titles_each_update_with_the_outlets_headline_not_its_summary(client, db_session):
    m = NationalMonitor(slug="a-story", title="A Story")
    db_session.add(m)
    db_session.flush()
    db_session.add(MonitorUpdate(monitor_id=m.id, date="2026-03-01", summary="Model words.",
                                 source_url="https://example.org/a", source_name="Example News",
                                 article_title="Something happened"))
    db_session.commit()
    [entry] = _entries(client.get("/api/feed/monitors/a-story.xml"))
    assert entry["title"] == "Something happened"
    assert entry["source"] == "https://example.org/a"
    assert entry["page"] == "https://civitas-research.org/action?tab=monitors&monitor=a-story"
    assert "Model words" not in entry["content"]


@pytest.mark.parametrize("path", [
    "/api/feed/members/nobody.xml", "/api/feed/members/jane-doe", "/api/feed/bills/119/HR.99.xml",
    "/api/feed/bills/119/XX.1.xml", "/api/feed/agencies/nope.xml", "/api/feed/monitors/nope.xml",
])
def test_unknown_followed_feeds_are_404(client, db_session, path):
    _member(db_session)
    assert client.get(path).status_code == 404


def test_the_index_lists_the_followed_kinds(client):
    kinds = client.get("/api/feeds").json()["followed"]
    assert [k["kind"] for k in kinds] == ["member", "bill", "agency", "monitor"]
    assert all(k["pattern"].startswith("/feed/") and k["pattern"].endswith(".xml") for k in kinds)
