"""app.pipeline_chain: pipelines run one after another, each whatever the
one before it did."""

import asyncio
import inspect
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app import pipeline_chain
from app.pipeline_chain import CRASHED, Link, run_chain


async def _passthrough(run_house):
    """run_house_on_sitting_lines without its lease and lines check."""
    return await run_house()


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
    out rather than let the chain leapfrog into the next heavy pipeline
    beside it, then tries once more — the other run may have been one
    senator, or have failed, so it never stands in for this one."""

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr(pipeline_chain, "WAIT_POLL_S", 0)

    def _busy_for(self, polls, seen):
        answers = iter([True] * polls + [False])

        def busy():
            seen.append("poll")
            return next(answers)
        return busy

    @pytest.mark.parametrize("reason", ["already_running", "held_elsewhere", "member_pipeline_running"])
    def test_it_waits_the_other_run_out_then_tries_again(self, reason):
        polls, order = [], []

        async def first():
            order.append(("A", len(polls)))
            return {"status": "skipped", "reason": reason} if len(order) == 1 else {"status": "completed"}

        async def second():
            order.append(("B", len(polls)))
            return {"status": "completed"}

        outcomes = asyncio.run(run_chain([Link("A", first), Link("B", second)], busy=self._busy_for(2, polls)))
        # A retried, and B started, only once busy() said free (third look).
        assert order == [("A", 0), ("A", 3), ("B", 3)]
        assert outcomes["A"].status == "completed"

    def test_a_retry_that_is_held_off_again_is_reported_as_skipped(self):
        # Not waited on a second time: reported, and the chain moves on.
        held = AsyncMock(return_value={"status": "skipped", "reason": "already_running"})
        polls = []
        outcomes = asyncio.run(run_chain([Link("A", held), Link("B", _completed())],
                                         busy=self._busy_for(0, polls)))
        assert held.await_count == 2 and len(polls) == 1
        assert outcomes["A"].status == "skipped" and outcomes["B"].status == "completed"

    def test_the_chain_is_reported_running_while_it_waits(self, monkeypatch):
        # A wait longer than the staleness cap still has the chain alive.
        seen, now = [], [time.monotonic()]

        def busy():
            now[0] += 7 * 3600  # each look 7h after the last
            seen.append(pipeline_chain.chain_running())
            return len(seen) < 3

        monkeypatch.setattr(pipeline_chain, "time", SimpleNamespace(monotonic=lambda: now[0]))
        asyncio.run(run_chain([Link("A", AsyncMock(side_effect=[
            {"status": "skipped", "reason": "already_running"}, {"status": "completed"}]))], busy=busy))
        assert seen == [True, True, True]

    def test_other_skips_do_not_wait(self):
        busy = []
        asyncio.run(run_chain([Link("A", AsyncMock(return_value={"status": "skipped", "reason": "busy"})),
                               Link("B", _completed())], busy=lambda: busy.append(1) or True))
        assert busy == []


class TestOneChainAtATime:
    def test_a_chain_is_not_started_while_another_runs(self):
        first = pipeline_chain.claim()
        link = AsyncMock()
        assert asyncio.run(run_chain([Link("A", link)])) is None
        link.assert_not_awaited()
        pipeline_chain.release(first)
        assert pipeline_chain.claim() is not None

    def test_a_wedged_chain_does_not_hold_the_slot(self, monkeypatch):
        monkeypatch.setitem(pipeline_chain._chains, 1, time.monotonic() - 13 * 3600)
        assert pipeline_chain.claim() is not None

    def test_a_claimed_chain_runs_under_its_claim_and_releases_it(self):
        chain_id = pipeline_chain.claim()
        asyncio.run(run_chain([Link("A", _completed())], chain_id=chain_id))
        assert not pipeline_chain.chain_running()


class TestTriggers:
    """A full trigger runs the nightly chain's five links, independent of
    each other — the admin one used to run three and the token one two, and
    both stopped at the first skip or failure, so neither could recover
    Stock trades or Election."""

    NAMES = ("run_senate_pipeline", "run_supplementary_pipeline", "run_house_pipeline",
             "run_stock_trades_pipeline", "run_election_pipeline")

    def _start(self, db_session, monkeypatch, senator=None, fetch_only=False):
        from app.api import admin

        started = []
        monkeypatch.setattr(admin, "run_pipeline_in_thread", lambda target, **kw: started.append(target))
        admin.admin_trigger_pipeline(senator=senator, fetch_only=fetch_only, db=db_session)  # a plain def
        return started

    def test_a_full_trigger_runs_every_nightly_link_and_reports_them(self, db_session, monkeypatch):
        (target,) = self._start(db_session, monkeypatch)
        assert pipeline_chain.chain_running()  # claimed before answering
        mocks = {n: _completed() for n in self.NAMES}
        mocks["run_senate_pipeline"] = AsyncMock(return_value={"status": "failed", "error": "boom"})
        with patch.multiple("app.scheduler", **mocks), \
             patch("app.scheduler.pipelines_running", return_value=False), \
             patch("app.pipeline.fetch.district_pvi.run_house_on_sitting_lines", _passthrough), \
             patch("app.services.bill_service.warm_bill_collection_cache"), \
             patch("app.ops_alerts.send_ops_alert") as alert, \
             patch("app.ops_alerts.resolve_ops_alert"):
            asyncio.run(target())
        for mock in mocks.values():
            mock.assert_awaited_once()
        assert alert.call_args.args[0] == "Triggered Senate run failed"
        assert not pipeline_chain.chain_running()

    @pytest.mark.parametrize("senator,fetch_only", [("Smith", False), (None, True)])
    def test_a_filtered_trigger_is_the_senate_pipeline_alone(self, db_session, monkeypatch, senator, fetch_only):
        senate, house = _completed(), AsyncMock()
        with patch("app.pipeline.senate_pipeline.run_senate_pipeline", senate), \
             patch("app.scheduler.run_house_pipeline", house):
            (target,) = self._start(db_session, monkeypatch, senator, fetch_only)
            assert not pipeline_chain.chain_running()  # no chain claimed
            asyncio.run(target())
        senate.assert_awaited_once_with(senator_filter=senator, fetch_only=fetch_only)
        house.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_token_trigger_runs_the_same_chain(self, db_session, monkeypatch):
        from app.api import admin
        from app.api import pipeline as pipeline_api

        started = []
        monkeypatch.setattr(pipeline_api, "check_pipeline_token", lambda _a: None)
        monkeypatch.setattr(admin, "run_pipeline_in_thread", lambda target, **kw: started.append(target))
        pipeline_api.trigger_pipeline(authorization="Bearer x", senator=None, fetch_only=False, db=db_session)
        mocks = {n: _completed() for n in self.NAMES}
        with patch.multiple("app.scheduler", **mocks), \
             patch("app.scheduler.pipelines_running", return_value=False), \
             patch("app.pipeline.fetch.district_pvi.run_house_on_sitting_lines", _passthrough), \
             patch("app.services.bill_service.warm_bill_collection_cache"), \
             patch("app.ops_alerts.resolve_ops_alert"):
            await started[0]()
        for mock in mocks.values():
            mock.assert_awaited_once()

    def test_a_trigger_that_cannot_start_hands_the_slot_back(self, db_session, monkeypatch):
        from app.api import admin
        from app.background import WritesHeld

        def held(*_a, **_k):
            raise WritesHeld("a data reset holds writes")

        monkeypatch.setattr(admin, "run_pipeline_in_thread", held)
        with pytest.raises(WritesHeld):
            asyncio.run(admin.admin_trigger_pipeline(senator=None, fetch_only=False, db=db_session))
        assert not pipeline_chain.chain_running()


TRIGGERS = [
    ("admin_trigger_pipeline", {"senator": None, "fetch_only": False}),
    ("admin_trigger_pipeline", {"senator": "Smith", "fetch_only": False}),
    ("admin_trigger_pipeline", {"senator": None, "fetch_only": True}),
    ("admin_trigger_house_pipeline", {}),
    ("admin_trigger_supplementary_pipeline", {}),
    ("admin_trigger_election_pipeline", {}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint,kwargs", TRIGGERS)
async def test_every_trigger_is_refused_while_a_chain_runs(db_session, monkeypatch, endpoint, kwargs):
    # A full one would run every pipeline a second time; any other would
    # run beside the chain's current link.
    from fastapi import HTTPException

    from app.api import admin

    started = []
    monkeypatch.setattr(admin, "run_pipeline_in_thread", lambda target, **kw: started.append(target))
    if endpoint in ("admin_trigger_pipeline", "admin_trigger_house_pipeline"):
        kwargs = {**kwargs, "db": db_session}
    monkeypatch.setitem(pipeline_chain._chains, 1, time.monotonic())
    with pytest.raises(HTTPException) as error:
        answer = getattr(admin, endpoint)(**kwargs)
        if inspect.isawaitable(answer):
            await answer
    assert error.value.status_code == 409 and not started
    pipeline_chain._chains.clear()
    answer = getattr(admin, endpoint)(**kwargs)
    if inspect.isawaitable(answer):
        await answer
    assert started
    pipeline_chain._chains.clear()  # the full trigger's claim


def test_pipelines_running_counts_live_runs_not_a_dead_runs_row(db_session, monkeypatch):
    # A RUNNING row with no live run behind it (its failure couldn't be
    # committed) would otherwise hold a waiting link for half a day.
    from app import scheduler
    from app.models import HousePipelineRun, PipelineStatus
    from app.time_utils import utcnow

    monkeypatch.setattr(scheduler, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    db_session.add(HousePipelineRun(started_at=utcnow(), status=PipelineStatus.RUNNING))
    db_session.commit()
    assert scheduler.pipelines_running() is False
    monkeypatch.setattr(scheduler, "is_stock_pipeline_running", lambda: True)
    monkeypatch.setattr(scheduler, "stock_pipeline_age", lambda: None)
    assert scheduler.pipelines_running() is True


@pytest.mark.parametrize("state,running", [((1, True, False), True), ((1, True, True), False), ((None, False, False), False)])
def test_pipelines_running_counts_a_senate_row_only_while_its_lease_is_live(db_session, monkeypatch, state, running):
    from app import scheduler

    monkeypatch.setattr(scheduler, "SessionLocal", lambda: db_session)
    with patch("app.pipeline.run_tracker.senate_run_state", return_value=state):
        assert scheduler.pipelines_running() is running


def test_pipelines_running_stops_counting_a_hung_run(monkeypatch):
    # Else a link waiting on it would wait for ever.
    from datetime import timedelta

    from app import scheduler

    monkeypatch.setattr(scheduler, "is_house_pipeline_running", lambda: True)
    monkeypatch.setattr(scheduler, "house_pipeline_age", lambda: timedelta(hours=13))
    with patch("app.pipeline.run_tracker.senate_run_state", return_value=(None, False, False)):
        assert scheduler.pipelines_running() is False
        monkeypatch.setattr(scheduler, "house_pipeline_age", lambda: timedelta(hours=1))
        assert scheduler.pipelines_running() is True
