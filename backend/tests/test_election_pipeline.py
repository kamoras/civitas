"""Tests for election_pipeline.py: lock acquisition (mirrors
test_house_pipeline.py's pattern), and the pure roster-sync/financial-
prioritization/snapshot helper functions directly."""

import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta
from unittest.mock import patch

from app.config import settings
from app.models import (
    Candidate,
    ElectionPipelineRun,
    PipelineStatus,
    Race,
    RaceCoverageItem,
    ScoreSnapshot,
)
from app.pipeline import election_pipeline
from app.time_utils import utcnow


def _mock_downstream_pipeline_phases(stack: ExitStack) -> None:
    """Patches every real phase run_election_pipeline runs AFTER roster
    sync. Each one already has its own dedicated tests elsewhere; left
    unmocked here, they run for real inside a real make_async_client()
    context — genuine network attempts to every state's confirmed-
    candidates vendor, every ballot-lookup link, RSS feeds, etc., each
    with its own real retry/backoff on failure.

    Confirmed live (2026-09-05): with only fetch_all_candidates mocked,
    this file's two run_election_pipeline() tests took ~275s and ~50s
    locally — and their real-network dependence made a "fast" CI job's
    duration swing between ~9 and ~15 minutes (once actually timing out)
    across otherwise-unrelated commits. This exists so a test of the
    LOCK/cycle-selection behavior doesn't also pay for (and flake on)
    every downstream phase's real I/O.
    """
    stack.enter_context(patch("app.pipeline.election_pipeline._refresh_financials", return_value=0))
    stack.enter_context(
        patch("app.pipeline.election_pipeline._sync_ballot_measures", return_value={"skipped": True})
    )
    # These three are imported locally inside run_election_pipeline
    # (from app.pipeline.fetch.state_candidates import ...) rather than
    # at module scope, so patching election_pipeline's own namespace
    # wouldn't touch them — the local import re-reads the name from the
    # source module every call, which is exactly what needs patching.
    stack.enter_context(
        patch("app.pipeline.fetch.state_candidates.crawl_for_new_sources", return_value={})
    )
    stack.enter_context(
        patch("app.pipeline.fetch.state_candidates.sync_confirmed_candidates", return_value={})
    )
    stack.enter_context(
        patch("app.pipeline.fetch.state_candidates.sync_ballot_filings", return_value={})
    )
    stack.enter_context(
        patch("app.pipeline.fetch.ballot_lookup.refresh_link_verification", return_value={"failed": 0})
    )
    stack.enter_context(
        patch("app.pipeline.analyze.election_coverage.ingest_race_coverage", return_value=0)
    )
    stack.enter_context(
        patch("app.pipeline.analyze.election_bluesky.post_race_coverage_updates", return_value=0)
    )


class TestElectionPipelineLock:
    def test_skips_when_a_recent_run_is_already_marked_running(self, db_session):
        db_session.add(ElectionPipelineRun(
            started_at=utcnow() - timedelta(minutes=5), status=PipelineStatus.RUNNING,
        ))
        db_session.commit()

        with patch("app.pipeline.election_pipeline.SessionLocal", return_value=db_session):
            result = asyncio.run(election_pipeline.run_election_pipeline())

        assert result == {"status": "skipped", "reason": "already_running"}
        assert db_session.query(ElectionPipelineRun).count() == 1
        assert election_pipeline.is_election_pipeline_running() is False

    def test_stale_running_row_is_cleared_before_a_fresh_attempt(self, db_session):
        stale = ElectionPipelineRun(
            started_at=utcnow() - timedelta(hours=13), status=PipelineStatus.RUNNING,
        )
        db_session.add(stale)
        db_session.commit()
        stale_id = stale.id

        # fetch_all_candidates mocked to fail fast, and every downstream
        # phase mocked off too — only the lock's clear-then-acquire
        # behavior is under test here.
        with ExitStack() as stack:
            stack.enter_context(patch("app.pipeline.election_pipeline.SessionLocal", return_value=db_session))
            stack.enter_context(patch(
                "app.pipeline.election_pipeline.fetch_all_candidates",
                side_effect=RuntimeError("network mocked off"),
            ))
            _mock_downstream_pipeline_phases(stack)
            asyncio.run(election_pipeline.run_election_pipeline())

        cleared = db_session.query(ElectionPipelineRun).filter(ElectionPipelineRun.id == stale_id).one()
        assert cleared.status == PipelineStatus.STALE
        assert db_session.query(ElectionPipelineRun).count() == 2  # stale row + fresh one


