"""Tests for durable per-phase pipeline timings.

Before these rows existed, the only cross-run timing a pipeline retained
was the run row's total `elapsed_seconds`. ProgressTracker was already
recording per-step startedAt/completedAt, but only into `progress_detail`
— a JSON blob overwritten by the next run — so a pipeline whose wall
clock grew from 90 minutes to 12 hours offered no way to tell which step
absorbed the difference. These tests cover the durable rows and the
rollup the admin endpoint computes from them.
"""

import json
from datetime import timedelta

import pytest

from app.models import HousePipelineRun, PipelinePhaseTiming, PipelineRun
from app.pipeline.progress_tracker import ProgressTracker
from app.time_utils import utcnow

STEPS = [
    ("fetch_a", "fetch", "Fetch A"),
    ("fetch_b", "fetch", "Fetch B"),
    ("analyze_a", "analyze", "Analyze A"),
    ("finalize", "finalize", "Finalize"),
]


def _tracker(db, run, steps=STEPS):
    db.add(run)
    db.commit()
    return ProgressTracker(run, steps, db, start_time=0.0)


def test_completing_a_step_writes_a_durable_timing_row(db_session):
    run = PipelineRun(status="running")
    tracker = _tracker(db_session, run)

    tracker.begin("fetch_a")
    tracker.complete("fetch_a")

    rows = db_session.query(PipelinePhaseTiming).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.run_kind == "pipeline_runs"
    assert row.run_id == run.id
    assert row.step_key == "fetch_a"
    assert row.phase == "fetch"
    assert row.label == "Fetch A"
    assert row.status == "done"
    assert row.started_at is not None
    assert row.completed_at is not None
    assert row.duration_seconds is not None and row.duration_seconds >= 0


def test_run_kind_is_derived_from_the_run_model(db_session):
    """No pipeline passes a run_kind — it comes off the row's __tablename__,
    which is what lets all five existing pipelines get timings without any
    change to their call sites."""
    run = HousePipelineRun(status="running")
    tracker = _tracker(db_session, run)

    tracker.begin("fetch_a")
    tracker.complete("fetch_a")

    row = db_session.query(PipelinePhaseTiming).one()
    assert row.run_kind == "house_pipeline_runs"


def test_skipped_and_failed_steps_are_recorded_with_their_status(db_session):
    run = PipelineRun(status="running")
    tracker = _tracker(db_session, run)

    tracker.skip("fetch_a", detail="nothing to do")
    tracker.begin("fetch_b")
    tracker.fail("fetch_b", detail="upstream 500")

    by_key = {r.step_key: r for r in db_session.query(PipelinePhaseTiming).all()}
    assert by_key["fetch_a"].status == "skipped"
    assert by_key["fetch_b"].status == "failed"
    # Skipped before it ever began: no start timestamp, so no duration to
    # report. Recording 0 here would understate a run's true phase totals.
    assert by_key["fetch_a"].duration_seconds is None
    assert by_key["fetch_b"].duration_seconds is not None


def test_repeated_terminal_transitions_update_rather_than_collide(db_session):
    """A retried or resumed step drives the same key terminal twice. The
    unique constraint on (run_kind, run_id, step_key) makes that an update,
    not an IntegrityError that would take out the whole tracker."""
    run = PipelineRun(status="running")
    tracker = _tracker(db_session, run)

    tracker.begin("fetch_a")
    tracker.fail("fetch_a", detail="transient")
    tracker.begin("fetch_a")
    tracker.complete("fetch_a")

    row = db_session.query(PipelinePhaseTiming).one()
    assert row.status == "done"


def test_two_runs_keep_separate_timing_rows(db_session):
    """The whole point of the table: last night's numbers survive tonight's
    run, so a phase that grew is visible as a comparison."""
    for _ in range(2):
        run = PipelineRun(status="running")
        tracker = _tracker(db_session, run)
        tracker.begin("fetch_a")
        tracker.complete("fetch_a")

    rows = db_session.query(PipelinePhaseTiming).all()
    assert len(rows) == 2
    assert len({r.run_id for r in rows}) == 2


def test_unflushed_run_row_records_nothing_and_does_not_raise(db_session):
    """A run row with no id has nothing stable to key timings on. That must
    degrade to "no timings", never to an exception inside the pipeline."""
    run = PipelineRun(status="running")  # deliberately not added/committed
    tracker = ProgressTracker(run, STEPS, db_session, start_time=0.0)

    tracker.begin("fetch_a")
    tracker.complete("fetch_a")

    assert db_session.query(PipelinePhaseTiming).count() == 0


def test_timing_failure_does_not_lose_progress_detail(monkeypatch, db_session):
    """progress_detail drives the admin dashboard and stuck-run detection;
    timings are diagnostic. The timing write commits separately and after,
    so a failure in it can never roll back the operational write."""
    run = PipelineRun(status="running")
    tracker = _tracker(db_session, run)

    import app.pipeline.progress_tracker as pt

    def _boom(self, step):
        raise RuntimeError("timing table exploded")

    monkeypatch.setattr(pt.ProgressTracker, "_record_timing", _boom, raising=True)

    tracker.begin("fetch_a")
    with pytest.raises(RuntimeError):
        tracker.complete("fetch_a")

    # The progress flush happened before the timing write, so it survived.
    steps = json.loads(run.progress_detail)
    assert [s["status"] for s in steps if s["key"] == "fetch_a"] == ["done"]


