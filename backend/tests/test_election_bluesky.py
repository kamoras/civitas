"""Tests for election_bluesky.post_race_coverage_updates.

Mocks _generate_post_text and _publish (network/LLM boundary) —
exercises eligibility (full_name matches only), the per-run/per-day/
per-race volume controls, the mark-considered-before-publish commit
ordering, and the stale-item drain, same style as test_bluesky_poster.py's
process_issues_for_bluesky tests.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import BroadcastPost, Candidate, Race, RaceCoverageItem
from app.pipeline.analyze import election_bluesky
from app.pipeline.analyze import election_bluesky as eb
from app.time_utils import utcnow


def _stub_relevance(monkeypatch, relevant=True):
    """The relevance gate loads an embedding model; unit tests stub it and
    race_relevance has its own tests."""
    from app.pipeline.analyze import race_relevance
    monkeypatch.setattr(race_relevance, "is_relevant", lambda *a, **k: relevant)


def _race(db, race_id="2026-SEN-GA", state="GA", office="S"):
    r = Race(id=race_id, cycle_year=2026, office=office, state=state)
    db.add(r)
    return r


def _candidate(db, cand_id="S6GA001", race_id="2026-SEN-GA", name="BRENNAN, JON"):
    c = Candidate(id=cand_id, race_id=race_id, name=name, party="DEM")
    db.add(c)
    return c


def _item(db, race_id="2026-SEN-GA", **overrides):
    # full_name + a matched candidate by default — the eligible shape;
    # individual tests override to exercise the gates.
    defaults = dict(
        race_id=race_id, source_type="news", source_name="AP News",
        title="Brennan holds narrow lead", url="https://apnews.com/a1",
        summary="Polling shows a tight contest.",
        matched_candidate_id="S6GA001", match_basis="full_name",
    )
    defaults.update(overrides)
    i = RaceCoverageItem(**defaults)
    db.add(i)
    return i


class TestPostRaceCoverageUpdates:
    def test_published_to_the_feed_without_a_bluesky_account(self, db_session, monkeypatch, bluesky_outbox):
        _stub_relevance(monkeypatch)
        _race(db_session)
        _candidate(db_session)
        item = _item(db_session)
        db_session.commit()

        with patch.object(election_bluesky, "_generate_post_text", return_value="Brennan holds a narrow lead."):
            assert election_bluesky.post_race_coverage_updates(db_session) == 1

        post = db_session.query(BroadcastPost).one()
        assert (post.kind, post.state, post.text, post.bsky_status) == ("race", "GA", "Brennan holds a narrow lead.", "off")
        assert (post.title, post.subject) == ("Update on the GA Senate race", "race:2026-SEN-GA")
        assert item.bsky_posted_at is not None
        assert item.bsky_posted is True  # actually published, counts toward the daily budget
        assert bluesky_outbox == []

    def test_grounding_failure_marks_considered_but_not_posted(self, db_session, monkeypatch):
        _stub_relevance(monkeypatch)
        _race(db_session)
        _candidate(db_session)
        item = _item(db_session)
        db_session.commit()

        with patch.object(election_bluesky, "_generate_post_text", return_value=None):
            posted = election_bluesky.post_race_coverage_updates(db_session)

        assert posted == 0
        assert item.bsky_posted_at is not None  # considered, won't be re-evaluated
        assert item.bsky_posted is False

    def test_missing_race_is_skipped_gracefully(self, db_session, monkeypatch):
        _stub_relevance(monkeypatch)
        # No Race row at all for this race_id — a real-world edge case if a
        # race got deleted between coverage ingestion and posting.
        item = _item(db_session, race_id="2026-SEN-NOWHERE", matched_candidate_id=None)
        db_session.commit()

        posted = election_bluesky.post_race_coverage_updates(db_session)
        assert posted == 0
        assert item.bsky_posted_at is not None

    def test_missing_roster_candidate_blocks_the_post(self, db_session, monkeypatch):
        """No roster row to build the grounding roster-fact from — the race
        framing would be an ungrounded claim, so the item is considered but
        never published."""
        _stub_relevance(monkeypatch)
        _race(db_session)
        item = _item(db_session, matched_candidate_id="S6GA-GONE")
        db_session.commit()

        with patch.object(election_bluesky, "_publish", return_value=True) as mock_publish:
            posted = election_bluesky.post_race_coverage_updates(db_session)

        assert posted == 0
        mock_publish.assert_not_called()
        assert item.bsky_posted_at is not None

    def test_surname_context_item_never_posted(self, db_session, monkeypatch):
        """Weaker surname+state matches are display-only on the site — an
        automated post asserts the race association in Civitas's own voice,
        which requires the full_name basis. The item is still marked
        considered eventually (by the stale drain), just never published."""
        _stub_relevance(monkeypatch)
        _race(db_session)
        _candidate(db_session)
        item = _item(
            db_session, match_basis="surname_context",
            fetched_at=utcnow() - timedelta(hours=49),  # old enough for the drain
        )
        db_session.commit()

        with patch.object(election_bluesky, "_generate_post_text") as mock_gen, \
             patch.object(election_bluesky, "_publish", return_value=True) as mock_publish:
            posted = election_bluesky.post_race_coverage_updates(db_session)

        assert posted == 0
        mock_gen.assert_not_called()
        mock_publish.assert_not_called()
        assert item.bsky_posted_at is not None  # drained: considered without posting
        assert item.bsky_posted is False

    def test_respects_max_posts_per_run_cap(self, db_session, monkeypatch):
        _stub_relevance(monkeypatch)
        monkeypatch.setattr(election_bluesky, "MAX_POSTS_PER_RUN", 2)
        # Distinct races — the per-race cooldown would otherwise stop a
        # second post to the same race within one run.
        for i in range(5):
            race_id = f"2026-SEN-R{i}"
            _race(db_session, race_id=race_id, state="GA")
            _candidate(db_session, cand_id=f"C{i}", race_id=race_id)
            _item(db_session, race_id=race_id, url=f"https://apnews.com/a{i}",
                  matched_candidate_id=f"C{i}")
        db_session.commit()

        with patch.object(election_bluesky, "_generate_post_text", return_value="A grounded sentence."), \
             patch.object(election_bluesky, "_publish", return_value=True):
            posted = election_bluesky.post_race_coverage_updates(db_session)

        assert posted == 2
        considered = db_session.query(RaceCoverageItem).filter(
            RaceCoverageItem.bsky_posted_at.isnot(None),
        ).count()
        assert considered == 2

    def test_daily_budget_exhausted_posts_nothing(self, db_session, monkeypatch):
        """With MAX_POSTS_PER_DAY items actually published in the last 24h
        (bsky_posted, not merely considered), the run must not post — the
        per-run cap alone multiplied to 480/day at the 15-minute
        election-season cadence (2026-07 review M4)."""
        _stub_relevance(monkeypatch)
        _race(db_session)
        _candidate(db_session)
        for i in range(election_bluesky.MAX_POSTS_PER_DAY):
            _item(
                db_session, url=f"https://apnews.com/posted{i}",
                bsky_posted=True, bsky_posted_at=utcnow() - timedelta(hours=2),
            )
        fresh = _item(db_session, url="https://apnews.com/fresh")
        db_session.commit()

        with patch.object(election_bluesky, "_generate_post_text") as mock_gen, \
             patch.object(election_bluesky, "_publish", return_value=True) as mock_publish:
            posted = election_bluesky.post_race_coverage_updates(db_session)

        assert posted == 0
        mock_gen.assert_not_called()
        mock_publish.assert_not_called()
        # Budget short-circuits before selection — the fresh item is left
        # unconsidered for a later run with budget, not burned.
        assert fresh.bsky_posted_at is None

    def test_daily_budget_ignores_posts_older_than_24h(self, db_session, monkeypatch):
        _stub_relevance(monkeypatch)
        _race(db_session)
        _candidate(db_session)
        # Budget-exhausting posts go on OTHER races: at 25h they are
        # outside the 24h budget window but inside the 48h per-race
        # cooldown, so keeping them on this race would test the cooldown
        # instead of the budget.
        for i in range(election_bluesky.MAX_POSTS_PER_DAY):
            _race(db_session, race_id=f"2026-SEN-Z{i}", state="GA")
            _item(
                db_session, race_id=f"2026-SEN-Z{i}",
                url=f"https://apnews.com/posted{i}",
                bsky_posted=True, bsky_posted_at=utcnow() - timedelta(hours=25),
            )
        _item(db_session, url="https://apnews.com/fresh")
        db_session.commit()

        with patch.object(election_bluesky, "_generate_post_text", return_value="A grounded sentence."), \
             patch.object(election_bluesky, "_publish", return_value=True):
            posted = election_bluesky.post_race_coverage_updates(db_session)

        assert posted == 1

    def test_race_cooldown_skips_second_post_within_window(self, db_session, monkeypatch):
        _stub_relevance(monkeypatch)
        _race(db_session)
        _candidate(db_session)
        # Actually-published item for this race 1h ago — inside the 6h window.
        _item(
            db_session, url="https://apnews.com/earlier",
            bsky_posted=True, bsky_posted_at=utcnow() - timedelta(hours=1),
        )
        fresh = _item(db_session, url="https://apnews.com/fresh")
        db_session.commit()

        with patch.object(election_bluesky, "_generate_post_text") as mock_gen, \
             patch.object(election_bluesky, "_publish", return_value=True) as mock_publish:
            posted = election_bluesky.post_race_coverage_updates(db_session)

        assert posted == 0
        mock_gen.assert_not_called()
        mock_publish.assert_not_called()
        assert fresh.bsky_posted_at is not None  # considered — not retried forever

    def test_race_cooldown_applies_within_a_single_run(self, db_session, monkeypatch):
        # Two fresh items about one busy race — only the first posts.
        _stub_relevance(monkeypatch)
        _race(db_session)
        _candidate(db_session)
        _item(db_session, url="https://apnews.com/a1")
        _item(db_session, url="https://apnews.com/a2")
        db_session.commit()

        with patch.object(election_bluesky, "_generate_post_text", return_value="A grounded sentence."), \
             patch.object(election_bluesky, "_publish", return_value=True) as mock_publish:
            posted = election_bluesky.post_race_coverage_updates(db_session)

        assert posted == 1
        mock_publish.assert_called_once()

    def test_considered_marker_committed_before_publish(self, db_session, monkeypatch):
        """At-most-once for a public account: the considered marker must be
        durable BEFORE the publish attempt, so a failed/crashed publish can
        never lead to a duplicate post on the next run."""
        _stub_relevance(monkeypatch)
        _race(db_session)
        _candidate(db_session)
        item = _item(db_session)
        db_session.commit()

        other_session_factory = sessionmaker(bind=db_session.get_bind())
        seen_at_publish_time = {}

        def crashing_publish(db, text, race, source_url=None):
            # A separate session sees only COMMITTED state — this is the
            # exact record a crashed process would leave behind.
            other = other_session_factory()
            try:
                row = other.query(RaceCoverageItem).one()
                seen_at_publish_time["bsky_posted_at"] = row.bsky_posted_at
            finally:
                other.close()
            raise RuntimeError("process died mid-publish")

        with patch.object(election_bluesky, "_generate_post_text", return_value="A grounded sentence."), \
             patch.object(election_bluesky, "_publish", side_effect=crashing_publish):
            with pytest.raises(RuntimeError):
                election_bluesky.post_race_coverage_updates(db_session)
        db_session.rollback()

        assert seen_at_publish_time["bsky_posted_at"] is not None  # committed pre-publish
        assert item.bsky_posted is False

        # And no retry on the next run — the item stays considered.
        with patch.object(election_bluesky, "_generate_post_text") as mock_gen:
            assert election_bluesky.post_race_coverage_updates(db_session) == 0
        mock_gen.assert_not_called()

    def test_already_considered_items_skipped(self, db_session, monkeypatch):
        _stub_relevance(monkeypatch)
        _race(db_session)
        _candidate(db_session)
        _item(db_session, bsky_posted_at=utcnow() - timedelta(hours=1))
        db_session.commit()

        with patch.object(election_bluesky, "_generate_post_text") as mock_gen:
            posted = election_bluesky.post_race_coverage_updates(db_session)

        assert posted == 0
        mock_gen.assert_not_called()


class TestRosterFact:
    """_roster_fact resolves matched_candidate_id through app/candidate_
    dedup.py before looking the candidate up — see that module for why:
    a coverage item can be matched against either of two FEC ids for a
    since-refiled candidate, and the site's own race list may by now show
    only the surviving one."""

    def test_resolves_a_dropped_duplicate_id_to_the_surviving_candidates_name(self, db_session):
        # Real Ohio House District 4 data: "WILSON, TAMARA" filed under
        # two FEC ids with byte-identical financials (same real fixture
        # test_elections_state_ballot.py's dedup tests use). H6OH04173 is
        # the id dedupe_candidates would drop from the on-site race list.
        race = _race(db_session, race_id="2026-HOUSE-OH-4", state="OH", office="H")
        race.district = 4
        db_session.add(Candidate(
            id="H2OH04164", race_id="2026-HOUSE-OH-4", name="WILSON, TAMARA", party="IND",
            contributions=22049.51, cash_on_hand=520819.93,
        ))
        db_session.add(Candidate(
            id="H6OH04173", race_id="2026-HOUSE-OH-4", name="WILSON, TAMARA", party="DEM",
            contributions=22049.51, cash_on_hand=520819.93,
        ))
        item = _item(
            db_session, race_id="2026-HOUSE-OH-4", matched_candidate_id="H6OH04173",
        )
        db_session.commit()

        fact = election_bluesky._roster_fact(item, race, db_session)
        assert fact == "FEC filings list WILSON, TAMARA as a candidate in the OH-4 House race."

    def test_a_non_duplicate_id_resolves_to_itself_as_before(self, db_session):
        race = _race(db_session)
        _candidate(db_session)
        item = _item(db_session)
        db_session.commit()

        fact = election_bluesky._roster_fact(item, race, db_session)
        assert fact == "FEC filings list BRENNAN, JON as a candidate in the GA Senate race."

    def test_no_matched_candidate_id_is_none(self, db_session):
        race = _race(db_session)
        item = _item(db_session, matched_candidate_id=None)
        db_session.commit()

        assert election_bluesky._roster_fact(item, race, db_session) is None


class TestDrainStaleUnconsidered:
    def test_marks_old_unconsidered_items_and_keeps_fresh_ones(self, db_session):
        _race(db_session)
        _candidate(db_session)
        old = _item(
            db_session, url="https://apnews.com/old",
            fetched_at=utcnow() - timedelta(hours=50),
        )
        fresh = _item(
            db_session, url="https://apnews.com/fresh",
            fetched_at=utcnow() - timedelta(hours=1),
        )
        db_session.commit()

        drained = election_bluesky._drain_stale_unconsidered(db_session)

        assert drained == 1
        assert old.bsky_posted_at is not None
        assert old.bsky_posted is False  # considered, never published
        assert fresh.bsky_posted_at is None


class TestPublishUrl:
    """_publish builds the URL passed to publish_post — 2026-08: this used
    to point at /elections/{race.id} (a standalone race-detail page); that
    page was merged into the state ballot page, so the link now points
    straight there instead of through the redirect that keeps old links
    working."""

    def test_links_to_the_race_s_section_of_its_state_ballot_page(self, db_session, bluesky_configured):
        race = _race(db_session, race_id="2026-HOUSE-GA-6", state="GA", office="H")
        db_session.commit()

        election_bluesky._publish(db_session, "some text", race)

        url = "https://civitas-research.org/elections/states/GA#race-2026-HOUSE-GA-6"
        assert db_session.query(BroadcastPost).one().url == url
        assert bluesky_configured == [("some text", url)]


class TestOnlyVettedSourcesArePosted:
    """The 2026-09-23 endorsement came from one member of the public's
    Bluesky post, ingested as coverage because it named a candidate on
    that race's FEC roster. Civitas restating a stranger's campaign post
    in its own voice is what made it an endorsement, so the fix is at the
    source, not in the wording."""

    @pytest.mark.parametrize("source_type, source_name, title, posted", [
        pytest.param("bluesky", "@kaseylz.bsky.social", "VOTE JANE DOE!", [],
                     id="an_arbitrary_social_post_is_never_eligible"),
        pytest.param("news", "Roll Call", "A real article about the race", ["some sentence."],
                     id="a_news_item_still_posts"),
    ])
    def test_only_a_vetted_source_is_restated(self, db_session, monkeypatch, source_type, source_name, title, posted):
        _stub_relevance(monkeypatch)
        published = []
        monkeypatch.setattr(eb, "_publish", lambda db, text, race, source_url=None: published.append(text))
        monkeypatch.setattr(eb, "_generate_post_text", lambda *a, **k: "some sentence.")
        monkeypatch.setattr(eb, "_roster_fact", lambda *a, **k: "FEC filings list X.")

        db_session.add(Race(id="2026-SEN-NJ", cycle_year=2026, office="S", state="NJ"))
        db_session.add(RaceCoverageItem(
            race_id="2026-SEN-NJ", source_type=source_type, source_name=source_name,
            title=title, url="u1", match_basis="full_name", fetched_at=utcnow(),
        ))
        db_session.commit()

        assert eb.post_race_coverage_updates(db_session) == len(posted)
        assert published == posted


def test_no_item_is_started_past_the_deadline(db_session, monkeypatch):
    """Where the coverage guards stop holding (lease.deadline): the loop
    never awaits, so it stops itself, leaving the rest for the next run."""
    import time

    _stub_relevance(monkeypatch)
    _race(db_session)
    _candidate(db_session)
    item = _item(db_session)
    db_session.commit()

    with patch.object(election_bluesky, "_publish", return_value=True) as mock_publish:
        posted = election_bluesky.post_race_coverage_updates(db_session, deadline=time.monotonic() - 1)
    assert posted == 0
    mock_publish.assert_not_called()
    assert item.bsky_posted_at is None


def test_a_data_reset_does_not_reset_the_budget_or_the_cooldown(db_session, monkeypatch):
    """A reset wipes the coverage items (and their posted marks) but keeps
    what was published, so re-ingested coverage of a race posted an hour
    ago is still inside its cooldown, and the day's posts still count."""
    from app import broadcast

    _stub_relevance(monkeypatch)
    race = _race(db_session)
    _candidate(db_session)
    db_session.commit()
    broadcast.publish(db_session, kind="race", subject=f"race:{race.id}", title="t", text="x", url="u", state="GA")
    for i in range(election_bluesky.MAX_POSTS_PER_DAY - 1):
        broadcast.publish(db_session, kind="race", subject=f"race:other-{i}", title="t", text="x", url="u")

    assert election_bluesky._races_posted_recently(db_session) >= {race.id}
    assert election_bluesky._posts_in_last_day(db_session) == election_bluesky.MAX_POSTS_PER_DAY

    _item(db_session)  # re-ingested after the reset: no posted mark
    db_session.commit()
    with patch.object(election_bluesky, "_generate_post_text", return_value="A sentence.") as gen:
        assert election_bluesky.post_race_coverage_updates(db_session) == 0
    gen.assert_not_called()


