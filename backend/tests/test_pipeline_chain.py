"""app.pipeline_chain: pipelines run one at a time, each whatever the one
before it did."""

import asyncio
import threading
import time
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app import pipeline_chain
from app.pipeline_chain import CRASHED, RAN_ELSEWHERE, Link, run_chain


@pytest.fixture(autouse=True)
def _nothing_elsewhere(monkeypatch):
    monkeypatch.setattr(pipeline_chain, "_running_elsewhere", lambda model: False)
    monkeypatch.setattr(pipeline_chain, "POLL_S", 0.01)


def _link(label, run, after=None):
    return Link(label, run, model=object, after=after)


def test_a_crash_is_the_links_outcome_and_the_next_link_runs():
    after = []
    second = AsyncMock(return_value={"status": "completed"})
    outcomes = asyncio.run(run_chain([
        _link("A", AsyncMock(side_effect=RuntimeError("boom")), after=lambda: after.append("A")),
        _link("B", second),
    ]))
    assert outcomes["A"].status == CRASHED and str(outcomes["A"].error) == "boom"
    assert outcomes["B"].status == "completed"
    second.assert_awaited_once()
    assert after == ["A"]  # its follow-up runs whatever it did


def test_skipped_and_failed_links_do_not_stop_the_chain():
    last = AsyncMock(return_value={"status": "completed"})
    outcomes = asyncio.run(run_chain([
        _link("A", AsyncMock(return_value={"status": "skipped", "reason": "busy"})),
        _link("B", AsyncMock(return_value={"status": "failed"})),
        _link("C", last),
    ]))
    assert [o.status for o in outcomes.values()] == ["skipped", "failed", "completed"]
    last.assert_awaited_once()


def test_cancellation_still_ends_the_chain():
    last = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_chain([_link("A", AsyncMock(side_effect=asyncio.CancelledError())), _link("B", last)]))
    last.assert_not_awaited()
    assert not pipeline_chain._link_lock.locked()


def test_a_pipeline_running_elsewhere_is_waited_out_not_run_again(monkeypatch):
    looks = iter([True, True, False])
    monkeypatch.setattr(pipeline_chain, "_running_elsewhere", lambda model: next(looks))
    run = AsyncMock()
    outcomes = asyncio.run(run_chain([_link("A", run)]))
    assert outcomes["A"].status == RAN_ELSEWHERE
    run.assert_not_awaited()


def test_two_chains_never_run_two_pipelines_at_once():
    # A manual run and the nightly one interleave, one link at a time.
    active, overlaps = [], []

    def link(label):
        async def run():
            active.append(label)
            if len(active) > 1:
                overlaps.append(tuple(active))
            await asyncio.sleep(0.03)
            active.remove(label)
            return {"status": "completed"}

        return _link(label, run)

    threads = [
        threading.Thread(target=lambda n=n: asyncio.run(run_chain([link(f"{n}{i}") for i in range(3)])))
        for n in ("nightly", "manual")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert overlaps == []


def test_a_hung_pipeline_holds_the_next_off_only_so_long(monkeypatch):
    # Waiting on it forever would stall every later link.
    monkeypatch.setattr(pipeline_chain, "STALE_PIPELINE_TIMEOUT", timedelta(seconds=0.05))
    ran = AsyncMock(return_value={"status": "completed"})
    pipeline_chain._link_lock.acquire()  # a hung run's
    try:
        started = time.monotonic()
        outcomes = asyncio.run(run_chain([_link("A", ran)]))
    finally:
        pipeline_chain._link_lock.release()
    assert outcomes["A"].status == "completed" and time.monotonic() - started < 5


def test_an_unreadable_elsewhere_check_runs_the_link(monkeypatch):
    def unreadable(model):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(pipeline_chain, "_running_elsewhere", unreadable)
    run = AsyncMock(return_value={"status": "completed"})
    assert asyncio.run(run_chain([_link("A", run)]))["A"].status == "completed"


class TestTriggers:
    """A trigger runs the nightly chain's five links, independent of each
    other — it used to run three and stop at the first skip or failure, so
    it could never recover Stock trades or Election."""

    def test_a_full_trigger_runs_every_nightly_link(self):
        from app.api.admin import _triggered_chain

        names = ("run_senate_pipeline", "run_supplementary_pipeline", "run_house_pipeline",
                 "run_stock_trades_pipeline", "run_election_pipeline")
        mocks = {n: AsyncMock(return_value={"status": "completed"}) for n in names}
        mocks["run_senate_pipeline"] = AsyncMock(return_value={"status": "failed"})
        with patch.multiple("app.scheduler", **mocks), \
             patch("app.services.bill_service.warm_bill_collection_cache"):
            asyncio.run(_triggered_chain(None, False)())
        for mock in mocks.values():
            mock.assert_awaited_once()

    @pytest.mark.parametrize("senator,fetch_only", [("Smith", False), (None, True)])
    def test_a_filtered_trigger_is_the_senate_pipeline_alone(self, senator, fetch_only):
        from app.api.admin import _triggered_chain

        senate = AsyncMock(return_value={"status": "completed"})
        house = AsyncMock()
        with patch("app.pipeline.senate_pipeline.run_senate_pipeline", senate), \
             patch("app.scheduler.run_house_pipeline", house):
            asyncio.run(_triggered_chain(senator, fetch_only)())
        senate.assert_awaited_once_with(senator_filter=senator, fetch_only=fetch_only)
        house.assert_not_awaited()


def test_a_chain_is_reported_running_between_its_links(monkeypatch):
    # check-and-deploy.sh waits on it: a restart would drop the rest.
    seen = []

    async def first():
        return {"status": "completed"}

    async def second():
        seen.append(pipeline_chain.chain_running())
        return {"status": "completed"}

    assert not pipeline_chain.chain_running()
    asyncio.run(run_chain([_link("A", first), _link("B", second)]))
    assert seen == [True] and not pipeline_chain.chain_running()


@pytest.mark.asyncio
async def test_the_status_reports_a_chain_so_deploys_wait_it_out(db_session, monkeypatch):
    from app.api.admin import admin_pipeline_status

    assert (await admin_pipeline_status(db=db_session))["pipelineChainIsRunning"] is False
    monkeypatch.setattr(pipeline_chain, "_chains", 1)
    assert (await admin_pipeline_status(db=db_session))["pipelineChainIsRunning"] is True