class TestCurrentElectionCycle:
    """current_election_cycle() replaces what used to be a frozen
    CURRENT_ELECTION_CYCLE = 2026 constant, so the pipeline (and the
    /elections/races filter) point at the next cycle automatically once
    an election passes, with no code change."""

    def test_during_2026_cycle_returns_2026(self):
        with patch("app.pipeline.election_pipeline.utcnow", return_value=datetime(2026, 7, 25)):
            assert election_pipeline.current_election_cycle() == 2026

    def test_after_2026_election_day_returns_2028(self):
        with patch("app.pipeline.election_pipeline.utcnow", return_value=datetime(2026, 11, 4)):
            assert election_pipeline.current_election_cycle() == 2028

    def test_run_election_pipeline_defaults_to_current_cycle(self, db_session):
        seen_cycles = []

        async def _fake_fetch_all_candidates(client, db, cycle, office):
            seen_cycles.append(cycle)
            return []

        with ExitStack() as stack:
            stack.enter_context(patch("app.pipeline.election_pipeline.SessionLocal", return_value=db_session))
            stack.enter_context(patch("app.pipeline.election_pipeline.utcnow", return_value=datetime(2026, 11, 4)))
            stack.enter_context(patch(
                "app.pipeline.election_pipeline.fetch_all_candidates",
                side_effect=_fake_fetch_all_candidates,
            ))
            _mock_downstream_pipeline_phases(stack)
            asyncio.run(election_pipeline.run_election_pipeline())

        assert seen_cycles == [2028, 2028]  # once for House, once for Senate


class TestRaceId:
    def test_senate_race_id(self):
        assert election_pipeline._race_id(2026, "S", "GA", None) == "2026-SEN-GA"

    def test_house_race_id(self):
        assert election_pipeline._race_id(2026, "H", "CA", 12) == "2026-HOUSE-CA-12"

    def test_house_at_large_defaults_to_zero(self):
        assert election_pipeline._race_id(2026, "H", "WY", None) == "2026-HOUSE-WY-0"


