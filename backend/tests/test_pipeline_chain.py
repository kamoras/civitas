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


def test_a_chain_waiting_its_turn_holds_no_data_reset_off():
    # It registers as a writer only while a link runs: a reset taken while
    # it waits refuses its next link, and the chain ends.
    from app import background

    run = AsyncMock()
    with background.exclusive("data reset"):
        outcomes = asyncio.run(run_chain([Link("A", run), Link("B", run)]))
    run.assert_not_awaited()
    assert list(outcomes) == ["A"] and outcomes["A"].status == "skipped"


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


def _age(chain_id, hours):
    queue = pipeline_chain._queue
    with queue._lock:
        queue._chains[chain_id].progress = time.monotonic() - hours * 3600


def test_the_holder_hung_past_a_runs_length_loses_the_turn():
    # Waiting on it forever would stall every chain after it.
    queue = pipeline_chain._queue
    hung, _ = queue.join("", ["X"])
    assert queue.turn(hung)
    _age(hung, 13)
    ran = _completed()
    assert asyncio.run(run_chain([Link("A", ran)]))["A"].status == "completed"


def test_a_chain_waiting_that_long_is_alive_not_hung():
    # Behind a 13-hour nightly chain, a queued trigger keeps its place.
    queue = pipeline_chain._queue
    holder, _ = queue.join("", ["X"])
    waiter, _ = queue.join("", ["Y"])
    later, _ = queue.join("", ["Z"])
    assert queue.turn(holder)
    _age(waiter, 13)
    assert not queue.turn(waiter)  # asking is progress
    queue.leave(holder)
    assert not queue.turn(later)  # the waiter is next, not dropped
    assert queue.turn(waiter)


def test_a_hung_holder_that_comes_back_queues_again_before_its_next_link():
    # Running on beside the chain that took its turn would put two
    # pipelines in memory at once.
    queue = pipeline_chain._queue
    slow, _ = queue.join("", ["A", "B"])
    assert queue.turn(slow)
    _age(slow, 13)
    other, _ = queue.join("", ["C"])
    assert queue.turn(other)  # took the turn from the hung holder
    assert not queue.turn(slow)  # back from its long link: behind `other` now
    queue.leave(other)
    assert queue.turn(slow)


def test_a_chain_is_reported_running_while_it_waits_and_between_links():
    # check-and-deploy.sh waits on it: a restart would drop the rest.
    seen = []

    async def second():
        seen.append(pipeline_chain.chain_running())
        return {"status": "completed"}

    assert not pipeline_chain.chain_running()
    asyncio.run(run_chain([Link("A", _completed()), Link("B", second)]))
    assert seen == [True] and not pipeline_chain.chain_running()


def test_a_trigger_that_would_repeat_what_a_live_chain_will_do_is_refused():
    full, _ = reserve(FULL, ["Senate", "House"])
    assert full is not None
    assert reserve(FULL, ["Senate", "House"])[0] is None  # a full run during one
    assert reserve("", ["House"])[0] is None  # still due in the full run
    pipeline_chain._queue.starting(full, "House")
    house, _ = reserve("", ["House"])  # started: a rerun after it is a new run
    assert house is not None
    assert reserve("", ["Senate (single senator X)"])[0] is not None
    pipeline_chain.leave(full)
    assert reserve(FULL, ["Senate", "House"])[0] is None  # the queued House is still due
    pipeline_chain.leave(house)
    assert reserve(FULL, ["Senate", "House"])[0] is not None


@pytest.mark.asyncio
async def test_the_status_reports_a_chain_so_deploys_wait_it_out(db_session):
    from app.api.admin import admin_pipeline_status

    assert (await admin_pipeline_status(db=db_session))["pipelineChainIsRunning"] is False
    reserve("", ["X"])
    assert (await admin_pipeline_status(db=db_session))["pipelineChainIsRunning"] is True