def test_an_old_article_fetched_again_is_drained_by_its_own_date(db_session):
    """After a data reset the same articles come back with a fresh fetch
    time; their own publication date still says how old the story is."""
    _race(db_session)
    old = _item(db_session, url="https://apnews.com/old", published_at=utcnow() - timedelta(days=3))
    fresh = _item(db_session, url="https://apnews.com/new", published_at=utcnow() - timedelta(hours=2))
    undated = _item(db_session, url="https://apnews.com/undated")
    db_session.commit()

    assert election_bluesky._drain_stale_unconsidered(db_session) == 1
    assert old.bsky_posted_at is not None
    assert fresh.bsky_posted_at is None and undated.bsky_posted_at is None


def test_an_article_already_published_about_is_never_published_again(db_session, monkeypatch):
    """An undated article posted about three days ago, re-ingested after a
    data reset: past the race's cooldown and with no date to drain it by,
    it is still the same article."""
    from app import broadcast
    from app.models import BroadcastPost

    _stub_relevance(monkeypatch)
    race = _race(db_session)
    _candidate(db_session)
    db_session.commit()
    old = broadcast.publish(db_session, kind="race", subject=f"race:{race.id}", title="t", text="x",
                            url="u", state="GA", source_url="https://apnews.com/a1")
    old.published_at = utcnow() - timedelta(days=3)
    db_session.commit()

    _item(db_session, url="https://apnews.com/a1")  # same article, fresh fetch, no date
    db_session.commit()
    with patch.object(election_bluesky, "_generate_post_text", return_value="A sentence.") as gen:
        assert election_bluesky.post_race_coverage_updates(db_session) == 0
    gen.assert_not_called()

    _item(db_session, url="https://apnews.com/a2")  # a different article about the race
    db_session.commit()
    with patch.object(election_bluesky, "_generate_post_text", return_value="A sentence."):
        assert election_bluesky.post_race_coverage_updates(db_session) == 1
    assert db_session.query(BroadcastPost).order_by(BroadcastPost.id.desc()).first().source_url == (
        "https://apnews.com/a2")


