"""Regression test for a live UnboundLocalError in admin_pipeline_status.

A stray local `import json` inside the function's `if last_run:` branch
shadowed the module-level `import json` for the whole function body, so
the *earlier* `json.loads(last_house_run.ground_truth_failures)` call (in
the `if last_house_run:` branch, added when the House ground-truth gate
landed) raised UnboundLocalError whenever a HousePipelineRun had
ground_truth_failures set. Reproduced and fixed 2026-07.
"""

import json

import pytest

from app.models import HousePipelineRun, PipelineRun


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