class TestTriggers:
    """A trigger runs the nightly chain's five links, independent of each
    other — it used to run two or three and stop at the first skip or
    failure, so it could never recover Stock trades or Election."""

    NAMES = ("run_senate_pipeline", "run_supplementary_pipeline", "run_house_pipeline",
             "run_stock_trades_pipeline", "run_election_pipeline")

    @pytest.fixture()
    def started(self, monkeypatch):
        """Threads the triggers start, run here and now."""
        ran = []

        def start(target, *, name):
            ran.append(name)
            target()

        monkeypatch.setattr("app.background.start_waiting_writer", start)
        return ran

    def test_a_full_trigger_runs_every_nightly_link_and_reports_them(self, started):
        from app.api.pipeline import start_triggered_chain

        mocks = {n: _completed() for n in self.NAMES}
        mocks["run_senate_pipeline"] = AsyncMock(return_value={"status": "failed", "error": "boom"})
        with patch.multiple("app.scheduler", **mocks), \
             patch("app.services.bill_service.warm_bill_collection_cache"), \
             patch("app.ops_alerts.send_ops_alert") as alert, \
             patch("app.ops_alerts.resolve_ops_alert"):
            assert start_triggered_chain(None, False, "failed") is False  # started, not queued
        for mock in mocks.values():
            mock.assert_awaited_once()
        assert alert.call_args.args[0] == "Triggered Senate run failed"

    @pytest.mark.parametrize("senator,fetch_only,label", [
        ("Smith", False, "Senate (single senator Smith)"), (None, True, "Senate (fetch only)"),
    ])
    def test_a_filtered_trigger_is_that_senate_run_alone_labelled_apart(self, senator, fetch_only, label, started):
        # Labelled apart: its success mustn't clear the full run's alerts.
        from app.api.pipeline import start_triggered_chain

        senate, house = _completed(), AsyncMock()
        with patch("app.scheduler.run_senate_pipeline", senate), patch("app.scheduler.run_house_pipeline", house), \
             patch("app.ops_alerts.resolve_ops_alert") as resolve:
            start_triggered_chain(senator, fetch_only, "failed")
        senate.assert_awaited_once_with(senator_filter=senator, fetch_only=fetch_only)
        house.assert_not_awaited()
        resolved = {c.args[0] for c in resolve.call_args_list}
        assert "nightly-crashed-senate" not in resolved
        assert f"nightly-crashed-{label.lower().replace(' ', '-')}" in resolved

    @pytest.mark.asyncio
    async def test_a_full_trigger_is_refused_while_a_full_chain_runs(self, db_session):
        from fastapi import HTTPException

        from app.api.admin import admin_trigger_pipeline

        reserve(FULL, ["Senate"])
        with pytest.raises(HTTPException) as refused:
            await admin_trigger_pipeline(senator=None, fetch_only=False, db=db_session)
        assert refused.value.status_code == 409

    @pytest.mark.asyncio
    async def test_a_trigger_behind_a_chain_says_it_is_queued(self, monkeypatch):
        from app.api.admin import admin_trigger_house_pipeline

        monkeypatch.setattr("app.background.start_waiting_writer", lambda target, *, name: None)
        reserve("", ["Election"])  # an Election trigger's chain, running
        answer = await admin_trigger_house_pipeline()
        assert answer["queued"] is True and "queued" in answer["message"]

    @pytest.mark.asyncio
    async def test_a_second_house_trigger_while_one_is_due_is_refused(self, monkeypatch):
        from fastapi import HTTPException

        from app.api.admin import admin_trigger_house_pipeline

        monkeypatch.setattr("app.background.start_waiting_writer", lambda target, *, name: None)
        await admin_trigger_house_pipeline()
        with pytest.raises(HTTPException) as refused:
            await admin_trigger_house_pipeline()
        assert refused.value.status_code == 409

    @pytest.mark.asyncio
    async def test_a_trigger_that_never_starts_leaves_no_place_in_the_queue(self, db_session, monkeypatch):
        from app.api.admin import admin_trigger_pipeline
        from app.background import WritesHeld

        def held(*_a, **_k):
            raise WritesHeld("a data reset holds writes")

        monkeypatch.setattr("app.background.start_waiting_writer", held)
        with pytest.raises(WritesHeld):
            await admin_trigger_pipeline(senator=None, fetch_only=False, db=db_session)
        assert not pipeline_chain.chain_running()

    def test_the_nightly_run_does_not_start_while_a_triggered_full_run_is_in_progress(self):
        # That run is tonight's: the nightly one would redo all of it.
        from app import scheduler

        mocks = {n: _completed() for n in self.NAMES}
        reserve(FULL, ["Senate"])
        with patch.multiple("app.scheduler", **mocks), \
             patch("app.ops_alerts.check_current_congress_staleness") as congress, \
             patch("app.ops_alerts.check_feedback_token_expiration"), \
             patch("app.ops_alerts.check_state_pvi_staleness"), \
             patch("app.scheduler.start_waiting_writer", lambda target, *, name: target()):
            scheduler._nightly_pipeline()
        for mock in mocks.values():
            mock.assert_not_awaited()
        congress.assert_called_once()  # its checks still run
