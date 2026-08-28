"""Coverage for the shared _clear_stuck_runs helper behind the House and
Stock Trades "clear stuck run" admin endpoints (consolidated from two
near-identical copies — see admin.py's _clear_stuck_runs).
"""

from datetime import timedelta
from app.time_utils import utcnow

import pytest
from fastapi import HTTPException

from app.models import HousePipelineRun, StockTradesPipelineRun


@pytest.mark.asyncio
async def test_clear_stuck_house_marks_running_rows_failed(db_session):
    from app.api.admin import admin_clear_stuck_house

    db_session.add(HousePipelineRun(
        status="running",
        started_at=utcnow() - timedelta(hours=9),
    ))
    db_session.commit()

    result = await admin_clear_stuck_house(db=db_session)

    assert result["cleared"] == 1
    row = db_session.query(HousePipelineRun).first()
    assert row.status == "failed"
    assert row.completed_at is not None
    assert row.error_message == "Cleared by admin (container restart)"


@pytest.mark.asyncio
async def test_clear_stuck_stock_trades_marks_running_rows_failed(db_session):
    from app.api.admin import admin_clear_stuck_stock_trades

    db_session.add(StockTradesPipelineRun(
        status="running",
        started_at=utcnow() - timedelta(hours=3),
    ))
    db_session.commit()

    result = await admin_clear_stuck_stock_trades(db=db_session)

    assert result["cleared"] == 1
    row = db_session.query(StockTradesPipelineRun).first()
    assert row.status == "failed"


@pytest.mark.asyncio
async def test_clear_stuck_house_no_op_when_nothing_stuck(db_session):
    from app.api.admin import admin_clear_stuck_house

    result = await admin_clear_stuck_house(db=db_session)
    assert result == {"cleared": 0, "message": "No stuck runs found"}


@pytest.mark.asyncio
async def test_clear_stuck_house_refuses_while_actively_running(db_session, monkeypatch):
    import app.api.admin as admin_module

    monkeypatch.setattr(
        "app.pipeline.house_pipeline.is_house_pipeline_running", lambda: True
    )

    with pytest.raises(HTTPException) as exc_info:
        await admin_module.admin_clear_stuck_house(db=db_session)

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_clear_stuck_election_marks_running_rows_failed(db_session):
    from app.models import ElectionPipelineRun
    from app.api.admin import admin_clear_stuck_election

    db_session.add(ElectionPipelineRun(
        status="running",
        started_at=utcnow() - timedelta(hours=13),
    ))
    db_session.commit()

    result = await admin_clear_stuck_election(db=db_session)

    assert result["cleared"] == 1
    row = db_session.query(ElectionPipelineRun).first()
    assert row.status == "failed"


@pytest.mark.asyncio
async def test_clear_stuck_election_refuses_while_actively_running(db_session, monkeypatch):
    import app.api.admin as admin_module

    monkeypatch.setattr(
        "app.pipeline.election_pipeline.is_election_pipeline_running", lambda: True
    )

    with pytest.raises(HTTPException) as exc_info:
        await admin_module.admin_clear_stuck_election(db=db_session)

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_trigger_election_pipeline_spawns_background_thread():
    from unittest.mock import patch
    from app.api.admin import admin_trigger_election_pipeline

    with patch("app.api.admin.run_pipeline_in_thread") as mock_run:
        result = await admin_trigger_election_pipeline()

    assert result == {"message": "Election pipeline triggered"}
    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs["name"] == "election-pipeline-run"
