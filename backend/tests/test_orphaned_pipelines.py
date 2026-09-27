"""Startup sweep of pipeline rows a restart left marked running."""

from unittest.mock import patch

from app.main import _invalidate_orphaned_pipelines
from app.models import (
    ElectionPipelineRun,
    HousePipelineRun,
    PipelineRun,
    PipelineStatus,
    StockTradesPipelineRun,
    SupplementaryPipelineRun,
)

MODELS = (PipelineRun, SupplementaryPipelineRun, HousePipelineRun, StockTradesPipelineRun, ElectionPipelineRun)


def test_every_pipelines_running_row_is_swept(db_session):
    """A restart kills every pipeline thread, not only the Senate's. Before,
    only PipelineRun was swept, so a killed election run read "running"
    until STALE_PIPELINE_TIMEOUT aged it out."""
    for model in MODELS:
        db_session.add(model(status=PipelineStatus.RUNNING))
        db_session.add(model(status=PipelineStatus.COMPLETED))
    db_session.commit()

    with patch("app.database.SessionLocal", return_value=db_session):
        _invalidate_orphaned_pipelines()

    for model in MODELS:
        statuses = sorted(r.status for r in db_session.query(model).all())
        assert statuses == sorted([PipelineStatus.STALE, PipelineStatus.COMPLETED]), model.__name__
        stale = db_session.query(model).filter(model.status == PipelineStatus.STALE).one()
        assert stale.completed_at is not None
        assert "restarted" in stale.error_message
