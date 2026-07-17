"""Tests for run_supplementary_pipeline's admin-visible run tracking.

Extracted from senate_pipeline.py (2026-07): explore-document ingestion,
SCOTUS justice scoring, and president scoring had no data dependency on
Senate's own work — they were only nested inside run_senate_pipeline()
because that pipeline already existed. SupplementaryPipelineRun mirrors
HousePipelineRun/StockTradesPipelineRun so these three genuinely
independent domains get the same admin-dashboard visibility Senate/
House/stock trades already have, instead of piggybacking on Senate's
own PipelineRun row.
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Justice, SupplementaryPipelineRun
from app.pipeline import supplementary_pipeline


@pytest.fixture(autouse=True)
def _reset_tracker():
    supplementary_pipeline._tracker.stop()
    yield
    supplementary_pipeline._tracker.stop()


def _run(db_session, explore_result=None, justice_result=None, president_result=None):
    with patch("app.pipeline.supplementary_pipeline.SessionLocal", return_value=db_session), \
         patch("app.pipeline.explore_pipeline.run_explore_pipeline", new_callable=AsyncMock) as mock_explore, \
         patch("app.pipeline.justice_pipeline.run_justice_pipeline", new_callable=AsyncMock) as mock_justice, \
         patch("app.pipeline.president_pipeline.run_president_pipeline", new_callable=AsyncMock) as mock_president:
        if isinstance(explore_result, Exception):
            mock_explore.side_effect = explore_result
        else:
            mock_explore.return_value = explore_result if explore_result is not None else {"total": 0}
        if isinstance(justice_result, Exception):
            mock_justice.side_effect = justice_result
        else:
            mock_justice.return_value = justice_result if justice_result is not None else {"justices": 0}
        if isinstance(president_result, Exception):
            mock_president.side_effect = president_result
        else:
            mock_president.return_value = president_result if president_result is not None else {"updated": 0}

        import asyncio
        return asyncio.run(supplementary_pipeline.run_supplementary_pipeline())


class TestSupplementaryPipelineRunTracking:
    def test_creates_a_run_row_and_marks_it_completed(self, db_session):
        # Empty Justice table -> justices_missing=True -> always runs,
        # regardless of which day of the week the test happens to run on.
        result = _run(
            db_session,
            explore_result={"docs": 12},
            justice_result={"justices": 9},
            president_result={"updated": 1},
        )

        assert result["status"] == "completed"
        assert result["explore_docs_ingested"] == 12
        assert result["justices_scored"] == 9
        assert result["presidents_updated"] == 1

        run = db_session.query(SupplementaryPipelineRun).one()
        assert run.status == "completed"
        assert run.explore_docs_ingested == 12
        assert run.justices_scored == 9
        assert run.justices_skipped is False
        assert run.presidents_updated == 1
        assert run.current_phase == "finalize"
        assert run.completed_at is not None
        assert run.elapsed_seconds is not None

    def test_in_memory_flag_is_set_during_and_cleared_after(self, db_session):
        assert supplementary_pipeline.is_supplementary_pipeline_running() is False
        _run(db_session)
        # Cleared by the finally block once the (synchronous, in this
        # test) run completes.
        assert supplementary_pipeline.is_supplementary_pipeline_running() is False

    def test_justices_skipped_outside_weekly_cadence_when_not_missing(self, db_session):
        db_session.add(Justice(id="j1", name="Test Justice", last_name="Justice"))
        db_session.commit()

        # Pin "now" to a Wednesday (weekday() == 2), not Sunday (6).
        with patch("app.pipeline.supplementary_pipeline.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 7, 15)  # a Wednesday
            result = _run(db_session, justice_result={"justices": 9})

        assert result["justices_scored"] == 0
        run = db_session.query(SupplementaryPipelineRun).one()
        assert run.justices_skipped is True
        assert run.justices_scored == 0

    def test_one_phase_failing_does_not_block_the_others(self, db_session):
        """Best-effort per phase, matching stock_pipeline's per-chamber pattern."""
        result = _run(
            db_session,
            explore_result=RuntimeError("Explore fetch down"),
            justice_result={"justices": 5},
            president_result={"updated": 2},
        )

        assert result["status"] == "completed"
        assert result["justices_scored"] == 5
        assert result["presidents_updated"] == 2

        run = db_session.query(SupplementaryPipelineRun).one()
        assert run.status == "completed"
        assert run.explore_docs_ingested == 0
        assert run.justices_scored == 5
        assert run.presidents_updated == 2
