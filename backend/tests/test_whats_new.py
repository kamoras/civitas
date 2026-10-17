"""New on Civitas (app/whats_new.py): each feature entry posted once, by the
volume rule, in the template, and never for a page that doesn't load."""

import re
from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import broadcast, whats_new
from app.api import feed as feed_api
from app.database import get_db
from app.models import BroadcastPost
from app.pipeline.analyze.bluesky_utils import BSKY_MAX_CHARS

_EDT = timedelta(hours=4)  # 2026-10 dates are on daylight time
_ENTRIES = [
    {"id": "new-map", "date": "2026-10-16", "title": "A map", "summary": "Each seat on a map.", "url": "/map"},
    {"id": "older", "date": "2026-10-10", "title": "A list", "summary": "Each bill in a list.", "url": "/list"},
    {"id": "stale", "date": "2026-09-01", "title": "Old", "summary": "Shipped long ago.", "url": "/old"},
]


def _eastern(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute) + _EDT


@pytest.fixture
def setup(monkeypatch, freeze_utcnow, bluesky_configured, link_cards):
    """Ten-to-one Eastern posting window, three entries, every page 200;
    returns (statuses, alerts, sent) to steer and read."""
    monkeypatch.setattr(broadcast, "_windows_cache", [(10, 1)])
    monkeypatch.setattr(whats_new, "entries", lambda: _ENTRIES)
    statuses: dict[str, int | None] = {}
    monkeypatch.setattr(whats_new, "page_status", lambda e: statuses.get(e["url"], 200))
    alerts: list[str] = []
    monkeypatch.setattr(whats_new, "send_ops_alert", lambda subject, body, **kw: alerts.append(kw["dedupe_key"]))
    freeze_utcnow(_eastern(16, 12))
    return statuses, alerts, bluesky_configured


def _other_post(db, when, kind="issue"):
    db.add(BroadcastPost(kind=kind, subject=f"{kind}:{when.isoformat()}", title="t", text="t.",
                         url="https://civitas-research.org/x", published_at=when, bsky_status="sent"))
    db.commit()


def _features(db):
    return db.query(BroadcastPost).filter(BroadcastPost.kind == "feature").order_by(BroadcastPost.id).all()


def test_posts_the_newest_entry_in_the_template(db_session, setup):
    _, _, sent = setup
    assert whats_new.post_whats_new(db_session) == "posted new-map"
    (post,) = _features(db_session)
    assert post.subject == "feature:new-map"
    assert post.title == "New on Civitas Research: A map"
    assert sent == [("New: A map. Each seat on a map.", "https://civitas-research.org/map")]


def test_once_per_entry_and_one_a_day(db_session, setup, freeze_utcnow):
    whats_new.post_whats_new(db_session)
    assert whats_new.post_whats_new(db_session) == "already posted today"
    # Next day: the next entry, never the first one again (a restart or a
    # deploy reads the same rows).
    freeze_utcnow(_eastern(17, 12))
    assert whats_new.post_whats_new(db_session) == "posted older"
    freeze_utcnow(_eastern(18, 12))
    # "stale" was released over ANNOUNCE_WITHIN ago: never posted.
    assert whats_new.post_whats_new(db_session) == "nothing due"
    assert [p.subject for p in _features(db_session)] == ["feature:new-map", "feature:older"]


def test_only_inside_the_posting_window(db_session, setup, freeze_utcnow):
    freeze_utcnow(_eastern(16, 7))
    assert whats_new.post_whats_new(db_session) == "outside the posting window"
    assert _features(db_session) == []


def test_held_on_a_busier_day_than_usual(db_session, setup):
    # A typical day over the fortnight before: one post a day.
    for day in range(2, 16):
        _other_post(db_session, _eastern(day, 14))
    # Two in the last 24 hours: busier than usual, so nothing is added.
    _other_post(db_session, _eastern(16, 9))
    _other_post(db_session, _eastern(16, 11))
    assert whats_new.busy_day_cap(db_session, _eastern(16, 12)) == 1
    assert whats_new.post_whats_new(db_session).startswith("held: 3 posts")
    assert _features(db_session) == []


def test_feature_posts_do_not_set_their_own_cap(db_session, setup):
    for day in range(2, 16):
        _other_post(db_session, _eastern(day, 14), kind="feature")
    assert whats_new.busy_day_cap(db_session, _eastern(16, 12)) == 0


def test_an_unreachable_page_is_an_outage_not_a_launch(db_session, setup):
    statuses, alerts, sent = setup
    statuses["/map"] = None
    assert whats_new.post_whats_new(db_session) == "held: /map unreachable"
    assert (_features(db_session), alerts, sent) == ([], [], [])


def test_a_404_page_is_skipped_with_one_alert(db_session, setup):
    statuses, alerts, _ = setup
    statuses["/map"] = 404
    assert whats_new.post_whats_new(db_session) == "posted older"
    assert alerts == ["whats-new-page:new-map"]


def test_the_page_status_reads_the_frontend_without_the_fragment(monkeypatch):
    calls = []

    class Resp:
        status_code = 503

    monkeypatch.setattr(whats_new.httpx, "get", lambda url, **kw: calls.append(url) or Resp())
    assert whats_new.page_status({"url": "/about/data#computed-lean"}) is None
    assert calls == [whats_new.settings.FRONTEND_INTERNAL_URL.rstrip("/") + "/about/data"]


def test_the_feed_and_the_api_list(db_session, setup):
    whats_new.post_whats_new(db_session)
    app = FastAPI()
    app.include_router(feed_api.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    feed = client.get("/api/feed/new.xml")
    assert feed.status_code == 200 and b"New on Civitas Research: A map" in feed.content
    listed = client.get("/api/whats-new").json()
    assert listed["feed"] == "/feed/new.xml"
    assert [e["id"] for e in listed["entries"]] == ["new-map", "older", "stale"]


# -- the checked-in entries ------------------------------------------------

_PRONOUN = re.compile(r"\b(he|she|him|his|her|hers|they|them|their|theirs)\b", re.IGNORECASE)
_EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿️]")


def test_every_checked_in_entry_meets_the_shown_text_rules():
    whats_new.entries.cache_clear()
    entries = whats_new.entries()
    assert entries, "the queue has content"
    assert len({e["id"] for e in entries}) == len(entries)
    dates = [date.fromisoformat(e["date"]) for e in entries]
    assert dates == sorted(dates, reverse=True), "newest first"
    for e in entries:
        assert set(e) == {"id", "date", "title", "summary", "url"}, e["id"]
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", e["id"]), e["id"]
        assert e["url"].startswith("/") and not e["url"].startswith("//"), e["id"]
        assert not e["title"].endswith("."), e["id"]
        assert e["summary"].endswith(".") and e["summary"].count(". ") == 0, e["id"]
        for text in (e["title"], e["summary"]):
            assert "—" not in text and not _EMOJI.search(text) and not _PRONOUN.search(text), e["id"]
            assert not re.search(r"\b(19|20)\d\d\b|\b1\d\dth\b", text), f"{e['id']}: names a year or Congress"
        # The whole post, with its link, fits one Bluesky post uncut.
        assert len(whats_new.post_text(e)) + 1 + len(whats_new.page_url(e)) <= BSKY_MAX_CHARS, e["id"]