class TestSyncRoster:
    def _raw(self, **overrides):
        # candidate_election_year=2026 by default: _sync_roster re-validates
        # every record's own election year (_on_ballot_in) so a wrong
        # upstream match can't mint a phantom race — records without a
        # confirmed 2026 election are skipped BY DESIGN.
        defaults = dict(
            candidate_id="S6GA001", state="GA", office="S", name="OSSOFF, JON",
            party="DEM", incumbent_challenge="I", has_raised_funds=True,
            candidate_election_year=2026,
        )
        defaults.update(overrides)
        return defaults

    def test_creates_race_and_candidate(self, db_session):
        synced = election_pipeline._sync_roster(db_session, 2026, [self._raw()])
        assert synced == 1
        race = db_session.query(Race).filter(Race.id == "2026-SEN-GA").one()
        assert race.office == "S"
        cand = db_session.query(Candidate).filter(Candidate.id == "S6GA001").one()
        assert cand.name == "OSSOFF, JON"
        assert cand.race_id == "2026-SEN-GA"

    def test_election_years_list_alone_confirms_the_ballot(self, db_session):
        # Some FEC records carry the cycle only in election_years, not in
        # candidate_election_year — either field confirming 2026 is enough.
        raw = self._raw(candidate_election_year=None, election_years=[2024, 2026])
        synced = election_pipeline._sync_roster(db_session, 2026, [raw])
        assert synced == 1

    def test_record_for_a_future_cycle_is_rejected(self, db_session):
        """An early 2028 declarer must not mint a 2026 race (2026-07 review
        F1: the original cycle= query fabricated phantom Senate races in
        ~15 states from exactly these records)."""
        raw = self._raw(
            candidate_id="S8GA001", candidate_election_year=2028,
            election_years=[2028],
        )
        synced = election_pipeline._sync_roster(db_session, 2026, [raw])
        assert synced == 0
        assert db_session.query(Race).count() == 0
        assert db_session.query(Candidate).count() == 0

    def test_non_state_filings_rejected(self, db_session):
        """DC and territorial delegate filings (PR/GU/...) are not federal
        House/Senate races and must not appear in the roster."""
        raws = [
            self._raw(candidate_id="H6PR001", state="PR", office="H"),
            self._raw(candidate_id="H6DC001", state="DC", office="H"),
            self._raw(candidate_id="H6GU001", state="GU", office="H"),
        ]
        synced = election_pipeline._sync_roster(db_session, 2026, raws)
        assert synced == 0
        assert db_session.query(Race).count() == 0
        assert db_session.query(Candidate).count() == 0

    def test_senate_candidate_outside_class_rotation_gets_special_race(self, db_session):
        """FL and OH have no regular (Class II) Senate seat in 2026, so a
        2026 Senate candidate there can only be running in a special
        election — keyed with a -SPECIAL suffix and flagged is_special."""
        raws = [
            self._raw(candidate_id="S6FL001", state="FL"),
            self._raw(candidate_id="S6OH001", state="OH"),
        ]
        synced = election_pipeline._sync_roster(db_session, 2026, raws)
        assert synced == 2

        fl = db_session.query(Race).filter(Race.id == "2026-SEN-FL-SPECIAL").one()
        assert fl.is_special is True
        oh = db_session.query(Race).filter(Race.id == "2026-SEN-OH-SPECIAL").one()
        assert oh.is_special is True

    def test_senate_candidate_in_class_state_gets_regular_race(self, db_session):
        # GA's Class II seat IS up in 2026 — a plain race, not a special.
        election_pipeline._sync_roster(db_session, 2026, [self._raw()])
        race = db_session.query(Race).filter(Race.id == "2026-SEN-GA").one()
        assert race.is_special is False

    def test_candidate_status_stored(self, db_session):
        election_pipeline._sync_roster(
            db_session, 2026, [self._raw(candidate_status="C")],
        )
        cand = db_session.query(Candidate).one()
        assert cand.candidate_status == "C"

    def test_house_candidate_gets_district(self, db_session):
        election_pipeline._sync_roster(db_session, 2026, [self._raw(
            candidate_id="H6CA12001", state="CA", office="H",
            district_number=12,
        )])
        race = db_session.query(Race).filter(Race.id == "2026-HOUSE-CA-12").one()
        assert race.district == 12

    def test_house_at_large_district_zero_is_accepted(self, db_session):
        # FEC's own "00" convention for a single-district state — a real
        # seat, not the missing-district case below.
        synced = election_pipeline._sync_roster(db_session, 2026, [self._raw(
            candidate_id="H6WY001", state="WY", office="H", district_number=0,
        )])
        assert synced == 1
        race = db_session.query(Race).filter(Race.id == "2026-HOUSE-WY-0").one()
        assert race.district == 0

    def test_house_candidate_with_out_of_range_district_is_rejected(self, db_session):
        # 2026-08-26 audit: phantom Race rows for districts that don't
        # exist (FL-59, GA-23, IL-51, NY-28 — real max for GA is 14),
        # populated with garbage-looking FEC filings. GA-23 has no
        # entry in district_pvi.json's real 435-seat map.
        synced = election_pipeline._sync_roster(db_session, 2026, [self._raw(
            candidate_id="H6GA023001", state="GA", office="H", district_number=23,
        )])
        assert synced == 0
        assert db_session.query(Race).count() == 0
        assert db_session.query(Candidate).count() == 0

    def test_house_candidate_with_missing_district_is_rejected(self, db_session):
        # Same audit: a null-district House row with an empty candidate
        # name and party "UNK" — clearly garbage, not a real at-large
        # filing (those carry district_number=0, not a missing field).
        synced = election_pipeline._sync_roster(db_session, 2026, [self._raw(
            candidate_id="H6AZ000001", state="AZ", office="H",
        )])
        assert synced == 0
        assert db_session.query(Race).count() == 0

    def test_updates_existing_candidate_without_duplicating(self, db_session):
        election_pipeline._sync_roster(db_session, 2026, [self._raw(has_raised_funds=False)])
        election_pipeline._sync_roster(db_session, 2026, [self._raw(has_raised_funds=True)])

        assert db_session.query(Candidate).count() == 1
        cand = db_session.query(Candidate).one()
        assert cand.has_raised_funds is True

    def test_malformed_record_skipped_without_failing_the_batch(self, db_session):
        good = self._raw()
        bad = {"candidate_id": None, "state": "GA", "office": "S"}  # missing candidate_id
        synced = election_pipeline._sync_roster(db_session, 2026, [bad, good])
        assert synced == 1
        assert db_session.query(Candidate).count() == 1

    def test_invalid_office_skipped(self, db_session):
        weird = self._raw(candidate_id="P6US001", office="P")  # presidential, not H/S
        synced = election_pipeline._sync_roster(db_session, 2026, [weird])
        assert synced == 0
        assert db_session.query(Candidate).count() == 0


