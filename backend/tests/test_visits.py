"""Tests for page-view tracking (app/api/visits.py).

PageView is deliberately separate from SiteVisit's unique-visitor dedup —
these tests cover the normalization that keeps "most visited pages"
meaningful (collapsing per-id routes to a template) and confirm repeat
views actually accumulate rather than no-op like SiteVisit does.
"""

import asyncio
import pathlib
import sqlite3
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.api import throttle, visits
from app.api.admin import admin_top_pages
from app.api.visits import (
    _extract_issue_public_id, _VisitEvent, _normalize_path, _write_visit_batch, track_visit,
)
from app.issue_ids import to_public_id
from app.models import IssueView, PageView, SiteVisit
from tests.visits_helpers import _drain_queue_and_write, _make_request, _view


class TestNormalizePath:
    @pytest.mark.parametrize("path, expected", [
        pytest.param("/leaderboard", "/leaderboard", id="known_static_path_passes_through"),
        pytest.param("/", "/", id="root_passes_through"),
        pytest.param("/politicians/chuck-grassley", "/politicians/[id]", id="politician_id_collapses"),
        pytest.param("/politicians/jane-doe", "/politicians/[id]", id="another_politician_id_collapses"),
        pytest.param("/issue/312", "/issue/[id]", id="issue_id_collapses"),
        pytest.param("/explore/987", "/explore/[id]", id="explore_id_collapses"),
        # "/politicians" itself (no id segment) is the directory page, not
        # a per-id route — must not collapse into "/politicians/[id]".
        pytest.param("/politicians", "/politicians", id="bare_dynamic_prefix_without_id_is_static_path"),
        pytest.param("/leaderboard/", "/leaderboard", id="trailing_slash_ignored"),
        pytest.param("/leaderboard?tab=house", "/leaderboard", id="query_string_ignored"),
        pytest.param("/some/random/junk", "/other", id="unknown_path_buckets_to_other"),
        pytest.param("", "/", id="empty_path_is_root"),
        # /bills shipped without a matching visits.py entry and silently
        # drained into "/other" until this was noticed (2026-07) — pin the
        # fix so a future page addition can't repeat it unnoticed.
        pytest.param("/bills", "/bills", id="recently_added_pages_are_tracked-bills"),
        pytest.param("/feedback", "/feedback", id="recently_added_pages_are_tracked-feedback"),
        # A bill page and a day report are different routes under one
        # prefix: the longer prefix must win.
        pytest.param("/congress", "/congress", id="congress_index"),
        pytest.param("/congress/bills", "/congress/bills", id="congress_bills_index"),
        pytest.param("/congress/bills/S.4668", "/congress/bills/[id]", id="congress_bill_page"),
        pytest.param("/congress/2026-09-24", "/congress/[id]", id="congress_day_report"),
    ])
    def test_normalize_path(self, path, expected):
        assert _normalize_path(path) == expected


_PID = to_public_id(42)


class TestExtractIssuePublicId:
    @pytest.mark.parametrize("path, expected", [
        pytest.param(f"/issue/{_PID}", _PID, id="well_formed_issue_path_extracts_the_id"),
        pytest.param(f"/issue/{_PID}/", _PID, id="trailing_slash_ignored"),
        pytest.param(f"/issue/{_PID}?ref=bsky", _PID, id="query_string_ignored"),
        # Not from_public_id's own letter-prefixed hex format — a made-up
        # string here must not grow IssueView with junk (see
        # _extract_issue_public_id's docstring).
        pytest.param("/issue/not-a-real-id", None, id="malformed_id_is_rejected"),
        pytest.param("/politicians/chuck-grassley", None, id="non_issue_path"),
        pytest.param("/issue", None, id="bare_issue_prefix"),
        pytest.param("/", None, id="root"),
    ])
    def test_extract_issue_public_id(self, path, expected):
        assert _extract_issue_public_id(path) == expected


class TestKnownRoutesStayInSync:
    def test_every_top_level_frontend_page_is_tracked_or_dynamic(self):
        """Every real top-level page.tsx route must resolve to something
        other than "/other" — otherwise a new page silently drains into
        the catch-all bucket exactly like /bills did (see
        TestNormalizePath's recently_added_pages_are_tracked). /admin is exempt: the
        frontend proxy explicitly excludes it from tracking (see
        frontend/src/proxy.ts's matcher)."""
        app_dir = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "app"
        if not app_dir.is_dir():
            return  # frontend checkout not present in this environment
        top_level_routes = sorted(
            f"/{p.parent.name}"
            for p in app_dir.glob("*/page.tsx")
            if p.parent.name != "admin"
        )
        untracked = [r for r in top_level_routes if _normalize_path(r) == "/other"]
        assert not untracked, (
            f"{untracked} resolve to /other — add to _KNOWN_STATIC_PATHS "
            f"or _DYNAMIC_PREFIXES in app/api/visits.py"
        )


    def test_every_about_chapter_is_tracked_by_name(self):
        """/about is split into chapters (frontend/src/lib/aboutPages.ts);
        each must be its own row, not drained into "/other"."""
        about_dir = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "app" / "about"
        if not about_dir.is_dir():
            return  # frontend checkout not present in this environment
        chapters = sorted(f"/about/{p.parent.name}" for p in about_dir.glob("*/page.tsx"))
        assert chapters, "expected About chapter pages under frontend/src/app/about/"
        assert [c for c in chapters if _normalize_path(c) != c] == []


