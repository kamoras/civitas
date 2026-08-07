"""Tests for per-source rate-limiter blocked-time accounting.

A phase duration says a step took four hours. It does not say whether
those hours were compute or RateLimiter.acquire() deliberately sleeping
to stay under the FEC's 0.25 RPS. Those readings point at opposite
remedies — restructure the fetch phase, or add compute — so the run data
has to tell them apart.
"""

import asyncio

import pytest

from app.models import PipelineRateLimitStat, PipelineRun
from app.pipeline import rate_limiter as rl
from app.pipeline.progress_tracker import ProgressTracker

STEPS = [
    ("fetch_a", "fetch", "Fetch A"),
    ("analyze_a", "analyze", "Analyze A"),
]


@pytest.fixture(autouse=True)
def clean_registry():
    """Limiters register themselves at construction and live for the life
    of the process. Tests build their own, so the registry is reset around
    each one to keep counters from leaking between them."""
    rl.reset_registry()
    yield
    rl.reset_registry()


def test_limiter_counts_requests_and_blocked_time():
    limiter = rl.RateLimiter(rps=50.0, name="test_source")

    async def run():
        for _ in range(3):
            await limiter.acquire()

    asyncio.run(run())

    assert limiter.total_requests == 3
    # First acquire is free; the next two each wait ~1/50s.
    assert limiter.total_blocked_seconds > 0


def test_first_acquire_is_not_counted_as_blocked():
    limiter = rl.RateLimiter(rps=1000.0, name="test_source")
    asyncio.run(limiter.acquire())
    assert limiter.total_requests == 1
    assert limiter.total_blocked_seconds < 0.05


def test_limiter_name_defaults_to_its_module():
    """Every limiter but one is the only limiter in its module, so the
    auto-derived name gives readable per-source accounting with no change
    to any of the 19 construction sites."""
    limiter = rl.RateLimiter(rps=10.0)
    # Constructed from this test module.
    assert limiter.name == "test_rate_limit_accounting"


def test_duplicate_names_are_disambiguated_not_merged():
    """Merged counters would misattribute one source's waiting to another.
    congress.py really does have two limiters with very different
    workloads (the API vs. scraping 535 member websites)."""
    a = rl.RateLimiter(rps=10.0, name="dup")
    b = rl.RateLimiter(rps=10.0, name="dup")
    assert a.name == "dup"
    assert b.name == "dup#2"
    assert set(rl.snapshot()) == {"dup", "dup#2"}


def test_diff_snapshots_omits_inactive_sources():
    rl.RateLimiter(rps=10.0, name="idle")
    busy = rl.RateLimiter(rps=1000.0, name="busy")

    before = rl.snapshot()
    asyncio.run(busy.acquire())
    after = rl.snapshot()

    deltas = rl.diff_snapshots(before, after)
    assert "idle" not in deltas
    assert deltas["busy"][0] == 1


def test_diff_snapshots_handles_a_source_that_did_not_exist_before():
    """A limiter constructed mid-step has no baseline entry. It must diff
    against zero rather than raising a KeyError inside the pipeline."""
    before = rl.snapshot()
    late = rl.RateLimiter(rps=1000.0, name="late")
    asyncio.run(late.acquire())

    deltas = rl.diff_snapshots(before, rl.snapshot())
    assert deltas["late"][0] == 1


def _tracker(db, run):
    db.add(run)
    db.commit()
    return ProgressTracker(run, STEPS, db, start_time=0.0)


def test_step_records_rate_limit_stats_for_sources_it_touched(db_session):
    limiter = rl.RateLimiter(rps=1000.0, name="fec")
    run = PipelineRun(status="running")
    tracker = _tracker(db_session, run)

    tracker.begin("fetch_a")
    asyncio.run(limiter.acquire())
    asyncio.run(limiter.acquire())
    tracker.complete("fetch_a")

    row = db_session.query(PipelineRateLimitStat).one()
    assert row.run_kind == "pipeline_runs"
    assert row.run_id == run.id
    assert row.step_key == "fetch_a"
    assert row.source == "fec"
    assert row.requests == 2


def test_untouched_sources_get_no_row(db_session):
    """Row count should track what a run actually touched, not how many
    fetch modules happen to exist."""
    rl.RateLimiter(rps=1000.0, name="unused")
    used = rl.RateLimiter(rps=1000.0, name="used")
    run = PipelineRun(status="running")
    tracker = _tracker(db_session, run)

    tracker.begin("fetch_a")
    asyncio.run(used.acquire())
    tracker.complete("fetch_a")

    sources = {r.source for r in db_session.query(PipelineRateLimitStat).all()}
    assert sources == {"used"}


