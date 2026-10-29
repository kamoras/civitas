"""Threads and Instagram (app/social/): each stored post fans out to the
configured channels through Meta's container flow, with the HTTP layer
mocked (graph.TRANSPORT)."""

import io
import json
from datetime import timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from PIL import Image
from sqlalchemy.orm import sessionmaker

from app import broadcast
from app.config import settings
from app.models import BroadcastDelivery, BroadcastPost
from app.pipeline.analyze.bluesky_utils import PostImage
from app.social import graph, threads
from app.time_utils import utcnow

URL = "https://civitas-research.org/issue/x"


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (1200, 630), (200, 180, 150)).save(out, "PNG")
    return out.getvalue()


class Meta:
    """A stand-in for both APIs: records every request, answers each
    container FINISHED after `pending` IN_PROGRESS reads."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.pending = 0
        self.refuse: set[str] = set()  # hosts that answer 400
        self.refresh_ok = True
        self.status = "FINISHED"

    def form(self, i: int) -> dict:
        req = self.requests[i]
        body = parse_qs(req.content.decode()) if req.method == "POST" else parse_qs(req.url.query.decode())
        return {k: v[0] for k, v in body.items()}

    def posts(self, host: str) -> list[dict]:
        return [self.form(i) for i, r in enumerate(self.requests)
                if r.method == "POST" and r.url.host == host and r.url.path.endswith(("/threads", "/media"))]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.url.host in self.refuse:
            return httpx.Response(400, json={"error": {"message": "Invalid parameter", "type": "OAuthException",
                                                       "code": 100}})
        if path.endswith("refresh_access_token"):
            if not self.refresh_ok:
                return httpx.Response(400, json={"error": {"message": "Session has expired", "code": 190}})
            return httpx.Response(200, json={"access_token": "refreshed-token", "token_type": "bearer",
                                             "expires_in": 5183944})
        if path.endswith(("/threads", "/media")):
            return httpx.Response(200, json={"id": "container-1"})
        if path.endswith(("/threads_publish", "/media_publish")):
            return httpx.Response(200, json={"id": "media-9"})
        if path.endswith("/container-1"):
            if self.pending:
                self.pending -= 1
                status = "IN_PROGRESS"
            else:
                status = self.status
            return httpx.Response(200, json={"status": status, "status_code": status})
        return httpx.Response(404, json={})


@pytest.fixture
def meta(monkeypatch, db_session):
    fake = Meta()
    monkeypatch.setattr(graph, "TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setattr(graph, "POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(graph, "POLL_TIMEOUT_S", 0.05)
    alerts, resolved = [], []
    monkeypatch.setattr(graph, "send_ops_alert", lambda s, b, **kw: alerts.append((s, b, kw)) or True)
    monkeypatch.setattr(graph, "resolve_ops_alert", lambda c: resolved.append(c) or 1)
    fake.alerts, fake.resolved = alerts, resolved
    # The sender thread, run inline on the test's database.
    monkeypatch.setattr(broadcast, "SessionLocal", lambda: sessionmaker(bind=db_session.get_bind())())
    monkeypatch.setattr(broadcast, "_spawn", lambda ids: broadcast._send_channels(ids))
    monkeypatch.setattr(broadcast, "_post_image", lambda post: PostImage(_png(), post.alt_text, 1200, 630)
                        if post.image_url and post.alt_text else None)
    return fake


@pytest.fixture
def both(monkeypatch):
    for name, value in (("THREADS_USER_ID", "111"), ("THREADS_ACCESS_TOKEN", "env-threads"),
                        ("INSTAGRAM_USER_ID", "222"), ("INSTAGRAM_ACCESS_TOKEN", "env-insta")):
        monkeypatch.setattr(settings, name, value)


def _publish(db, **kw):
    args = dict(kind="issue", subject="issue:x", title="A title", text="A post.", url=URL)
    args.update(kw)
    return broadcast.publish(db, **args)


def _deliveries(db, post) -> dict[str, BroadcastDelivery]:
    return {d.channel: d for d in db.query(BroadcastDelivery).filter_by(post_id=post.id)}


def test_off_when_unset_nothing_is_requested(db_session, meta):
    post = _publish(db_session)
    assert _deliveries(db_session, post) == {}
    assert meta.requests == []


def test_threads_text_post_goes_through_the_container_flow(db_session, meta, both):
    meta.pending = 2
    post = _publish(db_session)
    d = _deliveries(db_session, post)
    assert (d["threads"].status, d["threads"].remote_id, d["threads"].attempts) == ("sent", "media-9", 1)
    [created] = meta.posts("graph.threads.com")
    assert created == {"media_type": "TEXT", "text": f"A post. {URL}", "link_attachment": URL,
                       "access_token": "env-threads"}
    # The token goes in the body, never in a URL.
    assert all("env-threads" not in str(r.url) for r in meta.requests if r.method == "POST")
    paths = [r.url.path for r in meta.requests if r.url.host == "graph.threads.com"]
    assert paths == ["/v1.0/111/threads", *["/v1.0/container-1"] * 3, "/v1.0/111/threads_publish"]
    # Instagram can't take a post with no picture: skipped, not failed.
    assert (d["instagram"].status, d["instagram"].error) == ("skipped", "no picture to post (Instagram needs one)")
    assert meta.posts("graph.instagram.com") == []


def test_threads_text_fits_500_bytes_with_the_link_whole():
    body = threads.compose("A sentence about the record. " * 40, URL)
    assert len(body.encode()) <= 500 and body.endswith(f". {URL}")
    assert threads.compose("é" * 600, URL).endswith(URL)
    assert len(threads.compose("é" * 600, URL).encode()) <= 500


def test_a_post_with_a_picture_goes_to_both_as_a_jpeg(db_session, meta, both):
    post = _publish(db_session, image_url="https://civitas-research.org/api/og?vote=x", alt_text="A vote card.")
    jpeg = f"https://civitas-research.org/api/cards/posts/{post.id}.jpg"
    [t] = meta.posts("graph.threads.com")
    assert (t["media_type"], t["image_url"], t["alt_text"]) == ("IMAGE", jpeg, "A vote card.")
    [i] = meta.posts("graph.instagram.com")
    assert (i["image_url"], i["alt_text"], i["caption"]) == (jpeg, "A vote card.", f"A post. {URL}")
    assert {c: d.status for c, d in _deliveries(db_session, post).items()} == {"threads": "sent", "instagram": "sent"}


def test_the_jpeg_copy_is_a_jpeg(db_session, meta):
    post = _publish(db_session, image_url="https://civitas-research.org/api/og?vote=x", alt_text="A card.")
    data = broadcast.post_image_jpeg(post)
    assert data[:2] == b"\xff\xd8" and Image.open(io.BytesIO(data)).size == (1200, 630)


def test_the_jpeg_route_serves_a_post_picture_and_nothing_else(db_session, meta):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import cards
    from app.database import get_db

    post = _publish(db_session, image_url="https://civitas-research.org/api/og?vote=x", alt_text="A card.")
    bare = _publish(db_session, subject="issue:y")
    app = FastAPI()
    app.include_router(cards.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    ok = client.get(f"/api/cards/posts/{post.id}.jpg")
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/jpeg" and ok.content[:2] == b"\xff\xd8"
    assert "max-age" in ok.headers["cache-control"]
    assert client.get(f"/api/cards/posts/{bare.id}.jpg").status_code == 404
    assert client.get("/api/cards/posts/999.jpg").status_code == 404


def test_a_container_never_ready_fails_the_delivery(db_session, meta, both):
    meta.pending = 99
    post = _publish(db_session)
    d = _deliveries(db_session, post)["threads"]
    assert d.status == "failed" and "not ready" in d.error
    assert [r for r in meta.requests if r.url.path.endswith("threads_publish")] == []


def test_one_channel_failing_leaves_the_other_posting(db_session, meta, both, bluesky_configured):
    meta.refuse = {"graph.threads.com"}
    post = _publish(db_session, image_url="https://civitas-research.org/api/og?vote=x", alt_text="A card.")
    d = _deliveries(db_session, post)
    assert d["threads"].status == "failed"
    assert "HTTP 400" in d["threads"].error and "env-threads" not in d["threads"].error
    assert d["instagram"].status == "sent"
    assert post.bsky_status == "sent"
    assert db_session.query(BroadcastPost).count() == 1


def test_a_failed_delivery_is_retried_by_deliver_pending(db_session, meta, both):
    meta.refuse = {"graph.threads.com"}
    post = _publish(db_session)
    meta.refuse = set()
    assert broadcast.deliver_pending(db_session) == 0
    assert _deliveries(db_session, post)["threads"].status == "failed"  # too soon
    d = _deliveries(db_session, post)["threads"]
    d.last_attempt_at = utcnow() - broadcast.RETRY_AFTER - timedelta(minutes=1)
    db_session.commit()
    broadcast.deliver_pending(db_session)
    db_session.refresh(d)
    assert (d.status, d.attempts) == ("sent", 2)


def test_a_post_kept_off_bluesky_is_kept_off_every_channel(db_session, meta, both):
    post = _publish(db_session, bluesky=False)
    assert _deliveries(db_session, post) == {}


def test_a_correction_goes_only_where_the_flip_was_shown(db_session, meta, both):
    flip = _publish(db_session, kind="result", subject="result:x:flip")
    assert _deliveries(db_session, flip)["threads"].status == "sent"
    fix = _publish(db_session, kind="result", subject="result:x:correction", bluesky=False, follows=flip)
    assert set(_deliveries(db_session, fix)) == {"threads"}  # Instagram skipped the flip
    meta.refuse = {"graph.threads.com"}
    flip2 = _publish(db_session, kind="result", subject="result:y:flip")
    fix2 = _publish(db_session, kind="result", subject="result:y:correction", follows=flip2)
    assert _deliveries(db_session, fix2) == {}


def _tokens() -> dict:
    with open(graph._path()) as fh:
        return json.load(fh)


def test_the_token_is_refreshed_once_due_and_kept(db_session, meta, both, monkeypatch):
    graph.keep_token_fresh(threads.PLATFORM)  # adopts the .env token; under a day old, not refreshed
    assert [r for r in meta.requests if "refresh" in r.url.path] == []
    later = utcnow() + timedelta(days=2)
    monkeypatch.setattr(graph, "utcnow", lambda: later)
    graph.keep_token_fresh(threads.PLATFORM)
    [refresh] = [r for r in meta.requests if "refresh" in r.url.path]
    assert refresh.url.host == "graph.threads.com" and refresh.url.params["grant_type"] == "th_refresh_token"
    kept = _tokens()["threads"]
    assert kept["token"] == "refreshed-token"
    assert kept["expires_at"] == graph._iso(later + timedelta(seconds=5183944))
    assert meta.resolved == ["social-token-threads"]
    # Not again within the week; the refreshed token is the one used.
    graph.keep_token_fresh(threads.PLATFORM)
    assert len([r for r in meta.requests if "refresh" in r.url.path]) == 1
    assert graph.token(threads.PLATFORM) == "refreshed-token"
    # A new token pasted into .env replaces the kept one.
    monkeypatch.setattr(settings, "THREADS_ACCESS_TOKEN", "env-threads-2")
    assert graph.token(threads.PLATFORM) == "env-threads-2"


def test_a_failed_refresh_alerts_and_never_names_the_token(db_session, meta, both, monkeypatch, caplog):
    graph.keep_token_fresh(threads.PLATFORM)
    meta.refresh_ok = False
    later = utcnow() + timedelta(days=2)
    monkeypatch.setattr(graph, "utcnow", lambda: later)
    graph.keep_token_fresh(threads.PLATFORM)
    [(subject, body, kw)] = meta.alerts
    assert kw["condition"] == "social-token-threads" and "refresh failed" in subject
    assert "env-threads" not in body and "env-threads" not in caplog.text
    assert _tokens()["threads"]["token"] == "env-threads"  # kept, tried again tomorrow


def test_a_token_near_expiry_alerts(db_session, meta, both, monkeypatch):
    graph.keep_token_fresh(threads.PLATFORM)
    soon = utcnow() + timedelta(days=3)
    graph._update(threads.PLATFORM, lambda e: {**e, "refreshed_at": graph._iso(utcnow()),
                                               "expires_at": graph._iso(soon)})
    graph.keep_token_fresh(threads.PLATFORM)
    [(subject, _, kw)] = meta.alerts
    assert subject == f"Threads token expires {soon.date().isoformat()}"
    assert kw["condition"] == "social-token-threads"


def test_a_channel_out_of_tries_is_not_resent_beside_one_still_due(db_session, meta, both):
    post = _publish(db_session, image_url="https://civitas-research.org/api/og?vote=x", alt_text="A card.")
    d = _deliveries(db_session, post)
    d["threads"].status, d["threads"].attempts = "failed", broadcast.MAX_BSKY_ATTEMPTS
    d["instagram"].status, d["instagram"].last_attempt_at = "failed", utcnow() - timedelta(hours=2)
    db_session.commit()
    before = len(meta.posts("graph.threads.com"))
    broadcast.deliver_pending(db_session)
    assert len(meta.posts("graph.threads.com")) == before
    db_session.refresh(d["instagram"])
    assert d["instagram"].status == "sent"
