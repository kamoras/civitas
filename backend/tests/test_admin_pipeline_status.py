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


@pytest.mark.asyncio
async def test_status_endpoint_includes_election_last_run(db_session):
    from app.models import ElectionPipelineRun
    from app.api.admin import admin_pipeline_status

    db_session.add(ElectionPipelineRun(
        status="completed",
        candidates_synced=6917,
        financials_refreshed=500,
        coverage_items_ingested=42,
    ))
    db_session.commit()

    result = await admin_pipeline_status(db=db_session)
    assert result["electionLastRun"]["candidatesSynced"] == 6917
    assert result["electionLastRun"]["coverageItemsIngested"] == 42
    assert "electionIsRunning" in result


@pytest.mark.asyncio
async def test_history_includes_election_pipeline_type(db_session):
    from app.models import ElectionPipelineRun
    from app.api.admin import admin_pipeline_history

    db_session.add(ElectionPipelineRun(
        status="completed",
        started_at=utcnow(),
        candidates_synced=6917,
        financials_refreshed=500,
        coverage_items_ingested=42,
    ))
    db_session.commit()

    result = await admin_pipeline_history(limit=20, db=db_session)
    types = [r["pipelineType"] for r in result]
    assert "election" in types

    # The admin run-history table renders an Election row's PROCESSED cell
    # straight from these three keys (frontend: lib/pipelineRuns.ts). Assert
    # the exact camelCase names, not just that the row exists — renaming one
    # here would put the row back to showing zeros with nothing failing.
    entry = next(r for r in result if r["pipelineType"] == "election")
    assert entry["candidatesSynced"] == 6917
    assert entry["financialsRefreshed"] == 500
    assert entry["coverageItemsIngested"] == 42


@pytest.mark.asyncio
async def test_status_reports_a_data_reset_so_deploys_wait_it_out(db_session):
    """check-and-deploy.sh reads dataResetIsRunning with the pipeline flags:
    killing a reset mid-wipe leaves indexes describing rows that are gone."""
    from app.api.admin import admin_pipeline_status
    from app.pipeline import lease

    assert (await admin_pipeline_status(db=db_session))["dataResetIsRunning"] is False
    lease.acquire(db_session, lease.DATA_RESET)
    assert (await admin_pipeline_status(db=db_session))["dataResetIsRunning"] is True




@pytest.mark.asyncio
async def test_status_reports_an_explore_index_rebuild_so_deploys_wait_it_out(db_session):
    from app.api.admin import admin_pipeline_status
    from app.pipeline import vector_store

    assert (await admin_pipeline_status(db=db_session))["exploreIndexIsRebuilding"] is False
    with vector_store.rebuild_underway():
        assert (await admin_pipeline_status(db=db_session))["exploreIndexIsRebuilding"] is True


@pytest.mark.asyncio
async def test_status_reports_an_explore_run_so_deploys_wait_it_out(db_session):
    # A triggered or startup Explore run has no run row: its lease is it.
    from app.api.admin import admin_pipeline_status
    from app.pipeline import lease

    assert (await admin_pipeline_status(db=db_session))["exploreIsRunning"] is False
    lease.acquire(db_session, lease.EXPLORE)
    assert (await admin_pipeline_status(db=db_session))["exploreIsRunning"] is True


@pytest.mark.asyncio
async def test_check_and_deploy_waits_on_every_busy_flag_the_status_reports(db_session):
    # electionIsRunning was once published and not read: a deploy killed an
    # election run five minutes in. Every top-level boolean named for work
    # in progress is one the deploy script must read.
    import re
    from pathlib import Path

    from app.api.admin import admin_pipeline_status

    status = await admin_pipeline_status(db=db_session)
    flags = {k for k, v in status.items() if isinstance(v, bool) and re.search(r"[iI]s(Running|Rebuilding)$", k)}
    script = (Path(__file__).resolve().parents[2] / "check-and-deploy.sh").read_text()
    assert "isRunning" in flags and all(f'"{flag}"' in script for flag in flags), flags


_FLAGS = [
    ("app.pipeline.house_pipeline", "is_house_pipeline_running", "houseIsRunning"),
    ("app.pipeline.stock_pipeline", "is_stock_pipeline_running", "stockTradesIsRunning"),
    ("app.pipeline.supplementary_pipeline", "is_supplementary_pipeline_running", "supplementaryIsRunning"),
    ("app.pipeline.election_pipeline", "is_election_pipeline_running", "electionIsRunning"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("raised_before_rows", [True, False], ids=["finishing", "starting"])
async def test_a_run_changing_state_during_a_poll_never_reads_as_stuck(db_session, monkeypatch, raised_before_rows):
    """A run commits its RUNNING row before raising its flag and its final
    status before dropping it. A flag read only on one side of the row
    queries would, for a run starting (or finishing) mid-poll, pair a
    lowered flag with a RUNNING row — shown as stuck. Each flag here is up
    on exactly one side of the first query; every pipeline must still read
    as running."""
    from sqlalchemy import event

    from app.api.admin import admin_pipeline_status

    queried = []
    for module, name, _key in _FLAGS:
        monkeypatch.setattr(
            f"{module}.{name}", lambda: (not queried) if raised_before_rows else bool(queried),
        )

    def on_query(*_args):
        queried.append(True)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", on_query)
    try:
        result = await admin_pipeline_status(db=db_session)
    finally:
        event.remove(engine, "before_cursor_execute", on_query)
    assert all(result[key] for _m, _n, key in _FLAGS)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reads, running, clearable",
    [
        ([(None, False, False), (7, True, False)], True, False),  # started during the poll
        ([(7, True, False), (None, False, False)], True, False),  # finished during it
        ([(7, True, True), (7, True, True)], True, True),  # stuck on both reads: offered Clear
        ([(7, True, True), (8, True, False)], True, False),  # cleared and restarted meanwhile
    ],
)
async def test_the_senate_state_is_read_on_both_sides_of_its_row(db_session, monkeypatch, reads, running, clearable):
    from app.api.admin import admin_pipeline_status

    calls = iter(reads)
    monkeypatch.setattr("app.pipeline.run_tracker.senate_run_state", lambda db: next(calls))
    result = await admin_pipeline_status(db=db_session)
    assert (result["isRunning"], result["senateRowClearable"]) == (running, clearable)