_BROWSER = "Mozilla/5.0 (X11; Linux x86_64) Chrome/131.0 Safari/537.36"


def _signal(kind: str, path: str = "/", ip: str = "203.0.113.5"):
    return track_visit(_make_request(peer_ip=ip, user_agent=_BROWSER), kind=kind, path=path)


def _counted(db) -> dict[str, int]:
    _drain_queue_and_write(db)
    return {r.path: r.count for r in db.query(PageView).all()}


class TestCountedOnlyWhenThePageRuns:
    """A page request alone proves nothing (a crawler sends browser
    headers); a page counts once the same client's browser makes one of
    the requests Next's router makes when the page runs."""

    async def test_a_page_request_alone_counts_nothing(self, db_session):
        await _signal("page", "/leaderboard")
        assert _counted(db_session) == {}
        assert db_session.query(SiteVisit).count() == 0

    async def test_the_first_router_request_counts_the_page_once(self, db_session):
        await _signal("page", "/leaderboard")
        for _ in range(5):  # a page prefetches every link in view
            await _signal("router", "/compare")
        assert _counted(db_session) == {"/leaderboard": 1}
        assert db_session.query(SiteVisit).count() == 1

    async def test_another_clients_router_request_does_not_count_it(self, db_session):
        await _signal("page", "/leaderboard", ip="203.0.113.5")
        await _signal("router", ip="203.0.113.6")
        assert _counted(db_session) == {}

    async def test_a_router_request_with_no_page_counts_nothing(self, db_session):
        await _signal("router", "/compare")
        assert _counted(db_session) == {}

    async def test_a_page_not_run_within_the_window_is_dropped(self, db_session, monkeypatch):
        await _signal("page", "/leaderboard")
        real = throttle.time.time
        monkeypatch.setattr(throttle.time, "time", lambda: real() + visits._PENDING_S + 1)
        await _signal("router")
        assert _counted(db_session) == {}

    async def test_a_navigation_counts_once_the_client_has_run_a_page(self, db_session):
        await _signal("navigation", "/compare")  # no page run: nothing
        assert _counted(db_session) == {}
        await _signal("page", "/leaderboard")
        await _signal("router")
        await _signal("navigation", "/compare")
        await _signal("navigation", "/politicians/jane-doe")
        assert _counted(db_session) == {"/leaderboard": 1, "/compare": 1, "/politicians/[id]": 1}

    async def test_a_navigation_also_completes_a_held_page(self, db_session):
        await _signal("page", "/leaderboard")
        await _signal("navigation", "/compare")
        assert _counted(db_session) == {"/leaderboard": 1, "/compare": 1}

    async def test_a_held_page_keeps_only_what_page_views_keep(self, db_session, throttle_store):
        await _signal("page", "/politicians/jane-doe?ref=x")
        await _signal("page", f"/issue/{to_public_id(3)}")
        conn = sqlite3.connect(throttle_store)
        held = sorted(v for (v,) in conn.execute("SELECT value FROM held"))
        conn.close()
        assert held == [f"/issue/{to_public_id(3)}", "/politicians/[id]"]


