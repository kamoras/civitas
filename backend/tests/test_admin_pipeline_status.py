"""Regression test for a live UnboundLocalError in admin_pipeline_status.

A stray local `import json` inside the function's `if last_run:` branch
shadowed the module-level `import json` for the whole function body, so
the *earlier* `json.loads(last_house_run.ground_truth_failures)` call (in
the `if last_house_run:` branch, added when the House ground-truth gate
landed) raised UnboundLocalError whenever a HousePipelineRun had
ground_truth_failures set. Reproduced and fixed 2026-07.
"""

import json
from datetime import timedelta

import pytest

from app.models import HousePipelineRun, PipelineRun, StockTradesPipelineRun, SupplementaryPipelineRun
from app.time_utils import utcnow


@pytest.mark.asyncio
async def test_status_endpoint_does_not_crash_with_house_ground_truth_failures(db_session):
    from app.api.admin import admin_pipeline_status

    db_session.add(HousePipelineRun(
        status="completed",
        ground_truth_failures=json.dumps([{"dimension": "PP", "score": 1.0}]),
    ))
    db_session.commit()

    result = await admin_pipeline_status(db=db_session)
    assert result["houseLastRun"]["groundTruthFailures"] == [{"dimension": "PP", "score": 1.0}]


@pytest.mark.asyncio
async def test_status_endpoint_parses_senate_progress_detail(db_session):
    from app.api.admin import admin_pipeline_status

    db_session.add(PipelineRun(
        status="completed",
        progress_detail=json.dumps({"phase": "scoring"}),
    ))
    db_session.commit()

    result = await admin_pipeline_status(db=db_session)
    assert result["lastRun"]["progressSteps"] == {"phase": "scoring"}


@pytest.mark.asyncio
async def test_history_does_not_starve_infrequent_pipelines(db_session):
    """2026-07-23: Senate/House run far more often than Stock Trades/
    Supplementary. The history endpoint queried each pipeline type
    separately (each already capped at `limit`) but then re-truncated the
    combined, interleaved list down to that SAME `limit` — so once enough
    Senate/House runs piled up, Stock Trades and Supplementary's own,
    still-current last run silently fell out of the response entirely,
    even though nothing had actually failed. Reproduces the exact shape:
    20 recent Senate runs plus one much-older Stock Trades run — the Stock
    Trades run must still appear."""
    from app.api.admin import admin_pipeline_history

    now = utcnow()
    for i in range(25):
        db_session.add(PipelineRun(status="completed", started_at=now - timedelta(hours=i)))
    db_session.add(StockTradesPipelineRun(status="completed", started_at=now - timedelta(days=10)))
    db_session.add(SupplementaryPipelineRun(status="completed", started_at=now - timedelta(days=5)))
    db_session.commit()

    result = await admin_pipeline_history(limit=20, db=db_session)
    types = [r["pipelineType"] for r in result]
    assert "stock_trades" in types
    assert "supplementary" in types
