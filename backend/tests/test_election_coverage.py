"""Tests for election_coverage — corroborated candidate-name matching of
news articles + Bluesky search results to races.

The matching contract (2026-07 review F8): a bare surname is never
identifying. A match needs the surname PLUS corroboration in the same
text — the candidate's first name ("full_name") or their state's full
name ("surname_context") — and an item that still matches candidates in
more than one race is dropped entirely rather than guessed or fanned out.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.models import Candidate, Race, RaceCoverageItem
from app.pipeline.analyze import election_coverage
from app.pipeline.analyze import election_coverage as ec
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

    def test_west_virginia_does_not_corroborate_a_virginia_candidate(self, db_session):
        """"Virginia" word-matches inside "West Virginia".

        Found live: a story about a former WEST Virginia senator
        "deciding to register as an independent" was attached to VA-5,
        because the roster has a candidate surnamed REGISTER and the
        state check saw "Virginia" inside "West Virginia". Virginia/West
        Virginia is the only such pair among the fifty.
        """
        _race(db_session, "2026-HOUSE-VA-5", "VA")
        _candidate(db_session, "H6VA001", "2026-HOUSE-VA-5", "REGISTER, CHRIS")
        db_session.commit()

        assert election_coverage.resolve_item_race(
            self._matchers(db_session),
            "The former West Virginia senator was a lifelong Democrat before "
            "deciding to register as an independent two years ago.",
        ) is None

    def test_a_real_virginia_story_still_matches(self, db_session):
        """The guard must not cost genuine Virginia coverage."""
        _race(db_session, "2026-HOUSE-VA-5", "VA")
        _candidate(db_session, "H6VA001", "2026-HOUSE-VA-5", "REGISTER, CHRIS")
        db_session.commit()

        resolved = election_coverage.resolve_item_race(
            self._matchers(db_session),
            "Register picks up a key endorsement in the Virginia race.",
        )
        assert resolved is not None
        assert resolved[0].race_id == "2026-HOUSE-VA-5"
        assert resolved[1] == "surname_context"

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

    async def test_same_post_twice_in_one_pass_does_not_abort_it(self, db_session):
        """Reproduces the live IntegrityError that #594 uncovered.

        SessionLocal sets autoflush=False, so _already_ingested cannot
        see rows added earlier in the same pass. Two candidates in ONE
        race both matching the same post — which the module docstring
        calls fine — therefore queued two rows with the same
        (race_id, url) and blew up on uq_race_coverage_race_url at
        commit, losing the whole pass including its news items. It stayed
        hidden only because Bluesky search was returning nothing.
        """
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S6GA001", "2026-SEN-GA", "OSSOFF, JON",
                   has_raised_funds=True)
        _candidate(db_session, "S6GA002", "2026-SEN-GA", "WARNOCK, RAPHAEL",
                   has_raised_funds=True)
        db_session.commit()

        # One post naming both rivals, returned by BOTH candidates' searches.
        post = BlueskyPost(
            text="Jon Ossoff and Raphael Warnock both campaigned in Georgia today.",
            url="https://bsky.app/profile/apnews.com/post/dupe1",
            author_handle="apnews.com",
        )
        with patch.object(election_coverage, "fetch_news_articles", return_value=[]), \
             patch.object(election_coverage, "search_posts",
                          new=AsyncMock(return_value=[post])):
            ingested = await election_coverage.ingest_race_coverage(
                db_session, client=None)

        assert ingested == 1, "the duplicate should be skipped, not counted"
        assert db_session.query(RaceCoverageItem).count() == 1

    async def test_unavailable_source_does_not_advance_the_watermark(self, db_session):
        """An unavailable source is not a finding of no coverage.

        last_coverage_search is a rotation cursor: "never searched first,
        then longest-unsearched first". Stamping it while the source is
        down rotates candidates past as searched having never been
        searched — which is exactly what happened for weeks after Bluesky
        withdrew unauthenticated searchPosts and every call 403'd.
        """
        _race(db_session, "2026-SEN-GA", "GA")
        active = _candidate(
            db_session, "S6GA001", "2026-SEN-GA", "OSSOFF, JON",
            has_raised_funds=True,
        )
        db_session.commit()

        with patch.object(election_coverage, "fetch_news_articles", return_value=[]), \
             patch.object(election_coverage, "search_posts", new=AsyncMock(return_value=[])), \
             patch.object(election_coverage, "search_is_available", return_value=False):
            ingested = await election_coverage.ingest_race_coverage(db_session, client=None)

        assert ingested == 0
        assert active.last_coverage_search is None, \
            "watermark advanced despite the source being unavailable"

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


class TestSurnameMustLookLikeAName:
    """Many real candidate surnames are ordinary English nouns.

    Case-insensitive matching attached their races to any article using
    the word. Measured across the 554 live coverage items, requiring a
    capital drops 6.3% and every sampled drop was a false positive.
    """

    def _matchers(self, db):
        return election_coverage._build_matchers(db)

    def test_a_lowercase_common_word_is_not_a_name(self, db_session):
        _race(db_session, "2026-HOUSE-GA-2", "GA")
        _candidate(db_session, "H6GA002", "2026-HOUSE-GA-2", "HAND, ALICE")
        db_session.commit()

        assert election_coverage.resolve_item_race(
            self._matchers(db_session),
            "Born without a right hand, she is set to play in Georgia.",
        ) is None

    def test_the_same_surname_capitalised_still_matches(self, db_session):
        _race(db_session, "2026-HOUSE-GA-2", "GA")
        _candidate(db_session, "H6GA002", "2026-HOUSE-GA-2", "HAND, ALICE")
        db_session.commit()

        resolved = election_coverage.resolve_item_race(
            self._matchers(db_session), "Hand leads the Georgia primary field.")
        assert resolved is not None
        assert resolved[0].race_id == "2026-HOUSE-GA-2"

    def test_intercaps_surnames_survive(self, db_session):
        """The rule checks the MATCHED text, not a rebuilt "Mcconnell".

        Lower-casing the tail to test capitalisation rejects every real
        intercaps name — an error that made a first measurement of this
        rule report a 18.8% recall cost instead of 6.3%.
        """
        _race(db_session, "2026-SEN-KY", "KY")
        _candidate(db_session, "S6KY001", "2026-SEN-KY", "MCCONNELL, MITCH")
        db_session.commit()

        resolved = election_coverage.resolve_item_race(
            self._matchers(db_session),
            "McConnell to remain in rehab, will skip the Kentucky picnic.")
        assert resolved is not None
        assert resolved[0].race_id == "2026-SEN-KY"

    def test_all_caps_headlines_still_match(self, db_session):
        _race(db_session, "2026-SEN-KY", "KY")
        _candidate(db_session, "S6KY001", "2026-SEN-KY", "MCCONNELL, MITCH")
        db_session.commit()

        resolved = election_coverage.resolve_item_race(
            self._matchers(db_session), "MCCONNELL SKIPS KENTUCKY PICNIC")
        assert resolved is not None


class TestStoredItemsAreRevalidated:
    """Nothing ever deletes a RaceCoverageItem — there is no retention
    sweep — so a tightening of the matcher has to apply retroactively or
    the rules and the stored data drift apart permanently."""

    async def _run(self, db_session):
        with patch.object(election_coverage, "fetch_news_articles", return_value=[]), \
             patch.object(election_coverage, "search_posts", new=AsyncMock(return_value=[])):
            return await election_coverage.ingest_race_coverage(db_session, client=None)

    async def test_a_stored_false_positive_is_dropped(self, db_session):
        """The live NE-3 case: a government-shutdown article attached
        because a candidate there is surnamed ELSE."""
        _race(db_session, "2026-HOUSE-NE-3", "NE")
        _candidate(db_session, "H6NE003", "2026-HOUSE-NE-3", "ELSE, MARY",
                   has_raised_funds=True)
        db_session.add(RaceCoverageItem(
            race_id="2026-HOUSE-NE-3", source_type="news", source_name="AP",
            title="Senate funding patch secured",
            url="https://example.com/shutdown",
            summary="With a bipartisan deal in hand to avoid a shutdown, "
                    "nobody else expects a vote before Nebraska's primary.",
            matched_candidate_id="H6NE003", match_basis="surname_context",
        ))
        db_session.commit()

        await self._run(db_session)

        assert db_session.query(RaceCoverageItem).count() == 0

    async def test_genuine_coverage_survives_revalidation(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S6GA001", "2026-SEN-GA", "OSSOFF, JON",
                   has_raised_funds=True)
        db_session.add(RaceCoverageItem(
            race_id="2026-SEN-GA", source_type="news", source_name="AP",
            title="Ossoff holds narrow lead in Georgia Senate race",
            url="https://example.com/ossoff",
            summary="Polling shows a tight contest.",
            matched_candidate_id="S6GA001", match_basis="surname_context",
        ))
        db_session.commit()

        await self._run(db_session)

        assert db_session.query(RaceCoverageItem).count() == 1

    async def test_an_item_whose_candidate_left_the_roster_is_kept(self, db_session):
        """Nothing to re-validate against, so dropping it would delete
        real coverage every time the roster churns."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S6GA001", "2026-SEN-GA", "OSSOFF, JON",
                   has_raised_funds=True)
        db_session.add(RaceCoverageItem(
            race_id="2026-SEN-GA", source_type="news", source_name="AP",
            title="A withdrawn candidate's coverage",
            url="https://example.com/gone",
            summary="Text that no longer corroborates anyone on the roster.",
            matched_candidate_id="S6GA999",  # no longer a candidate
            match_basis="full_name",
        ))
        db_session.commit()

        await self._run(db_session)

        assert db_session.query(RaceCoverageItem).count() == 1


