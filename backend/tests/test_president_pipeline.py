"""Tests for president_pipeline.py's score-history snapshotting."""

from unittest.mock import AsyncMock, patch

import pytest

from app.models import President, ScoreSnapshot
from app.pipeline.analyze.president_scorer import PRESIDENT_ALGORITHM_VERSION, calc_public_mandate
from app.pipeline.president_pipeline import _record_president_snapshots, run_president_pipeline


def _make_president(**overrides) -> President:
    defaults = dict(
        id="test-prez", name="Test President", party="D", number=99,
        term_start="2021-01-20", term_end=None, is_current=True,
        score_public_mandate=60.0, score_effectiveness=55.0,
        score_agency_alignment=65.0,
    )
    defaults.update(overrides)
    return President(**defaults)


class TestRecordPresidentSnapshots:
    def test_writes_one_snapshot_per_president(self, db_session):
        db_session.add(_make_president())
        db_session.add(_make_president(id="test-prez-2", number=98, score_public_mandate=40.0))
        db_session.commit()

        _record_president_snapshots(db_session)

        snapshots = db_session.query(ScoreSnapshot).filter(
            ScoreSnapshot.entity_type == "president",
        ).all()
        assert len(snapshots) == 2
        assert {s.entity_id for s in snapshots} == {"test-prez", "test-prez-2"}

    def test_maps_dimensions_to_score_slots_correctly(self, db_session):
        db_session.add(_make_president(
            score_public_mandate=60.0, score_effectiveness=55.0,
            score_agency_alignment=65.0,
        ))
        db_session.commit()

        _record_president_snapshots(db_session)

        snap = db_session.query(ScoreSnapshot).filter(
            ScoreSnapshot.entity_type == "president", ScoreSnapshot.entity_id == "test-prez",
        ).first()
        assert snap.score_1 == 60.0  # publicMandate
        assert snap.score_2 == 55.0  # effectiveness
        assert snap.score_3 == 0.0  # competence, retired 2026-07 — always 0.0 now
        assert snap.score_4 == 65.0  # agencyAlignment
        # Pin the exact version, not just non-null: trend charts key formula-
        # change markers off this string, so a wrong stamp (e.g. the senator
        # ALGORITHM_VERSION copy-pasted in) must fail here.
        assert snap.algorithm_version == PRESIDENT_ALGORITHM_VERSION

    def test_rerunning_same_day_upserts_not_duplicates(self, db_session):
        p = _make_president()
        db_session.add(p)
        db_session.commit()

        _record_president_snapshots(db_session)
        p.score_public_mandate = 80.0
        db_session.commit()
        _record_president_snapshots(db_session)

        snapshots = db_session.query(ScoreSnapshot).filter(
            ScoreSnapshot.entity_type == "president", ScoreSnapshot.entity_id == "test-prez",
        ).all()
        assert len(snapshots) == 1
        assert snapshots[0].score_1 == 80.0

    def test_historical_president_with_unchanging_score_still_gets_snapshotted(self, db_session):
        # Not in DYNAMIC_PRESIDENTS/ECONOMICS_ONLY_PRESIDENTS — score never
        # moves, but the trend line still needs a continuous daily row,
        # same as senators/reps.
        db_session.add(_make_president(id="lincoln-16", number=16, is_current=False))
        db_session.commit()

        _record_president_snapshots(db_session)

        snap = db_session.query(ScoreSnapshot).filter(
            ScoreSnapshot.entity_type == "president", ScoreSnapshot.entity_id == "lincoln-16",
        ).first()
        assert snap is not None


def _patch_fetchers(
    eo_data=None, rulemaking_data=None, gdp_by_year=None, jobs=None,
    approval_polls=None, election_margin_data=None, historical_legacy_data=None,
):
    """Patches every live-data fetch president_pipeline.run_president_
    pipeline calls, so a test controls exactly what "this run" returned
    without touching a real network."""
    return [
        patch("app.pipeline.president_pipeline.fetch_historical_eo_counts", new=AsyncMock(return_value=eo_data or {})),
        patch("app.pipeline.president_pipeline.fetch_presidential_roster", new=AsyncMock(return_value=[])),
        patch("app.pipeline.president_pipeline.fetch_all_rulemaking_stats", new=AsyncMock(return_value=rulemaking_data or {})),
        patch("app.pipeline.president_pipeline.fetch_historical_real_gdp", new=AsyncMock(return_value=gdp_by_year or {})),
        patch("app.pipeline.president_pipeline.fetch_jobs_for_president", new=AsyncMock(return_value=jobs)),
        patch("app.pipeline.president_pipeline.fetch_president_approval_history", new=AsyncMock(return_value=approval_polls or [])),
        patch("app.pipeline.president_pipeline.fetch_election_margins", new=AsyncMock(return_value=election_margin_data or {})),
        patch("app.pipeline.president_pipeline.fetch_cspan_historians_survey", new=AsyncMock(return_value=historical_legacy_data or {})),
    ]


class TestRunPresidentPipelineFetchFailureFallback:
    """2026-07 (#218 review B2): a fetcher returning nothing THIS RUN for a
    president who previously had real data used to get written straight
    into `live`/the DB as an absence, wiping a real score to None (or, for
    Public Mandate specifically, silently switching a modern president's
    scoring basis to the election-margin proxy). Both must instead fall
    back to whatever's already stored."""

    @pytest.mark.asyncio
    async def test_approval_fetch_failure_keeps_stored_value_not_election_margin(self, db_session):
        # obama-44 has an approval-polling source (PRESIDENT_APPROVAL_
        # SLUGS) and already has real stored approval data from a prior
        # run. This run's approval fetch returns nothing (simulated UCSB
        # outage) — election_margin_data still returns a value for
        # obama-44 too, since the real mandates table covers every
        # president 1824-present, not just the pre-polling-era ones.
        db_session.add(President(
            id="obama-44", name="Barack Obama", party="D", number=44,
            term_start="2009-01-20", term_end="2017-01-20",
            avg_approval=55.0, approval_trend=3.0,
            score_public_mandate=calc_public_mandate(55.0, 3.0, None),
        ))
        db_session.commit()

        patches = _patch_fetchers(
            approval_polls=[],  # this run's fetch: total failure for every president
            election_margin_data={"obama-44": 7.2},
        )
        for p in patches:
            p.start()
        try:
            await run_president_pipeline(db_session)
        finally:
            for p in patches:
                p.stop()

        updated = db_session.query(President).filter(President.id == "obama-44").first()
        assert updated.election_margin is None
        assert updated.avg_approval == 55.0
        assert updated.score_public_mandate == calc_public_mandate(55.0, 3.0, None)

    @pytest.mark.asyncio
    async def test_rulemaking_fetch_failure_keeps_stored_agency_alignment(self, db_session):
        db_session.add(President(
            id="test-agency", name="Test President", party="D", number=90,
            term_start="2001-01-20", term_end="2005-01-20",
            rulemaking_count=1200, rulemaking_finalized_pct=75.0,
        ))
        db_session.commit()

        patches = _patch_fetchers(rulemaking_data={})  # this run's Federal Register fetch: total failure
        for p in patches:
            p.start()
        try:
            await run_president_pipeline(db_session)
        finally:
            for p in patches:
                p.stop()

        updated = db_session.query(President).filter(President.id == "test-agency").first()
        assert updated.rulemaking_count == 1200
        assert updated.score_agency_alignment is not None