class TestGeneratePostText:
    """The composed sentence is published whole or not at all."""

    def _generate(self, monkeypatch, predicate):
        monkeypatch.setattr(eb, "call_llm", lambda **k: {"actor": "Jon Brennan", "predicate": predicate})
        race = Race(id="2026-SEN-GA", cycle_year=2026, office="S", state="GA")
        item = RaceCoverageItem(race_id=race.id, title=f"Jon Brennan {predicate}", summary="", url="https://apnews.com/a1")
        return eb._generate_post_text(item, race, "FEC filings list BRENNAN, JON as a candidate in the GA Senate race.")

    def test_a_post_that_fits_is_returned_whole(self, monkeypatch):
        assert self._generate(monkeypatch, "leads the race") == "Jon Brennan leads the race."

    def test_a_post_too_long_for_bluesky_beside_its_link_is_not_cut(self, monkeypatch):
        # Cut at a word boundary, this read "...said Sens." or stopped before
        # the object its verb needs, and the feed stored the cut text.
        clause = "said Sens. Warnock and Brennan " + "would fund rural hospitals and roads " * 7 + "this year"
        race = Race(id="2026-SEN-GA", cycle_year=2026, office="S", state="GA")
        assert len(f"Jon Brennan {clause}.") > eb._post_budget(race)
        assert self._generate(monkeypatch, clause) is None
