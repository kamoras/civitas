"""Tests for election_coverage.ingest_race_coverage — deterministic
candidate-surname matching of news articles + Bluesky search results to
races. Verifies the matcher doesn't false-positive on short/common
surnames, correctly shares a surname across multiple races, and doesn't
duplicate already-ingested items on a second run.
"""

from unittest.mock import patch

import pytest

from app.models import Candidate, Race, RaceCoverageItem
from app.pipeline.analyze import election_coverage
from app.pipeline.fetch.bluesky_search import BlueskyPost
from app.pipeline.fetch.news_feeds import NewsArticle


def _race(db, race_id, state, office="S"):
    r = Race(id=race_id, cycle_year=2026, office=office, state=state)
    db.add(r)
    return r


def _candidate(db, cand_id, race_id, name):
    c = Candidate(id=cand_id, race_id=race_id, name=name, party="DEM")
    db.add(c)
    return c


@pytest.mark.asyncio
class TestIngestRaceCoverage:
    async def test_matches_news_article_by_surname(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S6GA001", "2026-SEN-GA", "OSSOFF, JON")
        db_session.commit()

        article = NewsArticle(
            title="Ossoff holds narrow lead in Georgia Senate race",
            url="https://apnews.com/article/ossoff-1",
            source_name="AP News",
            summary="Polling shows a tight contest.",
        )
        with patch.object(election_coverage, "fetch_news_articles", return_value=[article]), \
             patch.object(election_coverage, "search_posts", return_value=[]):
            ingested = await election_coverage.ingest_race_coverage(db_session, client=None)

        assert ingested == 1
        item = db_session.query(RaceCoverageItem).one()
        assert item.race_id == "2026-SEN-GA"
        assert item.source_type == "news"
        assert item.url == "https://apnews.com/article/ossoff-1"
        assert item.summary == "Polling shows a tight contest."  # verbatim, not LLM-touched

    async def test_short_surname_never_matches(self, db_session):
        """A 3-char-or-shorter surname (e.g. "OZ") is too likely to match
        unrelated text — must not be used as a matching pattern at all."""
        _race(db_session, "2026-SEN-PA", "PA")
        _candidate(db_session, "S6PA001", "2026-SEN-PA", "OZ, MEHMET")
        db_session.commit()

        article = NewsArticle(
            title="Ozone alert issued for the region",
            url="https://apnews.com/article/ozone",
            source_name="AP News",
        )
        with patch.object(election_coverage, "fetch_news_articles", return_value=[article]), \
             patch.object(election_coverage, "search_posts", return_value=[]):
            ingested = await election_coverage.ingest_race_coverage(db_session, client=None)

        assert ingested == 0

    async def test_shared_surname_attaches_to_both_races(self, db_session):
        """Two different candidates named Smith in two different states —
        a matching article/post must attach to both races, not just one."""
        _race(db_session, "2026-SEN-XX", "XX")
        _race(db_session, "2026-SEN-YY", "YY")
        _candidate(db_session, "S6XX001", "2026-SEN-XX", "SMITH, JANE")
        _candidate(db_session, "S6YY001", "2026-SEN-YY", "SMITH, ROBERT")
        db_session.commit()

        article = NewsArticle(
            title="Smith campaign announces new ad buy",
            url="https://apnews.com/article/smith-ad",
            source_name="AP News",
        )
        with patch.object(election_coverage, "fetch_news_articles", return_value=[article]), \
             patch.object(election_coverage, "search_posts", return_value=[]):
            ingested = await election_coverage.ingest_race_coverage(db_session, client=None)

        assert ingested == 2
        race_ids = {i.race_id for i in db_session.query(RaceCoverageItem).all()}
        assert race_ids == {"2026-SEN-XX", "2026-SEN-YY"}

    async def test_bluesky_post_matched_and_stored(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S6GA001", "2026-SEN-GA", "OSSOFF, JON")
        db_session.commit()

        post = BlueskyPost(
            text="Ossoff's campaign raised a record sum this quarter.",
            url="https://bsky.app/profile/apnews.com/post/abc123",
            author_handle="apnews.com",
        )
        with patch.object(election_coverage, "fetch_news_articles", return_value=[]), \
             patch.object(election_coverage, "search_posts", return_value=[post]):
            ingested = await election_coverage.ingest_race_coverage(db_session, client=None)

        assert ingested == 1
        item = db_session.query(RaceCoverageItem).one()
        assert item.source_type == "bluesky"
        assert item.author == "apnews.com"
        assert item.summary == post.text  # verbatim post text, not LLM-touched

    async def test_second_run_does_not_duplicate(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S6GA001", "2026-SEN-GA", "OSSOFF, JON")
        db_session.commit()

        article = NewsArticle(
            title="Ossoff holds narrow lead",
            url="https://apnews.com/article/ossoff-1",
            source_name="AP News",
        )
        with patch.object(election_coverage, "fetch_news_articles", return_value=[article]), \
             patch.object(election_coverage, "search_posts", return_value=[]):
            first = await election_coverage.ingest_race_coverage(db_session, client=None)
            second = await election_coverage.ingest_race_coverage(db_session, client=None)

        assert first == 1
        assert second == 0
        assert db_session.query(RaceCoverageItem).count() == 1

    async def test_no_candidates_returns_zero_without_fetching(self, db_session):
        with patch.object(election_coverage, "fetch_news_articles") as mock_news:
            ingested = await election_coverage.ingest_race_coverage(db_session, client=None)
        assert ingested == 0
        mock_news.assert_not_called()