def test_internal_timing_errors_are_swallowed(monkeypatch, db_session):
    """The real guard: _record_timing's own body must never propagate. A
    diagnostic write is not worth aborting a 12-hour pipeline over."""
    run = PipelineRun(status="running")
    tracker = _tracker(db_session, run)

    def _explode(*args, **kwargs):
        raise RuntimeError("db gone")

    monkeypatch.setattr(db_session, "query", _explode)

    tracker.begin("fetch_a")
    tracker.complete("fetch_a")  # must not raise


@pytest.mark.asyncio
async def test_timings_endpoint_rolls_up_by_phase(db_session):
    from app.api.admin import admin_pipeline_timings

    now = utcnow()
    run = PipelineRun(status="completed")
    db_session.add(run)
    db_session.commit()

    # 100s of fetch across two steps, 20s of analyze.
    for key, phase, secs in [
        ("fetch_a", "fetch", 60.0),
        ("fetch_b", "fetch", 40.0),
        ("analyze_a", "analyze", 20.0),
    ]:
        db_session.add(PipelinePhaseTiming(
            run_kind="pipeline_runs", run_id=run.id, step_key=key, phase=phase,
            label=key, status="done", started_at=now,
            completed_at=now + timedelta(seconds=secs), duration_seconds=secs,
        ))
    db_session.commit()

    result = await admin_pipeline_timings(kind="pipeline_runs", runs=10, db=db_session)

    assert result["pipelineType"] == "senate"
    entry = result["runs"][0]
    assert entry["runId"] == run.id
    assert entry["totalSeconds"] == 120.0

    phases = {p["phase"]: p for p in entry["phases"]}
    assert phases["fetch"]["seconds"] == 100.0
    assert phases["fetch"]["steps"] == 2
    # The number the hardware question turns on: how much of the run was
    # spent in fetch (largely rate-limited waiting) vs. local compute.
    assert phases["fetch"]["pct"] == 83.3
    assert phases["analyze"]["pct"] == 16.7
    # Phases are ordered by cost so the dominant one reads first.
    assert entry["phases"][0]["phase"] == "fetch"
    # Steps are ordered slowest-first for the same reason.
    assert entry["steps"][0]["stepKey"] == "fetch_a"


@pytest.mark.asyncio
async def test_timings_endpoint_reports_untimed_steps_separately(db_session):
    """A step with no duration must not be counted as 0 seconds — that
    would silently understate the run total and every phase percentage."""
    from app.api.admin import admin_pipeline_timings

    run = PipelineRun(status="running")
    db_session.add(run)
    db_session.commit()

    db_session.add(PipelinePhaseTiming(
        run_kind="pipeline_runs", run_id=run.id, step_key="done_step",
        phase="fetch", label="Done", status="done", duration_seconds=30.0,
    ))
    db_session.add(PipelinePhaseTiming(
        run_kind="pipeline_runs", run_id=run.id, step_key="skipped_step",
        phase="fetch", label="Skipped", status="skipped", duration_seconds=None,
    ))
    db_session.commit()

    result = await admin_pipeline_timings(kind="pipeline_runs", runs=10, db=db_session)
    entry = result["runs"][0]
    assert entry["totalSeconds"] == 30.0
    assert entry["untimedSteps"] == 1


@pytest.mark.asyncio
async def test_timings_endpoint_builds_a_per_phase_trend_across_runs(db_session):
    from app.api.admin import admin_pipeline_timings

    for secs in (100.0, 500.0):
        run = PipelineRun(status="completed")
        db_session.add(run)
        db_session.commit()
        db_session.add(PipelinePhaseTiming(
            run_kind="pipeline_runs", run_id=run.id, step_key="fetch_a",
            phase="fetch", label="Fetch A", status="done", duration_seconds=secs,
        ))
        db_session.commit()

    result = await admin_pipeline_timings(kind="pipeline_runs", runs=10, db=db_session)
    trend = [p["seconds"] for p in result["phaseTrend"]["fetch"]]
    # Most recent run first — a phase that grew reads as a descending series.
    assert trend == [500.0, 100.0]


@pytest.mark.asyncio
async def test_timings_endpoint_rejects_an_unknown_kind(db_session):
    from fastapi import HTTPException

    from app.api.admin import admin_pipeline_timings

    with pytest.raises(HTTPException) as exc:
        await admin_pipeline_timings(kind="not_a_pipeline", runs=10, db=db_session)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_timings_endpoint_is_empty_before_any_run_records_timings(db_session):
    from app.api.admin import admin_pipeline_timings

    result = await admin_pipeline_timings(kind="house_pipeline_runs", runs=10, db=db_session)
    assert result["runs"] == []
    assert result["phaseTrend"] == {}
