"""Tests for page-view tracking (app/api/visits.py).

PageView is deliberately separate from SiteVisit's unique-visitor dedup —
these tests cover the normalization that keeps "most visited pages"
meaningful (collapsing per-id routes to a template) and confirm repeat
views actually accumulate rather than no-op like SiteVisit does.
"""

from unittest.mock import MagicMock

from app.api.admin import admin_top_pages
from app.api.visits import _normalize_path, track_visit
from app.models import PageView


def _make_request(peer_ip: str = "203.0.113.5", user_agent: str = "Mozilla/5.0") -> MagicMock:
    req = MagicMock()
    req.client.host = peer_ip
    req.headers = {"User-Agent": user_agent}
    return req


class TestNormalizePath:
    def test_known_static_paths_pass_through(self):
        assert _normalize_path("/leaderboard") == "/leaderboard"
        assert _normalize_path("/") == "/"

    def test_politician_id_collapses_to_template(self):
        assert _normalize_path("/politicians/chuck-grassley") == "/politicians/[id]"
        assert _normalize_path("/politicians/jane-doe") == "/politicians/[id]"

    def test_issue_and_explore_ids_collapse_to_template(self):
        assert _normalize_path("/issue/312") == "/issue/[id]"
        assert _normalize_path("/explore/987") == "/explore/[id]"

    def test_bare_dynamic_prefix_without_id_is_static_path(self):
        # "/politicians" itself (no id segment) is the directory page, not
        # a per-id route — must not collapse into "/politicians/[id]".
        assert _normalize_path("/politicians") == "/politicians"

    def test_trailing_slash_and_query_string_ignored(self):
        assert _normalize_path("/leaderboard/") == "/leaderboard"
        assert _normalize_path("/leaderboard?tab=house") == "/leaderboard"

    def test_unknown_path_buckets_to_other(self):
        assert _normalize_path("/some/random/junk") == "/other"
        assert _normalize_path("") == "/"


class TestTrackVisitPageViews:
    async def test_repeat_views_accumulate_not_dedupe(self, db_session):
        await track_visit(_make_request(), path="/politicians/chuck-grassley", db=db_session)
        await track_visit(_make_request(), path="/politicians/jane-doe", db=db_session)

        rows = db_session.query(PageView).all()
        assert len(rows) == 1
        assert rows[0].path == "/politicians/[id]"
        assert rows[0].count == 2

    async def test_different_pages_get_separate_rows(self, db_session):
        await track_visit(_make_request(), path="/leaderboard", db=db_session)
        await track_visit(_make_request(), path="/compare", db=db_session)

        rows = {r.path: r.count for r in db_session.query(PageView).all()}
        assert rows == {"/leaderboard": 1, "/compare": 1}


class TestAdminTopPages:
    async def test_returns_pages_sorted_by_views_desc(self, db_session):
        for _ in range(3):
            await track_visit(_make_request(), path="/leaderboard", db=db_session)
        await track_visit(_make_request(), path="/compare", db=db_session)

        result = await admin_top_pages(days=7, limit=10, db=db_session)
        assert result[0] == {"path": "/leaderboard", "views": 3}
        assert result[1] == {"path": "/compare", "views": 1}
