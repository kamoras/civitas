"""Running pipelines one after another, each independent of the last.

The nightly run is five pipelines in order — Senate, Supplementary, House,
Stock trades, Election — and a full trigger runs the same five. They run
one after another because the Pi can't hold two in memory at once, not
because one needs another's output from the same night: each reads
whatever the database holds, and yesterday's data is a fine input when
today's run of the one before it didn't happen. So a link that is skipped,
fails or crashes is reported and the next link runs anyway. (Until 2026-09
any of those ended the chain there: a Supplementary failure once left
House, Stock trades and Election unrun for 19 nights.)

One chain runs at a time (claim): a trigger is refused while one is going,
and a nightly run that finds one going leaves the night to it. A link
skipped because another run holds the machine — another run of the same
pipeline, or, for Stock trades, a member pipeline — would otherwise have
the chain move straight on and put two heavy pipelines side by side. So the
chain waits until no pipeline is running, then tries that link once more
and reports what that attempt did: the other run may have been a single
senator, or have failed, so it never stands in for this one. The wait ends
because what it waits on does — every "running" it reads stops counting
once its run is past STALE_PIPELINE_TIMEOUT (scheduler.pipelines_running),
and the retry's lock takes over a hung run's.

The one skip that ends a chain is a data reset holding the database: every
later link would be refused the same way. What this can't cover is the
process itself dying — a kill takes the rest of the chain with it;
ops_alerts.check_pipeline_staleness is the backstop for that.
"""

import asyncio
import itertools
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.pipeline.run_tracker import ALREADY_RUNNING, MEMBER_PIPELINE_RUNNING, STALE_PIPELINE_TIMEOUT

logger = logging.getLogger(__name__)

# A link's outcome: its run's own status ("completed", "failed", "partial",
# "no_data", "skipped" …, from the dict it returned), or CRASHED (it raised).
CRASHED = "crashed"
# Returned no status: a bug in the pipeline, alerted rather than taken for
# a success.
UNKNOWN = "unknown"
# Outcomes that are a lost run of the pipeline, alerted as such.
FAILED = frozenset({CRASHED, UNKNOWN, "failed", "no_data"})
# How often a link held off by another run looks whether it has finished.
WAIT_POLL_S = 30


@dataclass(frozen=True)
class Link:
    label: str
    run: Callable[[], Awaitable[dict]]
    # After the link, unless it was skipped (a cache the link may have
    # changed — a crash may have changed it partway).
    after: Callable[[], None] | None = None


@dataclass(frozen=True)
class Outcome:
    status: str
    result: dict | None = None
    error: BaseException | None = None


# Chains in progress, by id: the monotonic time of their last progress (a
# link starting or ending, or a look while waiting). What check-and-deploy.sh
# reads (pipelineChainIsRunning), so a restart between two links doesn't
# drop the rest. One with no progress for STALE_PIPELINE_TIMEOUT — one link
# that long — is wedged, not busy: reported busy it would hold every deploy
# off, the one that fixes it included, and it no longer holds the slot.
_chains: dict[int, float] = {}
_chains_lock = threading.Lock()
_ids = itertools.count(1)


def _any_live(now: float) -> bool:
    return any(now - progress < STALE_PIPELINE_TIMEOUT.total_seconds() for progress in _chains.values())


def chain_running() -> bool:
    now = time.monotonic()
    with _chains_lock:
        return _any_live(now)


def claim() -> int | None:
    """The chain slot: an id to run a chain under (run_chain), or None while
    another chain is running. Taken where the chain is asked for — a trigger
    claims before it answers — so two requests can't both find it free.
    Hand it back with release() if the chain never starts."""
    now = time.monotonic()
    with _chains_lock:
        if _any_live(now):
            return None
        chain_id = next(_ids)
        _chains[chain_id] = now
        return chain_id


def release(chain_id: int) -> None:
    with _chains_lock:
        _chains.pop(chain_id, None)


def _progress(chain_id: int) -> None:
    with _chains_lock:
        _chains[chain_id] = time.monotonic()


def _skip_reason(outcome: Outcome) -> str | None:
    return (outcome.result or {}).get("reason") if outcome.status == "skipped" else None


def ends_chain(outcome: Outcome) -> bool:
    """A data reset refused it: every later link would be refused too."""
    from app.pipeline import lease

    return _skip_reason(outcome) == lease.REFUSED_BY_RESET


def held_off(outcome: Outcome) -> bool:
    """Skipped because another run holds the machine: another run of the same
    pipeline (its run lock, ALREADY_RUNNING, or its job's lease,
    REFUSED_HELD), or a member pipeline holding Stock trades off."""
    from app.pipeline import lease

    return _skip_reason(outcome) in (ALREADY_RUNNING, lease.REFUSED_HELD, MEMBER_PIPELINE_RUNNING)


async def _wait_out(chain_id: int, busy: Callable[[], bool]) -> None:
    """Until busy() is false — each run it reads stops counting once past
    STALE_PIPELINE_TIMEOUT, so this ends. The chain's progress meanwhile:
    it is alive, only waiting."""
    while True:
        try:
            if not busy():
                return
        except Exception:
            # Can't tell: take it as free — the retry's own lock refuses it
            # if not.
            logger.exception("Checking for a running pipeline failed")
            return
        _progress(chain_id)
        await asyncio.sleep(WAIT_POLL_S)


async def _run_link(link: Link) -> Outcome:
    """One link and its follow-up. Never raises for the link's own failure
    (an Exception is its CRASHED outcome); cancellation and exit do."""
    try:
        result = await link.run()
    except Exception as error:
        logger.exception("%s pipeline crashed", link.label)
        outcome = Outcome(CRASHED, error=error)
    else:
        # Every pipeline says how it went; one that doesn't is not taken
        # for a success.
        result = result if isinstance(result, dict) else {}
        outcome = Outcome(str(result.get("status") or UNKNOWN), result)
    if link.after is not None and outcome.status != "skipped":
        try:
            link.after()
        except Exception:
            logger.exception("After %s: follow-up failed", link.label)
    return outcome


async def run_chain(
    links: list[Link], on_outcome: Callable[[Link, Outcome], None] | None = None,
    busy: Callable[[], bool] | None = None, chain_id: int | None = None,
) -> dict[str, Outcome] | None:
    """Each link in turn, whatever the one before it did, under `chain_id`
    (claimed already, or claimed here — None, and nothing runs, while
    another chain holds the slot). `busy`: whether any pipeline is running —
    what a link held off by another run waits on before its one retry
    (without it, the chain moves straight on)."""
    if chain_id is None:
        chain_id = claim()
        if chain_id is None:
            logger.info("Another pipeline chain is running — this one is not started")
            return None
    outcomes: dict[str, Outcome] = {}
    try:
        for link in links:
            _progress(chain_id)
            outcome = await _run_link(link)
            if busy is not None and held_off(outcome):
                logger.info("%s pipeline held off by another run — waiting it out, then trying again",
                            link.label)
                await _wait_out(chain_id, busy)
                _progress(chain_id)
                outcome = await _run_link(link)
            _progress(chain_id)
            outcomes[link.label] = outcome
            logger.info("%s pipeline: %s", link.label,
                        outcome.result if outcome.result is not None else outcome.status)
            if on_outcome is not None:
                try:
                    on_outcome(link, outcome)
                except Exception:
                    logger.exception("Reporting %s's outcome failed", link.label)
            if ends_chain(outcome):
                logger.warning("A data reset holds the database — the rest of this chain is not run")
                break
    finally:
        release(chain_id)
    return outcomes
