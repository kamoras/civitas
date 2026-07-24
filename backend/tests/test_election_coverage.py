"""Tests for election_coverage — corroborated candidate-name matching of
news articles + Bluesky search results to races.

The matching contract (2026-07 review F8): a bare surname is never
identifying. A match needs the surname PLUS corroboration in the same
text — the candidate's first name ("full_name") or their state's full
name ("surname_context") — and an item that still matches candidates in
more than one race is dropped entirely rather than guessed or fanned out.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.models import Candidate, Race, RaceCoverageItem
from app.pipeline.analyze import election_coverage
from app.pipeline.fetch.bluesky_search import BlueskyPost
from app.pipeline.fetch.news_feeds import NewsArticle


def _race(db, race_id, state, office="S"):
    r = Race(id=race_id, cycle_year=2026, office=office, state=state)
    db.add(r)
    return r


def _candidate(db, cand_id, race_id, name, **overrides):
    defaults = dict(party="DEM")
    defaults.update(overrides)
    c = Candidate(id=cand_id, race_id=race_id, name=name, **defaults)
    db.add(c)
    return c


class TestResolveItemRace:
    """The matcher in isolation — resolve_item_race over compiled
    CandidateMatchers, no I/O."""

    def _matchers(self, db):
        return election_coverage._build_matchers(db)

    def test_bare_surname_does_not_match(self, db_session):
        """The roster has BROWN, SARAH (GA) — an article about an unrelated
        John Brown must not attach: surname alone is not identifying."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S6GA001", "2026-SEN-GA", "BROWN, SARAH")
        db_session.commit()

        resolved = election_coverage.resolve_item_race(
            self._matchers(db_session), "Basketball coach John Brown wins title",
        )
        assert resolved is None

    def test_surname_plus_first_name_matches_as_full_name(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S6GA001", "2026-SEN-GA", "BROWN, SARAH")
        db_session.commit()

        resolved = election_coverage.resolve_item_race(
            self._matchers(db_session), "Sarah Brown launches campaign",
        )
        assert resolved is not None
        matcher, basis = resolved
        assert matcher.race_id == "2026-SEN-GA"
        assert matcher.candidate_id == "S6GA001"
        assert basis == "full_name"

    def test_surname_plus_state_name_matches_as_surname_context(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S6GA001", "2026-SEN-GA", "BROWN, SARAH")
        db_session.commit()

        resolved = election_coverage.resolve_item_race(
            self._matchers(db_session), "Brown leads in Georgia poll",
        )
        assert resolved is not None
        matcher, basis = resolved
        assert matcher.race_id == "2026-SEN-GA"
        assert basis == "surname_context"

    def test_corroborated_matches_in_two_races_are_dropped(self, db_session):
        """Even when BOTH matches are individually corroborated, a text
        naming candidates in two different races doesn't identify one race
        — drop, never guess or fan out."""
        _race(db_session, "2026-SEN-GA", "GA")
        _race(db_session, "2026-SEN-TX", "TX")
        _candidate(db_session, "S6GA001", "2026-SEN-GA", "SMITH, JANE")
        _candidate(db_session, "S6TX001", "2026-SEN-TX", "SMITH, ROBERT")
        db_session.commit()

        resolved = election_coverage.resolve_item_race(
            self._matchers(db_session),
            "Jane Smith and Robert Smith spar over trade policy",
        )
        assert resolved is None

    def test_two_rivals_in_the_same_race_attach_once_to_that_race(self, db_session):
        """An article naming two rivals in one primary is unambiguous about
        the RACE — it attaches once, recording the strongest-basis
        candidate as the match evidence."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S6GA001", "2026-SEN-GA", "WARNOCK, RAPHAEL")
        _candidate(db_session, "S6GA002", "2026-SEN-GA", "WALKER, HERSCHEL", party="REP")
        db_session.commit()

        resolved = election_coverage.resolve_item_race(
            self._matchers(db_session),
            # Warnock matches full_name (Raphael present); Walker only
            # surname_context (via Georgia) — full_name must win.
            "Raphael Warnock leads Walker in new Georgia poll",
        )
        assert resolved is not None
        matcher, basis = resolved
        assert matcher.race_id == "2026-SEN-GA"
        assert matcher.candidate_id == "S6GA001"
        assert basis == "full_name"


class TestIngestRaceCoverage:
    async def test_matches_news_article_with_state_corroboration(self, db_session):
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
             patch.object(election_coverage, "search_posts", new=AsyncMock(return_value=[])):
            ingested = await election_coverage.ingest_race_coverage(db_session, client=None)

        assert ingested == 1
        item = db_session.query(RaceCoverageItem).one()
        assert item.race_id == "2026-SEN-GA"
        assert item.source_type == "news"
        assert item.url == "https://apnews.com/article/ossoff-1"
        assert item.summary == "Polling shows a tight contest."  # verbatim, not LLM-touched
        assert item.matched_candidate_id == "S6GA001"
        assert item.match_basis == "surname_context"

    async def test_short_surname_never_matches(self, db_session):
        """A 3-char-or-shorter surname (e.g. "OZ") is too likely to match
        unrelated text — must not be used as a matching pattern at all."""
        _race(db_session, "2026-SEN-PA", "PA")
        _candidate(db_session, "S6PA001", "2026-SEN-PA", "OZ, MEHMET")
        db_session.commit()

        article = NewsArticle(
            title="Ozone alert issued for Pennsylvania",
            url="https://apnews.com/article/ozone",
            source_name="AP News",
        )
        with patch.object(election_coverage, "fetch_news_articles", return_value=[article]), \
             patch.object(election_coverage, "search_posts", new=AsyncMock(return_value=[])):
            ingested = await election_coverage.ingest_race_coverage(db_session, client=None)

        assert ingested == 0

    async def test_uncorroborated_surname_never_ingested(self, db_session):
        """Surname present but neither first name nor state name — the
        pre-review fan-out behavior attached this everywhere; now it must
        not attach anywhere."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S6GA001", "2026-SEN-GA", "SMITH, JANE")
        db_session.commit()

        article = NewsArticle(
            title="Smith campaign announces new ad buy",
            url="https://apnews.com/article/smith-ad",
            source_name="AP News",
        )
        with patch.object(election_coverage, "fetch_news_articles", return_value=[article]), \
             patch.object(election_coverage, "search_posts", new=AsyncMock(return_value=[])):
            ingested = await election_coverage.ingest_race_coverage(db_session, client=None)

        assert ingested == 0
        assert db_session.query(RaceCoverageItem).count() == 0

    async def test_ambiguous_multi_race_article_dropped_entirely(self, db_session):
        """Replaces the old shared-surname fan-out contract: an article
        matching corroborated candidates in TWO races is stored for
        NEITHER (ambiguity => drop, same rule as fec.find_candidate)."""
        _race(db_session, "2026-SEN-GA", "GA")
        _race(db_session, "2026-SEN-TX", "TX")
        _candidate(db_session, "S6GA001", "2026-SEN-GA", "SMITH, JANE")
        _candidate(db_session, "S6TX001", "2026-SEN-TX", "SMITH, ROBERT")
        db_session.commit()

        article = NewsArticle(
            title="Jane Smith and Robert Smith trade barbs across state lines",
            url="https://apnews.com/article/smith-v-smith",
            source_name="AP News",
        )
        with patch.object(election_coverage, "fetch_news_articles", return_value=[article]), \
             patch.object(election_coverage, "search_posts", new=AsyncMock(return_value=[])):
            ingested = await election_coverage.ingest_race_coverage(db_session, client=None)

        assert ingested == 0
        assert db_session.query(RaceCoverageItem).count() == 0

    async def test_bluesky_post_matched_and_stored(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        # has_raised_funds=True so the candidate qualifies for the rotating
        # Bluesky search batch (paper filers don't get search traffic).
        _candidate(
            db_session, "S6GA001", "2026-SEN-GA", "OSSOFF, JON",
            has_raised_funds=True,
        )
        db_session.commit()

        post = BlueskyPost(
            text="Jon Ossoff's campaign raised a record sum this quarter.",
            url="https://bsky.app/profile/apnews.com/post/abc123",
            author_handle="apnews.com",
        )
        with patch.object(election_coverage, "fetch_news_articles", return_value=[]), \
             patch.object(election_coverage, "search_posts", new=AsyncMock(return_value=[post])) as mock_search:
            ingested = await election_coverage.ingest_race_coverage(db_session, client=None)

        assert ingested == 1
        # The search query is the candidate's "First Last" — already scoped
        # to the person, never the bare surname.
        mock_search.assert_awaited_once_with(None, "JON OSSOFF")
        item = db_session.query(RaceCoverageItem).one()
        assert item.source_type == "bluesky"
        assert item.author == "apnews.com"
        assert item.summary == post.text  # verbatim post text, not LLM-touched
        assert item.match_basis == "full_name"

    async def test_bluesky_search_updates_watermark_and_skips_inactive(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        active = _candidate(
            db_session, "S6GA001", "2026-SEN-GA", "OSSOFF, JON",
            has_raised_funds=True,
        )
        paper = _candidate(db_session, "S6GA002", "2026-SEN-GA", "DOE, JOHNNY")
        db_session.commit()

        with patch.object(election_coverage, "fetch_news_articles", return_value=[]), \
             patch.object(election_coverage, "search_posts", new=AsyncMock(return_value=[])) as mock_search:
            await election_coverage.ingest_race_coverage(db_session, client=None)

        mock_search.assert_awaited_once()  # only the active candidate searched
        assert active.last_coverage_search is not None
        assert paper.last_coverage_search is None

    async def test_aware_published_at_stored_naive_utc(self, db_session):
        """Sources hand us aware datetimes; the DB convention is naive UTC
        (time_utils.utcnow) — normalization happens at the ingestion
        boundary, not scattered at read sites."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S6GA001", "2026-SEN-GA", "OSSOFF, JON")
        db_session.commit()

        published = datetime(2026, 7, 20, 12, 30, tzinfo=timezone.utc)
        article = NewsArticle(
            title="Ossoff holds narrow lead in Georgia Senate race",
            url="https://apnews.com/article/ossoff-1",
            source_name="AP News",
            published=published,
        )
        with patch.object(election_coverage, "fetch_news_articles", return_value=[article]), \
             patch.object(election_coverage, "search_posts", new=AsyncMock(return_value=[])):
            await election_coverage.ingest_race_coverage(db_session, client=None)

        item = db_session.query(RaceCoverageItem).one()
        assert item.published_at.tzinfo is None
        assert item.published_at == datetime(2026, 7, 20, 12, 30)

    async def test_second_run_does_not_duplicate(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S6GA001", "2026-SEN-GA", "OSSOFF, JON")
        db_session.commit()

        article = NewsArticle(
            title="Ossoff holds narrow lead in Georgia",
            url="https://apnews.com/article/ossoff-1",
            source_name="AP News",
        )
        with patch.object(election_coverage, "fetch_news_articles", return_value=[article]), \
             patch.object(election_coverage, "search_posts", new=AsyncMock(return_value=[])):
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