def test_activity_is_attributed_to_the_step_it_happened_in(db_session):
    limiter = rl.RateLimiter(rps=1000.0, name="congress")
    run = PipelineRun(status="running")
    tracker = _tracker(db_session, run)

    tracker.begin("fetch_a")
    asyncio.run(limiter.acquire())
    tracker.complete("fetch_a")

    tracker.begin("analyze_a")
    for _ in range(3):
        asyncio.run(limiter.acquire())
    tracker.complete("analyze_a")

    by_step = {r.step_key: r.requests for r in db_session.query(PipelineRateLimitStat).all()}
    assert by_step == {"fetch_a": 1, "analyze_a": 3}


def test_step_never_begun_records_nothing(db_session):
    """Without a baseline there is no window to attribute. Diffing against
    zero would charge the step with every request the process ever made."""
    limiter = rl.RateLimiter(rps=1000.0, name="fec")
    run = PipelineRun(status="running")
    tracker = _tracker(db_session, run)

    asyncio.run(limiter.acquire())
    tracker.complete("fetch_a")  # no begin()

    assert db_session.query(PipelineRateLimitStat).count() == 0


def test_rate_limit_stat_errors_are_swallowed(db_session, monkeypatch):
    """Same posture as phase timings: a diagnostic write must never abort
    a 12-hour pipeline."""
    limiter = rl.RateLimiter(rps=1000.0, name="fec")
    run = PipelineRun(status="running")
    tracker = _tracker(db_session, run)

    tracker.begin("fetch_a")
    asyncio.run(limiter.acquire())

    def _explode(*args, **kwargs):
        raise RuntimeError("snapshot exploded")

    monkeypatch.setattr(rl, "snapshot", _explode)
    tracker.complete("fetch_a")  # must not raise


@pytest.mark.asyncio
async def test_timings_endpoint_reports_blocked_share_of_a_run(db_session):
    """The number the hardware decision turns on: a run that is mostly
    blocked on someone else's rate limit cannot be shortened by faster
    local hardware."""
    from app.api.admin import admin_pipeline_timings
    from app.models import PipelinePhaseTiming

    run = PipelineRun(status="completed")
    db_session.add(run)
    db_session.commit()

    db_session.add(PipelinePhaseTiming(
        run_kind="pipeline_runs", run_id=run.id, step_key="fetch_a",
        phase="fetch", label="Fetch A", status="done", duration_seconds=1000.0,
    ))
    db_session.add(PipelineRateLimitStat(
        run_kind="pipeline_runs", run_id=run.id, step_key="fetch_a",
        source="fec", requests=4210, blocked_seconds=600.0,
    ))
    db_session.add(PipelineRateLimitStat(
        run_kind="pipeline_runs", run_id=run.id, step_key="fetch_a",
        source="congress", requests=900, blocked_seconds=150.0,
    ))
    db_session.commit()

    result = await admin_pipeline_timings(kind="pipeline_runs", runs=10, db=db_session)
    entry = result["runs"][0]

    assert entry["blockedSeconds"] == 750.0
    assert entry["blockedPct"] == 75.0
    # Sources ordered by cost, so the dominant limiter reads first.
    assert [s["source"] for s in entry["rateLimitSources"]] == ["fec", "congress"]
    assert entry["rateLimitSources"][0]["requests"] == 4210
    # Per-step blocked time is carried alongside the step's duration so the
    # two are comparable without a second request.
    assert entry["steps"][0]["blockedSeconds"] == 750.0


@pytest.mark.asyncio
async def test_run_with_no_rate_limit_rows_reports_zero_blocked(db_session):
    from app.api.admin import admin_pipeline_timings
    from app.models import PipelinePhaseTiming

    run = PipelineRun(status="completed")
    db_session.add(run)
    db_session.commit()
    db_session.add(PipelinePhaseTiming(
        run_kind="pipeline_runs", run_id=run.id, step_key="analyze_a",
        phase="analyze", label="Analyze A", status="done", duration_seconds=500.0,
    ))
    db_session.commit()

    result = await admin_pipeline_timings(kind="pipeline_runs", runs=10, db=db_session)
    entry = result["runs"][0]
    assert entry["blockedSeconds"] == 0.0
    assert entry["blockedPct"] == 0.0
    assert entry["rateLimitSources"] == []