class TestFullNameMustAppearTogether:
    """Alaska's at-large race has a real candidate — $1.3M raised — named
    BILL HILL. The old rule asked only that the surname appear
    capitalised and the first name appear ANYWHERE in the text, in any
    position and any case, so "The Hill" plus "I'm just a bill, sittin
    here on Capitol Hill" was a full-name match. The race collected that
    as coverage and the account posted about it nine times.

    Measured across all 7,471 stored full_name matches: 25.1% were
    incidental in exactly this way."""

    def _basis(self, first, surname, text):
        pat = ec._full_name_pattern(first, surname)
        return ec._matches_full_name(pat, text)

    @pytest.mark.parametrize("text", [
        "The Hill reports that Congress needs a bill on fixed recesses.",
        "I'm just a bill, sittin here on Capitol Hill.",
        "Stopgap funding bill seeks delay of Trump's science funding plan",
    ])
    def test_incidental_words_are_not_a_full_name(self, text):
        assert not self._basis("BILL", "HILL", text)

    @pytest.mark.parametrize("text", [
        "Bill Hill announced his campaign for Alaska's at-large seat.",
        "Hill, Bill filed with the FEC this week.",
    ])
    def test_the_real_candidate_still_matches(self, text):
        assert self._basis("BILL", "HILL", text)

    def test_intercaps_surnames_survive(self):
        """The trap this module already documents: building "Mcconnell"
        to compare case-sensitively rejects every real "McConnell"."""
        assert self._basis("MITCH", "MCCONNELL",
                           "Mitch McConnell's absence looms large over the picnic.")
        assert self._basis("BETO", "O'ROURKE", "Beto O'Rourke campaigned in El Paso.")

    def test_a_middle_name_or_initial_does_not_break_the_match(self):
        assert self._basis("ROBERT", "KENNEDY", "Robert F. Kennedy Jr. spoke on Tuesday.")

    @pytest.mark.parametrize("first,surname,text", [
        ("DANIEL", "CAMERON", "Senate confirms Cameron Hamilton to lead FEMA."),
        ("ADAM", "DELGADO", "Adam Driver will play Mister Sinister; Delgado was not involved."),
        ("WILLIAM", "WARNER", "Paramount's Warner Bros. bid divides Hollywood."),
    ])
    def test_real_false_matches_from_production(self, first, surname, text):
        assert not self._basis(first, surname, text)