class TestPrioritizeForFinancialRefresh:
    def _add_candidate(self, db, cand_id, race_id, **overrides):
        db.add(Race(id=race_id, cycle_year=2026, office="S", state=race_id[-2:]))
        defaults = dict(name=cand_id, party="DEM")
        defaults.update(overrides)
        db.add(Candidate(id=cand_id, race_id=race_id, **defaults))

    def test_never_synced_before_previously_synced(self, db_session):
        # "old" is synced but well past the cache TTL — still in the pool,
        # just behind the never-synced candidate.
        self._add_candidate(
            db_session, "old", "2026-SEN-GA",
            last_financials_sync=utcnow() - timedelta(hours=settings.PIPELINE_CACHE_TTL_HOURS + 24),
        )
        self._add_candidate(db_session, "new", "2026-SEN-TX", last_financials_sync=None)
        db_session.commit()

        ordered = election_pipeline._prioritize_for_financial_refresh(db_session, limit=10)
        assert [c.id for c in ordered] == ["new", "old"]

    def test_recently_synced_candidate_excluded_entirely(self, db_session):
        """A candidate synced within the FEC cache TTL would be served from
        ApiCache anyway — re-selecting it burns a batch slot reading back
        identical numbers (2026-07 review M3), so it must drop out of the
        pool, not merely sort last."""
        self._add_candidate(
            db_session, "fresh", "2026-SEN-GA",
            incumbent_challenge="I",  # even top priority doesn't override the TTL floor
            last_financials_sync=utcnow() - timedelta(hours=1),
        )
        self._add_candidate(
            db_session, "stale", "2026-SEN-TX",
            last_financials_sync=utcnow() - timedelta(hours=settings.PIPELINE_CACHE_TTL_HOURS + 1),
        )
        db_session.commit()

        ordered = election_pipeline._prioritize_for_financial_refresh(db_session, limit=10)
        assert [c.id for c in ordered] == ["stale"]

    def test_incumbents_before_fundraisers_before_others(self, db_session):
        self._add_candidate(
            db_session, "other", "2026-SEN-GA",
            incumbent_challenge="C", has_raised_funds=False,
        )
        self._add_candidate(
            db_session, "fundraiser", "2026-SEN-TX",
            incumbent_challenge="C", has_raised_funds=True,
        )
        self._add_candidate(
            db_session, "incumbent", "2026-SEN-NY",
            incumbent_challenge="I", has_raised_funds=False,
        )
        db_session.commit()

        ordered = election_pipeline._prioritize_for_financial_refresh(db_session, limit=10)
        assert [c.id for c in ordered] == ["incumbent", "fundraiser", "other"]

    def test_respects_limit(self, db_session):
        for i in range(5):
            self._add_candidate(db_session, f"c{i}", f"2026-SEN-{'GA' if i == 0 else 'X' + str(i)}")
        db_session.commit()

        ordered = election_pipeline._prioritize_for_financial_refresh(db_session, limit=2)
        assert len(ordered) == 2


