"""app.pipeline_chain: pipelines run one at a time, each whatever the one
before it did."""

import asyncio
import threading
import time
from unittest.mock import AsyncMock, patch

import pytest

from app import pipeline_chain
from app.pipeline_chain import CRASHED, RAN_ELSEWHERE, Link, one_link, run_chain


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(pipeline_chain, "_running_elsewhere", lambda model: False)
    monkeypatch.setattr(pipeline_chain, "POLL_S", 0.005)
    monkeypatch.setattr(pipeline_chain, "ELSEWHERE_POLL_S", 0.005)
    monkeypatch.setattr(pipeline_chain, "_turns", pipeline_chain._Turns())
    monkeypatch.setattr(pipeline_chain, "_completed", {})
    monkeypatch.setattr(pipeline_chain, "_chains", {})


def _link(label, run, after=None, whole=True):
    return Link(label, run, model=object, after=after, whole=whole)


def _completed():
    return AsyncMock(return_value={"status": "completed"})


def test_a_crash_is_the_links_outcome_and_the_next_link_runs():
    after = []
    second = _completed()
    outcomes = asyncio.run(run_chain([
        _link("A", AsyncMock(side_effect=RuntimeError("boom")), after=lambda: after.append("A")),
        _link("B", second),
    ]))
    assert outcomes["A"].status == CRASHED and str(outcomes["A"].error) == "boom"
    assert outcomes["B"].status == "completed"
    second.assert_awaited_once()
    assert after == ["A"]  # its follow-up runs whatever it did


def test_skipped_and_failed_links_do_not_stop_the_chain():
    last = _completed()
    outcomes = asyncio.run(run_chain([
        _link("A", AsyncMock(return_value={"status": "skipped", "reason": "busy"})),
        _link("B", AsyncMock(return_value={"status": "failed"})),
        _link("C", last),
    ]))
    assert [o.status for o in outcomes.values()] == ["skipped", "failed", "completed"]
    last.assert_awaited_once()


def test_cancellation_still_ends_the_chain_and_frees_the_queue():
    last = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_chain([_link("A", AsyncMock(side_effect=asyncio.CancelledError())), _link("B", last)]))
    last.assert_not_awaited()
    assert not pipeline_chain._turns.held()
    assert asyncio.run(run_chain([_link("C", _completed())]))["C"].status == "completed"


def test_a_run_outside_any_chain_is_waited_out_not_repeated(monkeypatch):
    looks = iter([True, True, False])
    monkeypatch.setattr(pipeline_chain, "_running_elsewhere", lambda model: next(looks))
    run, after = AsyncMock(), []
    outcomes = asyncio.run(run_chain([_link("A", run, after=lambda: after.append(1))]))
    assert outcomes["A"].status == RAN_ELSEWHERE
    run.assert_not_awaited()
    assert after == [1]  # the other run may have changed what it follows up


def test_a_database_error_while_waiting_is_not_the_end_of_the_chain(monkeypatch):
    looks = iter([True])

    def flaky(model):
        try:
            return next(looks)
        except StopIteration:
            raise RuntimeError("database is locked") from None

    monkeypatch.setattr(pipeline_chain, "_running_elsewhere", flaky)
    run, last = _completed(), _completed()
    asyncio.run(run_chain([_link("A", run), _link("B", last)]))
    last.assert_awaited_once()


def _recording(active, overlaps, order, label, seconds=0.03):
    async def run():
        active.append(label)
        order.append(label)
        if len(active) > 1:
            overlaps.append(tuple(active))
        await asyncio.sleep(seconds)
        active.remove(label)
        return {"status": "completed"}

    return run


def test_two_chains_take_turns_one_pipeline_at_a_time():
    # A manual run and the nightly one interleave: neither runs two
    # pipelines at once, and neither starves behind the other.
    active, overlaps, order = [], [], []
    chains = {
        name: [_link(f"{name}{i}", _recording(active, overlaps, order, f"{name}{i}")) for i in range(3)]
        for name in ("n", "m")
    }
    threads = [threading.Thread(target=lambda c=c: asyncio.run(run_chain(c))) for c in chains.values()]
    threads[0].start()
    time.sleep(0.01)
    threads[1].start()
    for t in threads:
        t.join(10)
    assert overlaps == []
    assert order.index("m0") < order.index("n2")  # interleaved, not one chain then the other


def test_a_pipeline_another_chain_just_ran_is_not_run_again():
    # A House trigger queued behind the nightly Senate runs House; the
    # nightly chain, reaching House afterwards, doesn't repeat it.
    active, overlaps, order = [], [], []
    nightly = [_link("Senate", _recording(active, overlaps, order, "night-senate", 0.05)),
               _link("House", _recording(active, overlaps, order, "night-house"))]
    manual = [_link("House", _recording(active, overlaps, order, "manual-house"))]
    results = {}
    t1 = threading.Thread(target=lambda: results.setdefault("n", asyncio.run(run_chain(nightly))))
    t2 = threading.Thread(target=lambda: results.setdefault("m", asyncio.run(run_chain(manual))))
    t1.start()
    time.sleep(0.01)
    t2.start()
    t1.join(10)
    t2.join(10)
    assert order == ["night-senate", "manual-house"]
    assert results["n"]["House"].status == RAN_ELSEWHERE


@pytest.mark.parametrize("status", ["skipped", "failed"])
def test_a_run_that_did_not_complete_is_not_one_another_chain_skips(status):
    # A trigger's House that failed or was refused hasn't done tonight's
    # work: the nightly chain still runs it.
    asyncio.run(run_chain([_link("House", AsyncMock(return_value={"status": status}))]))
    again = _completed()
    asyncio.run(run_chain([_link("House", again)]))
    again.assert_awaited_once()


