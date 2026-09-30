"""app.pipeline_chain: pipelines run one at a time, each whatever the one
before it did; chains run one at a time, in the order they started."""

import asyncio
import threading
import time
from unittest.mock import AsyncMock, patch

import pytest

from app import pipeline_chain
from app.pipeline_chain import CRASHED, FULL, Link, reserve, run_chain


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(pipeline_chain, "POLL_S", 0.005)
    monkeypatch.setattr(pipeline_chain, "_queue", pipeline_chain._Queue())


def _completed():
    return AsyncMock(return_value={"status": "completed"})


def test_a_crash_is_the_links_outcome_and_the_next_link_runs():
    after = []
    second = _completed()
    outcomes = asyncio.run(run_chain([
        Link("A", AsyncMock(side_effect=RuntimeError("boom")), after=lambda: after.append("A")),
        Link("B", second),
    ]))
    assert outcomes["A"].status == CRASHED and str(outcomes["A"].error) == "boom"
    assert outcomes["B"].status == "completed"
    second.assert_awaited_once()
    assert after == ["A"]  # its follow-up runs whatever it did


def test_skipped_and_failed_links_do_not_stop_the_chain():
    last = _completed()
    outcomes = asyncio.run(run_chain([
        Link("A", AsyncMock(return_value={"status": "skipped", "reason": "busy"})),
        Link("B", AsyncMock(return_value={"status": "failed"})),
        Link("C", last),
    ]))
    assert [o.status for o in outcomes.values()] == ["skipped", "failed", "completed"]
    last.assert_awaited_once()


def test_a_data_reset_ends_the_chain():
    # Every later link would be refused the same way (and alert for it).
    from app.pipeline import lease

    last = AsyncMock()
    outcomes = asyncio.run(run_chain([
        Link("A", AsyncMock(return_value={"status": "skipped", "reason": lease.REFUSED_BY_RESET})),
        Link("B", last),
    ]))
    assert list(outcomes) == ["A"]
    last.assert_not_awaited()


def test_cancellation_still_ends_the_chain_and_frees_the_queue():
    last = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_chain([Link("A", AsyncMock(side_effect=asyncio.CancelledError())), Link("B", last)]))
    last.assert_not_awaited()
    assert not pipeline_chain.chain_running()
    assert asyncio.run(run_chain([Link("C", _completed())]))["C"].status == "completed"


def _recording(order, active, overlaps, label):
    async def run():
        active.append(label)
        order.append(label)
        if len(active) > 1:
            overlaps.append(tuple(active))
        await asyncio.sleep(0.02)
        active.remove(label)
        return {"status": "completed"}

    return run


def test_chains_run_one_at_a_time_in_the_order_they_started():
    # A trigger sent while the nightly chain runs waits for it: two
    # pipelines never run at once.
    order, active, overlaps = [], [], []
    nightly = [Link(f"n{i}", _recording(order, active, overlaps, f"n{i}")) for i in range(3)]
    manual = [Link("m0", _recording(order, active, overlaps, "m0"))]
    first = threading.Thread(target=lambda: asyncio.run(run_chain(nightly)))
    first.start()
    time.sleep(0.01)
    second = threading.Thread(target=lambda: asyncio.run(run_chain(manual)))
    second.start()
    first.join(10)
    second.join(10)
    assert overlaps == [] and order == ["n0", "n1", "n2", "m0"]


def test_a_hung_chain_loses_its_turn(monkeypatch):
    # Waiting on it forever would stall every chain after it.
    queue = pipeline_chain._queue
    hung = queue.join("")
    assert queue.first(hung)
    with queue._lock:
        queue._chains[hung] = ("", time.monotonic() - 13 * 3600)  # no progress for a run's length
    ran = _completed()
    assert asyncio.run(run_chain([Link("A", ran)]))["A"].status == "completed"
    assert not pipeline_chain.chain_running()  # a hung chain isn't reported busy either


def test_a_chain_is_reported_running_while_it_waits_and_between_links():
    # check-and-deploy.sh waits on it: a restart would drop the rest.
    seen = []

    async def second():
        seen.append(pipeline_chain.chain_running())
        return {"status": "completed"}

    assert not pipeline_chain.chain_running()
    asyncio.run(run_chain([Link("A", _completed()), Link("B", second)]))
    assert seen == [True] and not pipeline_chain.chain_running()


def test_reserving_a_full_chain_while_one_runs_is_refused():
    first = reserve(FULL)
    assert first is not None and reserve(FULL) is None
    assert reserve("") is not None  # a single pipeline's chain queues regardless
    pipeline_chain.leave(first)
    assert reserve(FULL) is not None