class TestSnapshotCandidates:
    def test_snapshots_only_candidates_with_cash_on_hand(self, db_session):
        db_session.add(Race(id="2026-SEN-GA", cycle_year=2026, office="S", state="GA"))
        db_session.add(Candidate(
            id="c1", race_id="2026-SEN-GA", name="A", party="DEM",
            cash_on_hand=1000.0, contributions=1200.0, disbursements=200.0,
        ))
        db_session.add(Candidate(id="c2", race_id="2026-SEN-GA", name="B", party="REP"))
        db_session.commit()

        count = election_pipeline._snapshot_candidates(db_session)
        assert count == 1
        snap = db_session.query(ScoreSnapshot).filter(ScoreSnapshot.entity_type == "candidate").one()
        assert snap.entity_id == "c1"
        assert snap.overall_score == 1000.0
        assert snap.score_1 == 1200.0
        assert snap.score_2 == 200.0

    def test_second_call_same_day_replaces_not_duplicates(self, db_session):
        db_session.add(Race(id="2026-SEN-GA", cycle_year=2026, office="S", state="GA"))
        db_session.add(Candidate(id="c1", race_id="2026-SEN-GA", name="A", party="DEM", cash_on_hand=1000.0))
        db_session.commit()

        election_pipeline._snapshot_candidates(db_session)
        election_pipeline._snapshot_candidates(db_session)

        assert db_session.query(ScoreSnapshot).filter(ScoreSnapshot.entity_type == "candidate").count() == 1

    def test_unchanged_figures_next_day_write_no_new_snapshot(self, db_session):
        """Changed-only snapshotting (2026-07 review): FEC totals for most
        candidates only move when a quarterly filing lands, so a nightly
        unconditional snapshot would add millions of no-information rows."""
        db_session.add(Race(id="2026-SEN-GA", cycle_year=2026, office="S", state="GA"))
        db_session.add(Candidate(
            id="c1", race_id="2026-SEN-GA", name="A", party="DEM",
            cash_on_hand=1000.0, contributions=1200.0, disbursements=200.0,
        ))
        db_session.commit()

        day1 = utcnow()
        day2 = day1 + timedelta(days=1)
        with patch("app.pipeline.election_pipeline.utcnow", return_value=day1):
            first = election_pipeline._snapshot_candidates(db_session)
        with patch("app.pipeline.election_pipeline.utcnow", return_value=day2):
            second = election_pipeline._snapshot_candidates(db_session)

        assert first == 1   # first sight always writes
        assert second == 0  # identical figures — skipped
        assert db_session.query(ScoreSnapshot).filter(ScoreSnapshot.entity_type == "candidate").count() == 1

    def test_changed_figures_next_day_write_a_new_snapshot(self, db_session):
        db_session.add(Race(id="2026-SEN-GA", cycle_year=2026, office="S", state="GA"))
        cand = Candidate(
            id="c1", race_id="2026-SEN-GA", name="A", party="DEM",
            cash_on_hand=1000.0, contributions=1200.0, disbursements=200.0,
        )
        db_session.add(cand)
        db_session.commit()

        day1 = utcnow()
        day2 = day1 + timedelta(days=1)
        with patch("app.pipeline.election_pipeline.utcnow", return_value=day1):
            election_pipeline._snapshot_candidates(db_session)

        cand.cash_on_hand = 2000.0  # a new filing landed
        db_session.commit()
        with patch("app.pipeline.election_pipeline.utcnow", return_value=day2):
            written = election_pipeline._snapshot_candidates(db_session)

        assert written == 1
        assert db_session.query(ScoreSnapshot).filter(ScoreSnapshot.entity_type == "candidate").count() == 2


class TestPruneStaleCoverage:
    def _item(self, db, url, fetched_at):
        db.add(RaceCoverageItem(
            race_id="2026-SEN-GA", source_type="news", source_name="AP News",
            title="t", url=url, fetched_at=fetched_at,
        ))

    def test_deletes_items_past_retention_and_keeps_recent_ones(self, db_session):
        db_session.add(Race(id="2026-SEN-GA", cycle_year=2026, office="S", state="GA"))
        self._item(db_session, "https://apnews.com/old", utcnow() - timedelta(days=91))
        self._item(db_session, "https://apnews.com/recent", utcnow() - timedelta(days=30))
        db_session.commit()

        deleted = election_pipeline._prune_stale_coverage(db_session)

        assert deleted == 1
        remaining = db_session.query(RaceCoverageItem).one()
        assert remaining.url == "https://apnews.com/recent"