def test_a_crashed_run_is_not_one_another_chain_skips():
    asyncio.run(run_chain([_link("House", AsyncMock(side_effect=RuntimeError("boom")))]))
    again = _completed()
    asyncio.run(run_chain([_link("House", again)]))
    again.assert_awaited_once()


def test_a_run_another_chain_began_earlier_and_completed_since_is_not_repeated():
    # A full trigger's Senate started before the nightly chain and finished
    # after it began: the nightly Senate doesn't run it back to back.
    import datetime as dt

    from app.time_utils import utcnow

    pipeline_chain._completed["Senate"] = utcnow() + dt.timedelta(seconds=1)
    run = _completed()
    assert asyncio.run(run_chain([_link("Senate", run)]))["Senate"].status == RAN_ELSEWHERE
    run.assert_not_awaited()


def test_a_partial_run_is_not_one_another_chain_skips():
    # A single-senator run isn't the Senate pipeline's whole run.
    first = asyncio.run(run_chain([_link("Senate", _completed(), whole=False)]))
    assert first["Senate"].status == "completed"
    whole = _completed()
    asyncio.run(run_chain([_link("Senate", whole)]))
    whole.assert_awaited_once()


def test_a_hung_turn_is_taken_over_and_the_queue_goes_on_serializing():
    # Waiting on it forever would stall every later link; letting every
    # waiter past it would run them all at once.
    turns = pipeline_chain._turns
    hung = object()
    turns.join(hung)
    assert turns.try_take(hung, 60) == "held"  # a hung run's turn, never released
    time.sleep(0.06)
    first, second = object(), object()
    turns.join(first)
    turns.join(second)
    assert turns.try_take(first, 0.05) == "taken"
    assert turns.try_take(second, 0.05) is None  # waits for `first`, not past it
    turns.release(hung)  # the hung run's late release frees nothing
    assert turns.try_take(second, 60) is None
    turns.release(first)
    assert turns.try_take(second, 60) == "held"


def test_a_chain_is_reported_running_between_its_links():
    # check-and-deploy.sh waits on it: a restart would drop the rest.
    seen = []

    async def second():
        seen.append(pipeline_chain.chain_running())
        return {"status": "completed"}

    assert not pipeline_chain.chain_running()
    asyncio.run(run_chain([_link("A", _completed()), _link("B", second)]))
    assert seen == [True] and not pipeline_chain.chain_running()


def test_one_link_is_a_chain_of_one():
    run = _completed()
    asyncio.run(one_link(_link("A", run))())
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_status_reports_a_chain_so_deploys_wait_it_out(db_session, monkeypatch):
    from app.api.admin import admin_pipeline_status

    assert (await admin_pipeline_status(db=db_session))["pipelineChainIsRunning"] is False
    monkeypatch.setitem(pipeline_chain._chains, 99, ("", time.monotonic()))
    assert (await admin_pipeline_status(db=db_session))["pipelineChainIsRunning"] is True


def test_a_chain_with_no_progress_for_a_runs_length_is_not_busy(monkeypatch):
    # Wedged: reported busy forever, it would hold every deploy off.
    monkeypatch.setitem(pipeline_chain._chains, 1, ("", time.monotonic() - 13 * 3600))
    assert not pipeline_chain.chain_running()


class TestTriggers:
    """A trigger runs the nightly chain's five links, independent of each
    other — it used to run two or three and stop at the first skip or
    failure, so it could never recover Stock trades or Election."""

    def test_a_full_trigger_runs_every_nightly_link(self):
        from app.scheduler import triggered_chain

        names = ("run_senate_pipeline", "run_supplementary_pipeline", "run_house_pipeline",
                 "run_stock_trades_pipeline", "run_election_pipeline")
        mocks = {n: _completed() for n in names}
        mocks["run_senate_pipeline"] = AsyncMock(return_value={"status": "failed"})
        with patch.multiple("app.scheduler", **mocks), \
             patch("app.services.bill_service.warm_bill_collection_cache"):
            asyncio.run(triggered_chain(None, False)())
        for mock in mocks.values():
            mock.assert_awaited_once()

    @pytest.mark.parametrize("senator,fetch_only", [("Smith", False), (None, True)])
    def test_a_filtered_trigger_is_the_senate_pipeline_alone(self, senator, fetch_only):
        from app.scheduler import triggered_chain

        senate, house = _completed(), AsyncMock()
        with patch("app.scheduler.run_senate_pipeline", senate), patch("app.scheduler.run_house_pipeline", house):
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
        monkeypatch.setitem(pipeline_chain._chains, 5, ("", time.monotonic()))  # an Election trigger's
        monkeypatch.setattr("app.api.pipeline.run_pipeline_in_thread", lambda *a, **k: started.append(1))
        await admin_trigger_pipeline(senator=None, fetch_only=False, db=db_session)
        assert started == [1]

    @pytest.mark.asyncio
    async def test_a_trigger_that_never_starts_leaves_no_registration(self, db_session, monkeypatch):
        from app.api.admin import admin_trigger_pipeline
        from app.background import WritesHeld

        def held(*_a, **_k):
            raise WritesHeld("a data reset holds writes")

        monkeypatch.setattr("app.api.pipeline.run_pipeline_in_thread", held)
        with pytest.raises(WritesHeld):
            await admin_trigger_pipeline(senator=None, fetch_only=False, db=db_session)
        assert not pipeline_chain.chain_running()
