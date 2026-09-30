"""app.pipeline_chain: pipelines run one after another, each whatever the
one before it did."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from app import pipeline_chain
from app.pipeline_chain import CRASHED, Link, run_chain


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
    assert after == ["A"]  # its follow-up runs whatever it did, short of a skip


def test_a_skipped_links_follow_up_is_not_run():
    after = []
    asyncio.run(run_chain([Link("A", AsyncMock(return_value={"status": "skipped", "reason": "busy"}),
                                after=lambda: after.append("A"))]))
    assert after == []


@pytest.mark.parametrize("result", [None, {}, {"reps_processed": 3}])
def test_a_result_without_a_status_is_no_success(result):
    outcomes = asyncio.run(run_chain([Link("A", AsyncMock(return_value=result))]))
    assert outcomes["A"].status == pipeline_chain.UNKNOWN
    assert pipeline_chain.UNKNOWN in pipeline_chain.FAILED


class TestHeldOffByAnotherRun:
    """A link skipped because another run holds the machine waits that run
    out rather than let the chain leapfrog it into the next heavy pipeline
    beside it."""

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr(pipeline_chain, "WAIT_POLL_S", 0)

    def _busy_for(self, polls, seen):
        answers = iter([True] * polls + [False])

        def busy():
            seen.append("poll")
            return next(answers)
        return busy

    @pytest.mark.parametrize("reason", ["already_running", "held_elsewhere"])
    def test_another_run_of_the_same_pipeline_is_waited_out_then_the_chain_moves_on(self, reason):
        polls, order = [], []
        first = AsyncMock(return_value={"status": "skipped", "reason": reason})

        async def second():
            order.append(len(polls))
            return {"status": "completed"}

        outcomes = asyncio.run(run_chain([Link("A", first), Link("B", second)], busy=self._busy_for(2, polls)))
        assert order == [3]  # B started only once busy() said free
        first.assert_awaited_once()  # that run refreshed A's data: not rerun
        assert pipeline_chain.ran_elsewhere(outcomes["A"])

    def test_a_link_held_off_by_a_member_pipeline_is_tried_again(self):
        stock = AsyncMock(side_effect=[{"status": "skipped", "reason": "member_pipeline_running"},
                                       {"status": "completed"}])
        outcomes = asyncio.run(run_chain([Link("Stock", stock)], busy=self._busy_for(1, [])))
        assert stock.await_count == 2 and outcomes["Stock"].status == "completed"

    def test_a_run_holding_it_past_the_stale_timeout_ends_the_chain(self, monkeypatch):
        from datetime import timedelta

        monkeypatch.setattr(pipeline_chain, "STALE_PIPELINE_TIMEOUT", timedelta(0))
        last = AsyncMock()
        outcomes = asyncio.run(run_chain([
            Link("A", AsyncMock(return_value={"status": "skipped", "reason": "already_running"})),
            Link("B", last),
        ], busy=lambda: True))
        assert outcomes["A"].status == pipeline_chain.WEDGED
        assert pipeline_chain.ends_chain(outcomes["A"])
        last.assert_not_awaited()

    def test_other_skips_do_not_wait(self):
        busy = []
        asyncio.run(run_chain([Link("A", AsyncMock(return_value={"status": "skipped", "reason": "busy"})),
                               Link("B", _completed())], busy=lambda: busy.append(1) or True))
        assert busy == []


def test_skipped_and_failed_links_do_not_stop_the_chain():
    last = _completed()
    outcomes = asyncio.run(run_chain([
        Link("A", AsyncMock(return_value={"status": "skipped", "reason": "already_running"})),
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


def test_cancellation_still_ends_the_chain():
    last = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_chain([Link("A", AsyncMock(side_effect=asyncio.CancelledError())), Link("B", last)]))
    last.assert_not_awaited()
    assert not pipeline_chain.chain_running()


def test_a_chain_is_reported_running_between_its_links():
    # check-and-deploy.sh waits on it: a restart would drop the rest.
    seen = []

    async def second():
        seen.append(pipeline_chain.chain_running())
        return {"status": "completed"}

    assert not pipeline_chain.chain_running()
    asyncio.run(run_chain([Link("A", _completed()), Link("B", second)]))
    assert seen == [True] and not pipeline_chain.chain_running()


def test_a_chain_with_no_progress_for_a_runs_length_is_not_busy(monkeypatch):
    # Wedged: reported busy forever, it would hold every deploy off.
    monkeypatch.setitem(pipeline_chain._chains, 1, time.monotonic() - 13 * 3600)
    assert not pipeline_chain.chain_running()


@pytest.mark.asyncio
async def test_the_status_reports_a_chain_so_deploys_wait_it_out(db_session, monkeypatch):
    from app.api.admin import admin_pipeline_status

    assert (await admin_pipeline_status(db=db_session))["pipelineChainIsRunning"] is False
    monkeypatch.setitem(pipeline_chain._chains, 1, time.monotonic())
    assert (await admin_pipeline_status(db=db_session))["pipelineChainIsRunning"] is True


class TestTriggers:
    """A full trigger runs the nightly chain's five links, independent of
    each other — the admin one used to run three and the token one two, and
    both stopped at the first skip or failure, so neither could recover
    Stock trades or Election."""

    NAMES = ("run_senate_pipeline", "run_supplementary_pipeline", "run_house_pipeline",
             "run_stock_trades_pipeline", "run_election_pipeline")

    def test_a_full_trigger_runs_every_nightly_link_and_reports_them(self):
        from app.api.admin import _trigger_target

        mocks = {n: _completed() for n in self.NAMES}
        mocks["run_senate_pipeline"] = AsyncMock(return_value={"status": "failed", "error": "boom"})
        with patch.multiple("app.scheduler", **mocks), \
             patch("app.scheduler.pipelines_running", return_value=False), \
             patch("app.services.bill_service.warm_bill_collection_cache"), \
             patch("app.ops_alerts.send_ops_alert") as alert, \
             patch("app.ops_alerts.resolve_ops_alert"):
            asyncio.run(_trigger_target(None, False)())
        for mock in mocks.values():
            mock.assert_awaited_once()
        assert alert.call_args.args[0] == "Triggered Senate run failed"

    @pytest.mark.parametrize("senator,fetch_only", [("Smith", False), (None, True)])
    def test_a_filtered_trigger_is_the_senate_pipeline_alone(self, senator, fetch_only):
        from app.api.admin import _trigger_target

        senate, house = _completed(), AsyncMock()
        with patch("app.pipeline.senate_pipeline.run_senate_pipeline", senate), \
             patch("app.scheduler.run_house_pipeline", house):
            asyncio.run(_trigger_target(senator, fetch_only)())
        senate.assert_awaited_once_with(senator_filter=senator, fetch_only=fetch_only)
        house.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_token_trigger_runs_the_same_chain(self, db_session, monkeypatch):
        from app.api import pipeline as pipeline_api

        started = []
        monkeypatch.setattr(pipeline_api, "check_pipeline_token", lambda _a: None)
        monkeypatch.setattr(pipeline_api, "run_pipeline_in_thread", lambda target, **kw: started.append(target))
        await pipeline_api.trigger_pipeline(authorization="Bearer x", senator=None, fetch_only=False, db=db_session)
        mocks = {n: _completed() for n in self.NAMES}
        with patch.multiple("app.scheduler", **mocks), \
             patch("app.scheduler.pipelines_running", return_value=False), \
             patch("app.services.bill_service.warm_bill_collection_cache"), \
             patch("app.ops_alerts.resolve_ops_alert"):
            await started[0]()
        for mock in mocks.values():
            mock.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("senator,fetch_only,refused", [(None, False, True), ("Smith", False, False), (None, True, False)])
async def test_a_full_trigger_is_refused_while_a_chain_runs(db_session, monkeypatch, senator, fetch_only, refused):
    # It would run every pipeline a second time behind the first.
    from fastapi import HTTPException

    from app.api import admin

    started = []
    monkeypatch.setattr(admin, "run_pipeline_in_thread", lambda target, **kw: started.append(target))
    monkeypatch.setitem(pipeline_chain._chains, 1, time.monotonic())
    if refused:
        with pytest.raises(HTTPException) as error:
            await admin.admin_trigger_pipeline(senator=senator, fetch_only=fetch_only, db=db_session)
        assert error.value.status_code == 409 and not started
    else:
        await admin.admin_trigger_pipeline(senator=senator, fetch_only=fetch_only, db=db_session)
        assert started


def test_pipelines_running_reads_each_pipelines_flag_and_run_rows(db_session, monkeypatch):
    from app import scheduler

    monkeypatch.setattr(scheduler, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    assert scheduler.pipelines_running() is False
    monkeypatch.setattr(scheduler, "is_stock_pipeline_running", lambda: True)
    assert scheduler.pipelines_running() is True
