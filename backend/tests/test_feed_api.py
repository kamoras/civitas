"""The Atom feeds (app/api/feed.py)."""

from datetime import datetime
from xml.etree import ElementTree as ET

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import feed as feed_api
from app.database import get_db
from app.models import BroadcastPost

A = "{http://www.w3.org/2005/Atom}"


@pytest.fixture
def client(db_session):
    app = FastAPI()
    app.include_router(feed_api.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _post(db, kind="issue", when="2026-09-28T12:00:00", state=None, **kw):
    post = BroadcastPost(
        kind=kind, state=state, published_at=datetime.fromisoformat(when),
        title=kw.pop("title", f"{kind} title"), text=kw.pop("text", f"{kind} text."),
        url=kw.pop("url", f"https://civitas-research.org/{kind}"), **kw,
    )
    db.add(post)
    db.commit()
    return post


def _feed(resp):
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/atom+xml; charset=utf-8"
    return ET.fromstring(resp.content)


def test_an_empty_feed_is_still_a_valid_feed(client):
    root = _feed(client.get("/api/feed/all.xml"))
    assert root.tag == f"{A}feed"
    assert root.findtext(f"{A}id") == "https://civitas-research.org/feed.xml"
    assert root.findtext(f"{A}title") == "Civitas"
    assert root.findtext(f"{A}updated") == "2026-09-29T00:00:00Z"
    assert root.findtext(f"{A}author/{A}name") == "Civitas"
    assert root.find(f"{A}link[@rel='self']").get("href") == "https://civitas-research.org/feed.xml"
    assert root.findall(f"{A}entry") == []


def test_entries_newest_first_with_everything_a_reader_needs(client, db_session):
    older = _post(db_session, "congress_day", "2026-09-27T22:00:00", title="Congress, Friday",
                  text="The Senate met.", url="https://civitas-research.org/congress/2026-09-26")
    newer = _post(db_session, "issue", "2026-09-28T13:05:09")

    root = _feed(client.get("/api/feed/all.xml"))
    entries = root.findall(f"{A}entry")
    assert [e.findtext(f"{A}id") for e in entries] == [
        f"tag:civitas-research.org,2026:post/{newer.id}",
        f"tag:civitas-research.org,2026:post/{older.id}",
    ]
    assert root.findtext(f"{A}updated") == "2026-09-28T13:05:09Z"
    last = entries[1]
    assert last.findtext(f"{A}title") == "Congress, Friday"
    assert last.findtext(f"{A}content") == "The Senate met."
    assert last.find(f"{A}content").get("type") == "text"
    assert last.find(f"{A}link").get("href") == "https://civitas-research.org/congress/2026-09-26"
    assert last.findtext(f"{A}published") == "2026-09-27T22:00:00Z"
    assert last.find(f"{A}category").get("term") == "congress_day"


def test_a_topic_feed_holds_only_its_kinds(client, db_session):
    for kind in ("issue", "congress_day", "congress_week", "spotlight", "race"):
        _post(db_session, kind)
    titles = lambda path: [e.findtext(f"{A}title") for e in _feed(client.get(path)).findall(f"{A}entry")]  # noqa: E731
    assert sorted(titles("/api/feed/congress.xml")) == ["congress_day title", "congress_week title"]
    assert titles("/api/feed/elections.xml") == ["race title"]
    assert len(titles("/api/feed/all.xml")) == 5


def test_a_state_feed_holds_that_states_posts(client, db_session):
    _post(db_session, "race", state="GA", title="GA race")
    _post(db_session, "spotlight", state="GA", title="GA member")
    _post(db_session, "race", state="NC", title="NC race")
    _post(db_session, "issue")
    root = _feed(client.get("/api/feed/states/ga.xml"))
    assert root.findtext(f"{A}title") == "Civitas: Georgia"
    assert root.findtext(f"{A}id") == "https://civitas-research.org/feed/states/GA.xml"
    assert sorted(e.findtext(f"{A}title") for e in root.findall(f"{A}entry")) == ["GA member", "GA race"]


@pytest.mark.parametrize("path", [
    "/api/feed/nope.xml", "/api/feed/all", "/api/feed/all.json",
    "/api/feed/states/ZZ.xml", "/api/feed/states/GA",
])
def test_unknown_feeds_are_404(client, path):
    assert client.get(path).status_code == 404


def test_text_xml_cannot_carry_is_dropped_not_fatal(client, db_session):
    _post(db_session, text="A vote\x0b on <S. 1> & more.")
    entry = _feed(client.get("/api/feed/all.xml")).find(f"{A}entry")
    assert entry.findtext(f"{A}content") == "A vote on <S. 1> & more."


def test_the_length_is_capped(client, db_session, monkeypatch):
    monkeypatch.setattr(feed_api, "FEED_LENGTH", 3)
    for i in range(5):
        _post(db_session, when=f"2026-09-2{i}T12:00:00")
    assert len(_feed(client.get("/api/feed/all.xml")).findall(f"{A}entry")) == 3


def test_a_poller_with_the_current_etag_gets_a_304(client, db_session):
    _post(db_session)
    first = client.get("/api/feed/all.xml")
    assert "max-age" in first.headers["cache-control"]
    assert first.headers["last-modified"] == "Mon, 28 Sep 2026 12:00:00 GMT"
    again = client.get("/api/feed/all.xml", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304 and again.content == b""
    _post(db_session, when="2026-09-29T12:00:00")
    assert client.get("/api/feed/all.xml", headers={"If-None-Match": first.headers["etag"]}).status_code == 200


def test_head_is_answered(client):
    assert client.head("/api/feed/all.xml").status_code == 200


def test_the_index_lists_every_feed_and_state(client):
    body = client.get("/api/feeds").json()
    assert [f["path"] for f in body["feeds"]] == [
        "/feed.xml", "/feed/issues.xml", "/feed/congress.xml", "/feed/members.xml", "/feed/elections.xml",
    ]
    georgia = next(s for s in body["states"] if s["code"] == "GA")
    assert georgia == {"code": "GA", "name": "Georgia", "path": "/feed/states/GA.xml"}
    assert len(body["states"]) == 51
