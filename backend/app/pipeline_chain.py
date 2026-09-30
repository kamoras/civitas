"""Running pipelines one after another, each independent of the last.

The nightly run is five pipelines in order — Senate, Supplementary, House,
Stock trades, Election — and the admin triggers run some of them. They run
one at a time because the Pi can't hold two in memory at once, not because
one needs another's output from the same night: each reads whatever the
database holds, and yesterday's data is a fine input when today's run of
the one before it didn't happen. So a link that is skipped, fails or
crashes is reported and the next link runs anyway. (Until 2026-09 any of
those ended the chain there: a Senate crash left Supplementary, House,
Stock trades and Election a day stale for a reason unrelated to any of
them.)

Every chain in the process shares one lock, held for each link: a manual
run and the nightly run interleave rather than run two pipelines at once.
A link whose pipeline is already running somewhere else (a manual trigger
of it, another chain's) isn't run a second time: the chain waits for that
run to end and moves on. Neither wait lasts past STALE_PIPELINE_TIMEOUT —
a run that old is hung (its own alerts say so), and waiting on it forever
would stall every later link, which is what this module exists to stop.

Pipelines run only in the pipeline process (settings.PROCESS_ROLE), which
is always one process, so a process-local lock serializes all of them.
"""

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.pipeline.run_tracker import STALE_PIPELINE_TIMEOUT

logger = logging.getLogger(__name__)

# Held while a link runs, by whichever chain runs it.
_link_lock = threading.Lock()
# Chains in progress in this process, waits between links included: what
# check-and-deploy.sh reads (pipelineChainIsRunning), so a restart doesn't
# drop the links a chain has yet to reach.
_chains = 0
_chains_lock = threading.Lock()


def chain_running() -> bool:
    with _chains_lock:
        return _chains > 0
# How often a link waiting on the lock, or on another run of its pipeline,
# looks again.
POLL_S = 30.0

# A link's outcome: its run's own status ("completed", "failed", "partial",
# "skipped" …, from the dict it returned), or one of these.
CRASHED = "crashed"  # it raised
RAN_ELSEWHERE = "ran elsewhere"  # another run of it was live: waited out, not repeated


@dataclass(frozen=True)
class Link:
    label: str
    run: Callable[[], Awaitable[dict]]
    # Its run-row model: how a run of it elsewhere is seen (run_tracker.live_run).
    model: type
    # After the link, whatever its outcome (a cache the link may have changed).
    after: Callable[[], None] | None = None


@dataclass(frozen=True)
class Outcome:
    status: str
    result: dict | None = None
    error: BaseException | None = None


def _running_elsewhere(model: type) -> bool:
    from app.database import SessionLocal
    from app.pipeline.run_tracker import run_in_progress

    db = SessionLocal()
    try:
        return run_in_progress(db, model)
    finally:
        db.close()


async def _wait_while(busy: Callable[[], bool], what: str) -> None:
    """Wait while `busy()` holds, at most STALE_PIPELINE_TIMEOUT."""
    deadline = time.monotonic() + STALE_PIPELINE_TIMEOUT.total_seconds()
    logged = False
    while busy():
        if time.monotonic() >= deadline:
            logger.warning("Still waiting on %s after %s — going on without it", what, STALE_PIPELINE_TIMEOUT)
            return
        if not logged:
            logger.info("Waiting on %s", what)
            logged = True
        await asyncio.sleep(POLL_S)


async def run_link(link: Link) -> Outcome:
    """One link: waited out if its pipeline is running elsewhere, else run
    under the chain lock. Never raises for the link's own failure (an
    Exception is its CRASHED outcome); cancellation and exit still end the
    chain."""
    try:
        elsewhere = await asyncio.to_thread(_running_elsewhere, link.model)
    except Exception:
        logger.warning("Could not tell whether %s is running elsewhere — running it", link.label, exc_info=True)
        elsewhere = False
    if elsewhere:
        await _wait_while(lambda: _running_elsewhere(link.model), f"the {link.label} run already under way")
        logger.info("%s ran elsewhere — not run again", link.label)
        return Outcome(RAN_ELSEWHERE)

    acquired = False
    deadline = time.monotonic() + STALE_PIPELINE_TIMEOUT.total_seconds()
    while not (acquired := _link_lock.acquire(blocking=False)):
        if time.monotonic() >= deadline:
            logger.warning("%s waited %s for the pipeline running before it — running it beside that one",
                           link.label, STALE_PIPELINE_TIMEOUT)
            break
        await asyncio.sleep(POLL_S)
    try:
        try:
            result = await link.run()
        except Exception as error:
            logger.exception("%s pipeline crashed", link.label)
            outcome = Outcome(CRASHED, error=error)
        else:
            result = result if isinstance(result, dict) else {}
            outcome = Outcome(str(result.get("status") or "completed"), result)
    finally:
        if acquired:
            _link_lock.release()
    if link.after is not None:
        try:
            link.after()
        except Exception:
            logger.exception("After %s: follow-up failed", link.label)
    return outcome


async def run_chain(
    links: list[Link], on_outcome: Callable[[Link, Outcome], None] | None = None,
) -> dict[str, Outcome]:
    """Each link in turn, whatever the one before it did."""
    global _chains
    outcomes: dict[str, Outcome] = {}
    with _chains_lock:
        _chains += 1
    try:
        for link in links:
            outcome = await run_link(link)
            outcomes[link.label] = outcome
            logger.info("%s pipeline: %s", link.label,
                        outcome.result if outcome.result is not None else outcome.status)
            if on_outcome is not None:
                try:
                    on_outcome(link, outcome)
                except Exception:
                    logger.exception("Reporting %s's outcome failed", link.label)
    finally:
        with _chains_lock:
            _chains -= 1
    return outcomes


def one_link(label: str, run: Callable[[], Awaitable[dict]], model: type) -> Callable[[], Awaitable[None]]:
    """A single pipeline as a chain of one — for a trigger — so it takes the
    chain lock like every other run in the process."""
    async def chain() -> None:
        await run_chain([Link(label, run, model)])

    return chain
