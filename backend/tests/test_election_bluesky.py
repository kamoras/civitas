"""Tests for election_bluesky.post_race_coverage_updates.

Mocks _generate_post_text and publish_post (network/LLM boundary) —
exercises the selection, capping, and "always mark considered" logic,
same style as test_bluesky_poster.py's process_issues_for_bluesky tests.
"""

from datetime import timedelta
from unittest.mock import patch

from app.models import Race, RaceCoverageItem
from app.pipeline.analyze import election_bluesky
from app.time_utils import utcnow


def _race(db, race_id="2026-SEN-GA", state="GA", office="S"):
    r = Race(id=race_id, cycle_year=2026, office=office, state=state)
    db.add(r)
    return r


def _item(db, race_id="2026-SEN-GA", **overrides):
    defaults = dict(
        race_id=race_id, source_type="news", source_name="AP News",
        title="Ossoff holds narrow lead", url="https://apnews.com/a1",
        summary="Polling shows a tight contest.",
    )
    defaults.update(overrides)
    i = RaceCoverageItem(**defaults)
    db.add(i)
    return i


class TestPostRaceCoverageUpdates:
    def test_no_credentials_returns_zero(self, db_session, monkeypatch):
        monkeypatch.setattr(election_bluesky.settings, "BSKY_HANDLE", "", raising=False)
        monkeypatch.setattr(election_bluesky.settings, "BSKY_APP_PASSWORD", "", raising=False)
        _race(db_session)
        _item(db_session)
        db_session.commit()

        posted = election_bluesky.post_race_coverage_updates(db_session)
        assert posted == 0
        # Not even considered — credentials check short-circuits first.
        assert db_session.query(RaceCoverageItem).one().bsky_posted_at is None

    def test_successful_post_marks_item_and_increments(self, db_session, monkeypatch):
        monkeypatch.setattr(election_bluesky.settings, "BSKY_HANDLE", "test.handle", raising=False)
        monkeypatch.setattr(election_bluesky.settings, "BSKY_APP_PASSWORD", "pw", raising=False)
        _race(db_session)
        item = _item(db_session)
        db_session.commit()

        with patch.object(election_bluesky, "_generate_post_text", return_value="Ossoff holds a narrow lead, per new polling."), \
             patch.object(election_bluesky, "_publish", return_value=True) as mock_publish:
            posted = election_bluesky.post_race_coverage_updates(db_session)

        assert posted == 1
        mock_publish.assert_called_once()
        assert item.bsky_posted_at is not None

    def test_grounding_failure_marks_considered_but_not_posted(self, db_session, monkeypatch):
        monkeypatch.setattr(election_bluesky.settings, "BSKY_HANDLE", "test.handle", raising=False)
        monkeypatch.setattr(election_bluesky.settings, "BSKY_APP_PASSWORD", "pw", raising=False)
        _race(db_session)
        item = _item(db_session)
        db_session.commit()

        with patch.object(election_bluesky, "_generate_post_text", return_value=None):
            posted = election_bluesky.post_race_coverage_updates(db_session)

        assert posted == 0
        assert item.bsky_posted_at is not None  # considered, won't be re-evaluated

    def test_missing_race_is_skipped_gracefully(self, db_session, monkeypatch):
        monkeypatch.setattr(election_bluesky.settings, "BSKY_HANDLE", "test.handle", raising=False)
        monkeypatch.setattr(election_bluesky.settings, "BSKY_APP_PASSWORD", "pw", raising=False)
        # No Race row at all for this race_id — a real-world edge case if a
        # race got deleted between coverage ingestion and posting.
        item = _item(db_session, race_id="2026-SEN-NOWHERE")
        db_session.commit()

        posted = election_bluesky.post_race_coverage_updates(db_session)
        assert posted == 0
        assert item.bsky_posted_at is not None

    def test_respects_max_posts_per_run_cap(self, db_session, monkeypatch):
        monkeypatch.setattr(election_bluesky.settings, "BSKY_HANDLE", "test.handle", raising=False)
        monkeypatch.setattr(election_bluesky.settings, "BSKY_APP_PASSWORD", "pw", raising=False)
        monkeypatch.setattr(election_bluesky, "MAX_POSTS_PER_RUN", 2)
        _race(db_session)
        for i in range(5):
            _item(db_session, url=f"https://apnews.com/a{i}")
        db_session.commit()

        with patch.object(election_bluesky, "_generate_post_text", return_value="A grounded sentence."), \
             patch.object(election_bluesky, "_publish", return_value=True):
            posted = election_bluesky.post_race_coverage_updates(db_session)

        assert posted == 2
        considered = db_session.query(RaceCoverageItem).filter(
            RaceCoverageItem.bsky_posted_at.isnot(None),
        ).count()
        assert considered == 2

    def test_already_considered_items_skipped(self, db_session, monkeypatch):
        monkeypatch.setattr(election_bluesky.settings, "BSKY_HANDLE", "test.handle", raising=False)
        monkeypatch.setattr(election_bluesky.settings, "BSKY_APP_PASSWORD", "pw", raising=False)
        _race(db_session)
        _item(db_session, bsky_posted_at=utcnow() - timedelta(hours=1))
        db_session.commit()

        with patch.object(election_bluesky, "_generate_post_text") as mock_gen:
            posted = election_bluesky.post_race_coverage_updates(db_session)

        assert posted == 0
        mock_gen.assert_not_called()