class TestTrackVisitPageViews:
    async def test_a_headless_automation_browser_is_not_a_visitor(self, db_session):
        """Playwright/Puppeteer's default headless Chrome (cloud agents testing
        the site) runs the page like a browser, so it passes the page-then-
        router check; only its own User-Agent token tells it apart."""
        headless = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "HeadlessChrome/131.0.0.0 Safari/537.36"
        )
        await _view(_make_request(user_agent=headless), "/")
        assert _drain_queue_and_write(db_session) == 0
        await _view(_make_request(user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/131.0 Safari/537.36"), "/")
        assert _drain_queue_and_write(db_session) == 1

    @pytest.mark.parametrize("user_agent", [
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; bingbot/2.0; "
        "+http://www.bing.com/bingbot.htm) Chrome/116.0.1938.76 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4 Safari/605.1.15 (Applebot/0.1; +http://www.apple.com/go/applebot)",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36; compatible; OAI-SearchBot/1.4; +https://openai.com/searchbot",
        "Mozilla/5.0 (compatible; Baiduspider-render/2.0; +http://www.baidu.com/search/spider.html)",
    ])
    async def test_a_crawler_that_renders_the_page_is_not_a_visitor(self, db_session, user_agent):
        await _view(_make_request(user_agent=user_agent), "/")
        assert _drain_queue_and_write(db_session) == 0

    async def test_a_phone_whose_model_ends_in_bot_is_a_visitor(self, db_session):
        cubot = ("Mozilla/5.0 (Linux; Android 10; CUBOT X30 Build/QP1A.190711.020) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36")
        await _view(_make_request(user_agent=cubot), "/")
        assert _drain_queue_and_write(db_session) == 1

    async def test_repeat_views_accumulate_not_dedupe(self, db_session):
        await _view(_make_request(), "/politicians/chuck-grassley")
        await _view(_make_request(), "/politicians/jane-doe")
        _drain_queue_and_write(db_session)

        rows = db_session.query(PageView).all()
        assert len(rows) == 1
        assert rows[0].path == "/politicians/[id]"
        assert rows[0].count == 2

    async def test_different_pages_get_separate_rows(self, db_session):
        await _view(_make_request(), "/leaderboard")
        await _view(_make_request(), "/compare")
        _drain_queue_and_write(db_session)

        rows = {r.path: r.count for r in db_session.query(PageView).all()}
        assert rows == {"/leaderboard": 1, "/compare": 1}


class TestTrackVisitIssueViews:
    """/issue/{id} is the one path PageView's normalization deliberately
    throws away (collapsed to "/issue/[id]") — IssueView is where that
    per-id detail survives, for the trending computation (app/trending.py)."""

    async def test_issue_view_accumulates_alongside_the_normalized_page_view(self, db_session):
        pid = to_public_id(1)
        await _view(_make_request(), f"/issue/{pid}")
        await _view(_make_request(), f"/issue/{pid}")
        _drain_queue_and_write(db_session)

        issue_rows = db_session.query(IssueView).all()
        assert len(issue_rows) == 1
        assert issue_rows[0].issue_public_id == pid
        assert issue_rows[0].count == 2
        # Still rolls up into the normal page-view template too — this is
        # additive, not a replacement for the existing aggregate.
        page_rows = db_session.query(PageView).all()
        assert page_rows[0].path == "/issue/[id]"
        assert page_rows[0].count == 2

    async def test_different_issues_get_separate_rows(self, db_session):
        await _view(_make_request(), f"/issue/{to_public_id(1)}")
        await _view(_make_request(), f"/issue/{to_public_id(2)}")
        _drain_queue_and_write(db_session)

        rows = {r.issue_public_id: r.count for r in db_session.query(IssueView).all()}
        assert rows == {to_public_id(1): 1, to_public_id(2): 1}

    async def test_malformed_issue_id_writes_no_issue_view_row(self, db_session):
        await _view(_make_request(), "/issue/not-a-real-id")
        _drain_queue_and_write(db_session)

        assert db_session.query(IssueView).all() == []
        # The page-view aggregate still counts it — only the per-id table
        # is protected against junk.
        assert db_session.query(PageView).one().path == "/issue/[id]"


class TestVisitQueueArchitecture:
    """2026-07 incident + follow-up review: track_visit used to write to
    the DB inline, both sharing SQLite's writer lock with the nightly
    pipeline (exhausted the connection pool, OOM-killed the container)
    and blocking the event loop with a synchronous call inside `async
    def` (freezing every other concurrent request, not just this one).

    Fixed by moving the write off the request path entirely: track_visit
    only enqueues; a single background consumer (run_visit_consumer)
    drains and writes in batches via asyncio.to_thread. These pin the two
    failure modes that must never propagate: a full queue (enqueue side)
    and write-lock contention (consumer side) — neither should ever raise
    or block a page load.
    """

    def test_write_batch_lock_contention_does_not_raise(self):
        db = MagicMock()
        db.execute.side_effect = OperationalError("stmt", {}, Exception("database is locked"))
        event = _VisitEvent(
            date="2026-07-22", visitor_hash="abc", browser="Chrome",
            os="Linux", device_type="desktop", normalized_path="/leaderboard",
        )

        _write_visit_batch([event], db)  # must not raise

        db.rollback.assert_called_once()

    async def test_queue_full_drops_event_without_raising(self, monkeypatch):
        tiny_queue = asyncio.Queue(maxsize=1)
        monkeypatch.setattr("app.api.visits._visit_queue", tiny_queue)

        await _view(_make_request(), "/leaderboard")  # fills the queue
        result = await _view(_make_request(), "/compare")  # must not raise or block

        assert result is None
        assert tiny_queue.qsize() == 1  # the second event was dropped, not queued


class TestAdminTopPages:
    async def test_returns_pages_sorted_by_views_desc(self, db_session):
        for _ in range(3):
            await _view(_make_request(), "/leaderboard")
        await _view(_make_request(), "/compare")
        _drain_queue_and_write(db_session)

        result = admin_top_pages(days=7, limit=10, db=db_session)
        assert result[0] == {"path": "/leaderboard", "views": 3}
        assert result[1] == {"path": "/compare", "views": 1}
