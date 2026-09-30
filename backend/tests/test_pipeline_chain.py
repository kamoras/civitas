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
    assert after == ["A"]  # its follow-up runs whatever it did


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
             patch("app.services.bill_service.warm_bill_collection_cache"), \
             patch("app.ops_alerts.resolve_ops_alert"):
            await started[0]()
        for mock in mocks.values():
            mock.assert_awaited_once()
