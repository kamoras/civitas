"""Tests for election_pipeline.py: lock acquisition (mirrors
test_house_pipeline.py's pattern), and the pure roster-sync/financial-
prioritization/snapshot helper functions directly."""

import asyncio
from datetime import timedelta
from unittest.mock import patch

from app.models import Candidate, ElectionPipelineRun, PipelineStatus, Race, ScoreSnapshot
from app.pipeline import election_pipeline
from app.time_utils import utcnow


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

        # fetch_all_candidates mocked to fail fast — only the lock's
        # clear-then-acquire behavior is under test here.
        with patch("app.pipeline.election_pipeline.SessionLocal", return_value=db_session), \
             patch(
                 "app.pipeline.election_pipeline.fetch_all_candidates",
                 side_effect=RuntimeError("network mocked off"),
             ):
            asyncio.run(election_pipeline.run_election_pipeline())

        cleared = db_session.query(ElectionPipelineRun).filter(ElectionPipelineRun.id == stale_id).one()
        assert cleared.status == PipelineStatus.STALE
        assert db_session.query(ElectionPipelineRun).count() == 2  # stale row + fresh one


class TestRaceId:
    def test_senate_race_id(self):
        assert election_pipeline._race_id(2026, "S", "GA", None) == "2026-SEN-GA"

    def test_house_race_id(self):
        assert election_pipeline._race_id(2026, "H", "CA", 12) == "2026-HOUSE-CA-12"

    def test_house_at_large_defaults_to_zero(self):
        assert election_pipeline._race_id(2026, "H", "WY", None) == "2026-HOUSE-WY-0"


class TestSyncRoster:
    def _raw(self, **overrides):
        defaults = dict(
            candidate_id="S6GA001", state="GA", office="S", name="OSSOFF, JON",
            party="DEM", incumbent_challenge="I", has_raised_funds=True,
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

    def test_house_candidate_gets_district(self, db_session):
        election_pipeline._sync_roster(db_session, 2026, [self._raw(
            candidate_id="H6CA12001", state="CA", office="H",
            district_number=12,
        )])
        race = db_session.query(Race).filter(Race.id == "2026-HOUSE-CA-12").one()
        assert race.district == 12

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
        self._add_candidate(
            db_session, "old", "2026-SEN-GA",
            last_financials_sync=utcnow() - timedelta(days=1),
        )
        self._add_candidate(db_session, "new", "2026-SEN-TX", last_financials_sync=None)
        db_session.commit()

        ordered = election_pipeline._prioritize_for_financial_refresh(db_session, limit=10)
        assert [c.id for c in ordered] == ["new", "old"]

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