@pytest.mark.asyncio
async def test_the_status_reports_a_chain_so_deploys_wait_it_out(db_session):
    from app.api.admin import admin_pipeline_status

    assert (await admin_pipeline_status(db=db_session))["pipelineChainIsRunning"] is False
    reserve("")
    assert (await admin_pipeline_status(db=db_session))["pipelineChainIsRunning"] is True


class TestTriggers:
    """A trigger runs the nightly chain's five links, independent of each
    other — it used to run two or three and stop at the first skip or
    failure, so it could never recover Stock trades or Election."""

    NAMES = ("run_senate_pipeline", "run_supplementary_pipeline", "run_house_pipeline",
             "run_stock_trades_pipeline", "run_election_pipeline")

    def test_a_full_trigger_runs_every_nightly_link_and_reports_them(self):
        from app.scheduler import triggered_chain

        mocks = {n: _completed() for n in self.NAMES}
        mocks["run_senate_pipeline"] = AsyncMock(return_value={"status": "failed", "error": "boom"})
        with patch.multiple("app.scheduler", **mocks), \
             patch("app.services.bill_service.warm_bill_collection_cache"), \
             patch("app.ops_alerts.send_ops_alert") as alert, \
             patch("app.ops_alerts.resolve_ops_alert"):
            asyncio.run(triggered_chain(None, False)())
        for mock in mocks.values():
            mock.assert_awaited_once()
        assert alert.call_args.args[0] == "Triggered Senate run failed"

    @pytest.mark.parametrize("senator,fetch_only", [("Smith", False), (None, True)])
    def test_a_filtered_trigger_is_the_senate_pipeline_alone(self, senator, fetch_only):
        from app.scheduler import triggered_chain

        senate, house = _completed(), AsyncMock()
        with patch("app.scheduler.run_senate_pipeline", senate), patch("app.scheduler.run_house_pipeline", house), \
             patch("app.ops_alerts.resolve_ops_alert"):
            asyncio.run(triggered_chain(senator, fetch_only)())
        senate.assert_awaited_once_with(senator_filter=senator, fetch_only=fetch_only)
        house.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_full_trigger_is_refused_while_a_full_chain_runs(self, db_session, monkeypatch):
        # It would queue a second chain behind the first and redo its work.
        from fastapi import HTTPException

        from app.api.admin import admin_trigger_pipeline

        started = []
        monkeypatch.setattr("app.api.pipeline.run_pipeline_in_thread", lambda *a, **k: started.append(1))
        await admin_trigger_pipeline(senator=None, fetch_only=False, db=db_session)
        with pytest.raises(HTTPException) as refused:
            await admin_trigger_pipeline(senator=None, fetch_only=False, db=db_session)
        assert refused.value.status_code == 409 and started == [1]

    @pytest.mark.asyncio
    async def test_a_single_pipeline_chain_does_not_refuse_a_full_trigger(self, db_session, monkeypatch):
        from app.api.admin import admin_trigger_pipeline

        started = []
        reserve("")  # an Election trigger's
        monkeypatch.setattr("app.api.pipeline.run_pipeline_in_thread", lambda *a, **k: started.append(1))
        await admin_trigger_pipeline(senator=None, fetch_only=False, db=db_session)
        assert started == [1]

    @pytest.mark.asyncio
    async def test_a_trigger_that_never_starts_leaves_no_place_in_the_queue(self, db_session, monkeypatch):
        from app.api.admin import admin_trigger_pipeline
        from app.background import WritesHeld

        def held(*_a, **_k):
            raise WritesHeld("a data reset holds writes")

        monkeypatch.setattr("app.api.pipeline.run_pipeline_in_thread", held)
        with pytest.raises(WritesHeld):
            await admin_trigger_pipeline(senator=None, fetch_only=False, db=db_session)
        assert not pipeline_chain.chain_running()

    def test_the_nightly_run_does_not_start_while_a_triggered_full_run_is_in_progress(self):
        # That run is tonight's: the nightly one would redo all of it.
        from app import scheduler

        mocks = {n: _completed() for n in self.NAMES}
        reserve(FULL)
        with patch.multiple("app.scheduler", **mocks), \
             patch("app.background.threading.Thread",
                   lambda target, **_k: type("T", (), {"start": lambda self: target()})()):
            scheduler._nightly_pipeline()
        for mock in mocks.values():
            mock.assert_not_awaited()
