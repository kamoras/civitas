"""The public run status names each chain link's latest run.

The records band used to read only `lastRun` (the Senate's), so one failed
Senate run printed "LAST RUN FAILED" over a night where the other four
links completed.
"""

from datetime import datetime, timedelta

from app.api.pipeline import pipeline_status
from app.models import (
    ElectionPipelineRun,
    HousePipelineRun,
    PipelineRun,
    StockTradesPipelineRun,
    SupplementaryPipelineRun,
)

T0 = datetime(2026, 10, 15, 3, 0)


def test_one_failed_link_is_named_and_the_rest_counted(db_session, freeze_utcnow):
    freeze_utcnow(T0 + timedelta(hours=6))
    db_session.add_all([
        # An older completed Senate run, then the night's failure.
        PipelineRun(status="completed", started_at=T0 - timedelta(days=1),
                    completed_at=T0 - timedelta(days=1, hours=-1)),
        PipelineRun(status="failed", started_at=T0, completed_at=T0 + timedelta(minutes=12),
                    error_message="database is locked"),
        SupplementaryPipelineRun(status="completed", started_at=T0 + timedelta(minutes=15),
                                 completed_at=T0 + timedelta(hours=1)),
        HousePipelineRun(status="completed", started_at=T0 + timedelta(hours=1),
                         completed_at=T0 + timedelta(hours=3, minutes=47)),
        StockTradesPipelineRun(status="completed", started_at=T0 + timedelta(hours=4),
                               completed_at=T0 + timedelta(hours=4, minutes=30)),
        ElectionPipelineRun(status="completed", started_at=T0 + timedelta(hours=4, minutes=30),
                            completed_at=T0 + timedelta(hours=5)),
    ])
    db_session.commit()

    status = pipeline_status(db=db_session)

    assert [(link.pipeline, link.state) for link in status.links] == [
        ("senate", "failed"),
        ("supplementary", "completed"),
        ("house", "completed"),
        ("stock_trades", "completed"),
        ("election", "completed"),
    ]
    assert status.links[0].completed_at == T0 + timedelta(minutes=12)
    assert status.links_completed == 4
    assert status.filed_at == T0 + timedelta(hours=5)


def test_partial_is_not_counted_complete_and_a_dead_running_row_is_failed(db_session, freeze_utcnow):
    freeze_utcnow(T0 + timedelta(days=2))
    db_session.add_all([
        HousePipelineRun(status="partial", started_at=T0, completed_at=T0 + timedelta(hours=2)),
        # Past STALE_PIPELINE_TIMEOUT: the run that wrote it is gone.
        StockTradesPipelineRun(status="running", started_at=T0),
    ])
    db_session.commit()

    status = pipeline_status(db=db_session)

    assert {link.pipeline: link.state for link in status.links} == {
        "house": "partial",
        "stock_trades": "failed",
    }
    assert status.links_completed == 0
    assert status.filed_at is None


def test_a_live_run_reads_as_running(db_session, freeze_utcnow):
    freeze_utcnow(T0 + timedelta(minutes=5))
    db_session.add(ElectionPipelineRun(status="running", started_at=T0))
    db_session.commit()

    status = pipeline_status(db=db_session)

    assert [(link.pipeline, link.state) for link in status.links] == [("election", "running")]
