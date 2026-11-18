"""The election run's orchestration (election_pipeline.run_election_pipeline)
as a whole: every phase stubbed with a recorder, so a test pins the order
the phases run in and what each step records on the run row. The phases
themselves are tested in their own files; test_election_pipeline.py covers
the lock, a held election, the leases and the crawl's edges."""

import inspect
import json
from contextlib import asynccontextmanager
from datetime import datetime

import pytest

from app.models import ElectionPipelineRun, PipelineStatus
from app.pipeline import election_pipeline as ep
from app.pipeline import lease

NOW = datetime(2026, 9, 30, 3, 0)  # a campaign night: no election held


@pytest.fixture()
def night(db_session, monkeypatch, freeze_utcnow):
    """Every phase answers from a recorder; `calls` is the order they ran in."""
    freeze_utcnow(NOW)
    calls: list[str] = []
    monkeypatch.setattr(ep, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    @asynccontextmanager
    async def granted(tier, **_kw):
        yield lease.Granted(None)

    monkeypatch.setattr(lease, "job_async", granted)

    def stub(name, value=None, target=ep, raises=None):
        def record(*args, **kwargs):
            calls.append(name)
            if raises:
                raise raises
            return value

        async def async_record(*args, **kwargs):
            return record()

        original = getattr(target, name)
        monkeypatch.setattr(target, name, async_record if inspect.iscoroutinefunction(original) else record)

    stub("refresh_senate_classes", False, target=ep.senate_classes)
    stub("refresh_special_elections", {"new": ["a", "b"], "problems": []})
    stub("publish_new_specials")
    stub("report_file_problems")
    stub("fetch_all_candidates", [])
    stub("_sync_roster", 7)
    stub("fetch_bioguide_to_fec_ids", {})
    stub("link_members", 3)
    stub("_refresh_financials", 5)
    stub("crawl_for_new_sources", {"NM": "adopted results", "WY": "none"})
    stub("_confirmed_candidates_phase", "12 confirmed")
    stub("_sync_ballot_measures", {"synced": 4, "failed_states": 1})
    stub("refresh_link_verification", {"failed": 0}, target=ep.ballot_lookup)
    stub("ingest_race_coverage", 6, target=ep.election_coverage)
    stub("calibrate_and_store", None, target=ep.race_relevance)
    stub("counting_is_live", False, target=ep.live_bluesky)
    stub("post_race_coverage_updates", 2, target=ep.election_bluesky)
    stub("recheck_stored_captions", None, target=ep.coverage_captions)
    stub("_snapshot_candidates", 9)
    stub("_prune_stale_coverage")
    return db_session, calls, stub


def _steps(db) -> list[tuple[str, str, str | None]]:
    run = db.query(ElectionPipelineRun).one()
    return [(s["key"], s["status"], s.get("detail")) for s in json.loads(run.progress_detail)]


async def test_a_night_runs_every_phase_in_order_and_records_each(night):
    db, calls, _ = night
    result = await ep.run_election_pipeline(2026)
    assert result["status"] == PipelineStatus.COMPLETED
    assert (result["candidates_synced"], result["financials_refreshed"], result["coverage_items_ingested"]) == (7, 5, 6)
    assert calls == [
        "refresh_senate_classes", "refresh_special_elections", "publish_new_specials", "report_file_problems",
        "fetch_all_candidates", "fetch_all_candidates", "_sync_roster",
        "fetch_bioguide_to_fec_ids", "link_members",
        "_refresh_financials",
        "crawl_for_new_sources", "_confirmed_candidates_phase",
        "_sync_ballot_measures",
        "refresh_link_verification",
        "ingest_race_coverage", "calibrate_and_store", "counting_is_live", "post_race_coverage_updates",
        "recheck_stored_captions",
        "_snapshot_candidates", "_prune_stale_coverage",
    ]
    assert _steps(db) == [
        ("special_elections", "done", "2 new calendar rows"),
        ("roster_sync", "done", "7 candidates"),
        ("financial_refresh", "done", "5 refreshed"),
        ("confirmed_candidates", "done", "12 confirmed; crawl: NM adopted results"),
        ("ballot_measures", "done", "4 measures, 1 states failed"),
        ("coverage_ingestion", "done", "6 items"),
        ("bluesky_posting", "done", "2 posted"),
        ("snapshot", "done", "9 snapshotted"),
    ]


async def test_posting_stands_down_while_the_live_count_posts(night):
    db, calls, stub = night
    stub("counting_is_live", True, target=ep.live_bluesky)
    await ep.run_election_pipeline(2026)
    assert "post_race_coverage_updates" not in calls
    assert ("bluesky_posting", "done", "stood down: the live count is posting") in _steps(db)


async def test_posting_past_the_coverage_deadline_waits_for_the_next_run(night, monkeypatch):
    db, calls, _ = night
    monkeypatch.setattr(lease, "deadline", lambda tier: float("-inf"))
    await ep.run_election_pipeline(2026)
    assert "calibrate_and_store" not in calls and "post_race_coverage_updates" not in calls
    assert ("bluesky_posting", "skipped", "skipped: reached its deadline") in _steps(db)
    # The phases after it still run.
    assert ("snapshot", "done", "9 snapshotted") in _steps(db)


async def test_a_phase_that_raises_is_failed_and_the_rest_run(night):
    db, calls, stub = night
    stub("_refresh_financials", raises=RuntimeError("FEC mocked off"))
    result = await ep.run_election_pipeline(2026)
    assert result["status"] == PipelineStatus.COMPLETED
    steps = _steps(db)
    assert steps[2][:2] == ("financial_refresh", "failed")
    assert [s[1] for s in steps[3:]] == ["done"] * 5
    assert "_snapshot_candidates" in calls
