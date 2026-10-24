"""Outlet reposts: a post is reposted only when it links an article a live
issue cites (bluesky_engagement), never by a similarity threshold."""

import json
from types import SimpleNamespace

import atproto
import httpx

from app.models import ActionIssue, ApiCache
from app.pipeline.analyze import bluesky_engagement as be


def test_an_article_is_keyed_without_scheme_query_or_trailing_slash():
    assert be.article_key("https://www.npr.org/2026/10/01/x/?utm=1#top") == "npr.org/2026/10/01/x"
    assert be.article_key("http://npr.org/2026/10/01/x") == "npr.org/2026/10/01/x"
    assert be.article_key(None) is None and be.article_key("not a url") is None


def test_the_sources_are_what_the_live_issues_cite():
    a = SimpleNamespace(source_urls=json.dumps(["https://apnews.com/article/one", "https://npr.org/two/"]))
    b = SimpleNamespace(source_urls="not json")
    sources = be.issue_sources([a, b])
    assert sources == {"apnews.com/article/one": a, "npr.org/two": a}


def test_the_linked_article_is_read_from_the_embed_alone_or_beside_media():
    external = SimpleNamespace(uri="https://n.pr/abc")
    assert be.linked_article(SimpleNamespace(embed=SimpleNamespace(external=external))) == "https://n.pr/abc"
    with_media = SimpleNamespace(embed=SimpleNamespace(external=None, media=SimpleNamespace(external=external)))
    assert be.linked_article(with_media) == "https://n.pr/abc"
    assert be.linked_article(SimpleNamespace(embed=None)) is None


def test_a_short_link_is_followed_and_a_cited_link_is_not():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host == "n.pr":
            return httpx.Response(301, headers={"Location": "https://www.npr.org/2026/10/01/story/"})
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    known = {"npr.org/2026/10/01/story": object()}
    assert be.resolve_key("https://n.pr/abc", known, client) == "npr.org/2026/10/01/story"
    calls.clear()
    assert be.resolve_key("https://npr.org/2026/10/01/story", known, client) == "npr.org/2026/10/01/story"
    assert calls == []  # already a cited article: nothing fetched

    def down(request):
        raise httpx.ConnectError("down")
    assert be.resolve_key("https://n.pr/abc", {}, httpx.Client(transport=httpx.MockTransport(down))) is None
    # A rate limit or a server error says nothing about the article yet.
    for status in (429, 503):
        busy = httpx.Client(transport=httpx.MockTransport(lambda r, s=status: httpx.Response(s)))
        assert be.resolve_key("https://n.pr/abc", {}, busy) is None
    # A site refusing the bot is still at the article's address.
    refused = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    assert be.resolve_key("https://npr.org/2026/10/01/x", {}, refused) == "npr.org/2026/10/01/x"


def test_only_a_post_linking_a_cited_article_is_reposted(db_session, monkeypatch):
    db_session.add(ActionIssue(date="2026-10-01", rank=1, title="Senate passes bill", is_current=True,
                               source_urls=json.dumps(["https://www.npr.org/2026/10/01/story"])))
    db_session.commit()
    monkeypatch.setattr(be.settings, "BSKY_HANDLE", "civitas.test", raising=False)
    monkeypatch.setattr(be.settings, "BSKY_APP_PASSWORD", "x", raising=False)
    monkeypatch.setattr(be, "NEWS_OUTLET_HANDLES", ["npr.org"])
    now = be.datetime.now(be.UTC).isoformat()

    def post(cid, link):
        embed = SimpleNamespace(external=SimpleNamespace(uri=link)) if link else None
        return SimpleNamespace(reason=None, post=SimpleNamespace(cid=cid, uri=f"at://{cid}", indexed_at=now,
                                                                 embed=embed, record=SimpleNamespace(text="x")))

    feed = [post("c1", "https://npr.org/2026/10/01/story/?s=bsky"), post("c2", "https://npr.org/other"),
            post("c3", None)]
    reposted = []

    class FakeClient:
        def login(self, *a):
            pass

        def get_author_feed(self, **k):
            return SimpleNamespace(feed=feed)

        def repost(self, uri, cid):
            reposted.append(cid)

        def like(self, uri, cid):
            pass

    monkeypatch.setattr(atproto, "Client", FakeClient)
    monkeypatch.setattr(be, "resolve_key", lambda url, known, client: be.article_key(url))
    be.engage_with_news_posts(db_session)
    assert reposted == ["c1"]
    assert db_session.get(ApiCache, ("bsky_engagement", "c1")).data_json == be.REPOSTED
    assert db_session.get(ApiCache, ("bsky_engagement", "c2")).data_json == "npr.org/other"
    # A later run neither reposts c1 again nor follows c2's link again.
    monkeypatch.setattr(be, "resolve_key", lambda *a: (_ for _ in ()).throw(AssertionError("followed again")))
    be.engage_with_news_posts(db_session)
    assert reposted == ["c1"]
    # Once an issue cites c2's article, c2 is reposted from its recorded key.
    db_session.add(ActionIssue(date="2026-10-02", rank=2, title="Other story", is_current=True,
                               source_urls=json.dumps(["https://npr.org/other"])))
    db_session.commit()
    be.engage_with_news_posts(db_session)
    assert reposted == ["c1", "c2"]


def test_a_link_not_followed_now_is_tried_again_next_run(db_session, monkeypatch):
    db_session.add(ActionIssue(date="2026-10-01", rank=1, title="Senate passes bill", is_current=True,
                               source_urls=json.dumps(["https://www.npr.org/2026/10/01/story"])))
    db_session.commit()
    monkeypatch.setattr(be.settings, "BSKY_HANDLE", "civitas.test", raising=False)
    monkeypatch.setattr(be.settings, "BSKY_APP_PASSWORD", "x", raising=False)
    monkeypatch.setattr(be, "NEWS_OUTLET_HANDLES", ["npr.org"])
    item = SimpleNamespace(reason=None, post=SimpleNamespace(
        cid="c1", uri="at://c1", indexed_at=be.datetime.now(be.UTC).isoformat(),
        embed=SimpleNamespace(external=SimpleNamespace(uri="https://n.pr/abc")), record=SimpleNamespace(text="x")))
    reposted = []

    class FakeClient:
        def login(self, *a):
            pass

        def get_author_feed(self, **k):
            return SimpleNamespace(feed=[item])

        def repost(self, uri, cid):
            reposted.append(cid)

        def like(self, uri, cid):
            pass

    monkeypatch.setattr(atproto, "Client", FakeClient)
    monkeypatch.setattr(be, "resolve_key", lambda *a: None)  # rate-limited
    be.engage_with_news_posts(db_session)
    assert reposted == [] and db_session.get(ApiCache, ("bsky_engagement", "c1")) is None
    monkeypatch.setattr(be, "resolve_key", lambda *a: "npr.org/2026/10/01/story")
    be.engage_with_news_posts(db_session)
    assert reposted == ["c1"]
