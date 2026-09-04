"""Tests for election_bluesky.post_race_coverage_updates.

Mocks _generate_post_text and _publish (network/LLM boundary) —
exercises eligibility (full_name matches only), the per-run/per-day/
per-race volume controls, the mark-considered-before-publish commit
ordering, and the stale-item drain, same style as test_bluesky_poster.py's
process_issues_for_bluesky tests.
"""

from datetime import timedelta
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from app.models import Candidate, Race, RaceCoverageItem
from app.pipeline.analyze import election_bluesky
from app.time_utils import utcnow


def _creds(monkeypatch):
    monkeypatch.setattr(election_bluesky.settings, "BSKY_HANDLE", "test.handle", raising=False)
    monkeypatch.setattr(election_bluesky.settings, "BSKY_APP_PASSWORD", "pw", raising=False)


def _race(db, race_id="2026-SEN-GA", state="GA", office="S"):
    r = Race(id=race_id, cycle_year=2026, office=office, state=state)
    db.add(r)
    return r


def _candidate(db, cand_id="S6GA001", race_id="2026-SEN-GA", name="OSSOFF, JON"):
    c = Candidate(id=cand_id, race_id=race_id, name=name, party="DEM")
    db.add(c)
    return c


def _item(db, race_id="2026-SEN-GA", **overrides):
    # full_name + a matched candidate by default — the eligible shape;
    # individual tests override to exercise the gates.
    defaults = dict(
        race_id=race_id, source_type="news", source_name="AP News",
        title="Ossoff holds narrow lead", url="https://apnews.com/a1",
        summary="Polling shows a tight contest.",
        matched_candidate_id="S6GA001", match_basis="full_name",
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
        _candidate(db_session)
        _item(db_session)
        db_session.commit()

        posted = election_bluesky.post_race_coverage_updates(db_session)
        assert posted == 0
        # Not even considered — credentials check short-circuits first.
        assert db_session.query(RaceCoverageItem).one().bsky_posted_at is None

    def test_successful_post_marks_item_and_increments(self, db_session, monkeypatch):
        _creds(monkeypatch)
        _race(db_session)
        _candidate(db_session)
        item = _item(db_session)
        db_session.commit()

        with patch.object(election_bluesky, "_generate_post_text", return_value="Ossoff holds a narrow lead, per new polling."), \
             patch.object(election_bluesky, "_publish", return_value=True) as mock_publish:
            posted = election_bluesky.post_race_coverage_updates(db_session)

        assert posted == 1
        mock_publish.assert_called_once()
        assert item.bsky_posted_at is not None
        assert item.bsky_posted is True  # actually published, counts toward the daily budget

    def test_grounding_failure_marks_considered_but_not_posted(self, db_session, monkeypatch):
        _creds(monkeypatch)
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
        _creds(monkeypatch)
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
        _creds(monkeypatch)
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
        _creds(monkeypatch)
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
        _creds(monkeypatch)
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
        _creds(monkeypatch)
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
        _creds(monkeypatch)
        _race(db_session)
        _candidate(db_session)
        for i in range(election_bluesky.MAX_POSTS_PER_DAY):
            _item(
                db_session, url=f"https://apnews.com/posted{i}",
                bsky_posted=True, bsky_posted_at=utcnow() - timedelta(hours=25),
            )
        _item(db_session, url="https://apnews.com/fresh")
        db_session.commit()

        with patch.object(election_bluesky, "_generate_post_text", return_value="A grounded sentence."), \
             patch.object(election_bluesky, "_publish", return_value=True):
            posted = election_bluesky.post_race_coverage_updates(db_session)

        assert posted == 1

    def test_race_cooldown_skips_second_post_within_window(self, db_session, monkeypatch):
        _creds(monkeypatch)
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
        _creds(monkeypatch)
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
        _creds(monkeypatch)
        _race(db_session)
        _candidate(db_session)
        item = _item(db_session)
        db_session.commit()

        other_session_factory = sessionmaker(bind=db_session.get_bind())
        seen_at_publish_time = {}

        def failing_publish(text, race):
            # A separate session sees only COMMITTED state — this is the
            # exact record a crashed process would leave behind.
            other = other_session_factory()
            try:
                row = other.query(RaceCoverageItem).one()
                seen_at_publish_time["bsky_posted_at"] = row.bsky_posted_at
            finally:
                other.close()
            return False  # publish fails

        with patch.object(election_bluesky, "_generate_post_text", return_value="A grounded sentence."), \
             patch.object(election_bluesky, "_publish", side_effect=failing_publish):
            posted = election_bluesky.post_race_coverage_updates(db_session)

        assert posted == 0
        assert seen_at_publish_time["bsky_posted_at"] is not None  # committed pre-publish
        assert item.bsky_posted is False

        # And no retry on the next run — the item stays considered.
        with patch.object(election_bluesky, "_generate_post_text") as mock_gen:
            assert election_bluesky.post_race_coverage_updates(db_session) == 0
        mock_gen.assert_not_called()

    def test_already_considered_items_skipped(self, db_session, monkeypatch):
        _creds(monkeypatch)
        _race(db_session)
        _candidate(db_session)
        _item(db_session, bsky_posted_at=utcnow() - timedelta(hours=1))
        db_session.commit()

        with patch.object(election_bluesky, "_generate_post_text") as mock_gen:
            posted = election_bluesky.post_race_coverage_updates(db_session)

        assert posted == 0
        mock_gen.assert_not_called()


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
    working. 2026-09: the race id is also carried as a ?race= query param,
    not just a #race- fragment — a fragment is never sent to the server, so
    without the query param /api/og had no way to render a race-specific
    OG card and every post's link preview showed the same generic
    state-level card regardless of which race the post was about."""

    def test_links_to_the_race_s_section_of_its_state_ballot_page(self, db_session):
        race = _race(db_session, race_id="2026-HOUSE-GA-6", state="GA", office="H")
        db_session.commit()

        with patch("app.pipeline.analyze.election_bluesky.publish_post", return_value=True) as mock_publish:
            assert election_bluesky._publish("some text", race) is True

        url = mock_publish.call_args.args[1]
        assert url == "https://civitas-research.org/elections/states/GA?race=2026-HOUSE-GA-6#race-2026-HOUSE-GA-6"
