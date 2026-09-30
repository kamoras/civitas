"""Coverage for the shared _clear_stuck_runs helper behind the House and
Stock Trades "clear stuck run" admin endpoints (consolidated from two
near-identical copies — see admin.py's _clear_stuck_runs).
"""

from datetime import timedelta
from app.time_utils import utcnow

import pytest
from fastapi import HTTPException

from app.models import ElectionPipelineRun, HousePipelineRun, StockTradesPipelineRun


@pytest.mark.parametrize("endpoint, model, hours", [
    ("admin_clear_stuck_house", HousePipelineRun, 9),
    ("admin_clear_stuck_stock_trades", StockTradesPipelineRun, 3),
    ("admin_clear_stuck_election", ElectionPipelineRun, 13),
])
@pytest.mark.asyncio
async def test_clear_stuck_marks_running_rows_failed(db_session, endpoint, model, hours):
    from app.api import admin

    db_session.add(model(status="running", started_at=utcnow() - timedelta(hours=hours)))
    db_session.commit()

    result = await getattr(admin, endpoint)(db=db_session)

    assert result["cleared"] == 1
    row = db_session.query(model).first()
    assert row.status == "failed"
    assert row.completed_at is not None
    assert row.error_message == "Cleared by admin (container restart)"


@pytest.mark.asyncio
async def test_clear_stuck_house_no_op_when_nothing_stuck(db_session):
    from app.api.admin import admin_clear_stuck_house

    result = await admin_clear_stuck_house(db=db_session)
    assert result == {"cleared": 0, "message": "No stuck runs found"}


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


@pytest.mark.asyncio
async def test_clear_stuck_reads_the_rows_before_the_flag_and_clears_only_those(db_session, monkeypatch):
    """A run raises its flag before its row commits, so the flag is checked
    after the rows: a run starting between the two is refused (409), and a
    row that appeared after the read is never the one cleared."""
    from app.api.admin import admin_clear_stuck_house

    stuck = HousePipelineRun(status="running", started_at=utcnow() - timedelta(hours=9))
    db_session.add(stuck)
    db_session.commit()
    stuck_id = stuck.id
    started = []

    def meanwhile_a_run_starts():
        # Another clear finishes the stuck row, and a new run starts — all
        # between this clear's row read and its flag check.
        db_session.query(HousePipelineRun).filter_by(id=stuck_id).update({"status": "failed"})
        db_session.add(HousePipelineRun(status="running", started_at=utcnow()))
        db_session.commit()
        started.append(True)
        return False  # its flag not yet seen: only the row check protects it

    monkeypatch.setattr("app.pipeline.house_pipeline.is_house_pipeline_running", meanwhile_a_run_starts)
    result = await admin_clear_stuck_house(db=db_session)
    assert started and result["cleared"] == 0
    db_session.expire_all()
    assert db_session.query(HousePipelineRun).filter_by(status="running").count() == 1

    monkeypatch.setattr("app.pipeline.house_pipeline.is_house_pipeline_running", lambda: True)
    with pytest.raises(HTTPException) as refused:
        await admin_clear_stuck_house(db=db_session)
    assert refused.value.status_code == 409
    assert db_session.query(HousePipelineRun).filter_by(status="running").count() == 1
